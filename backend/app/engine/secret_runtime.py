"""Worker 内部脚本密钥运行时：解密、环境变量、文本文件和精确脱敏。"""

import secrets
import shlex
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import CredentialAuthType
from app.core.response import Errors
from app.core.security import decrypt_text
from app.models.job import Credential
from app.services.credential_service import is_script_secret


@dataclass(frozen=True)
class SecretFile:
    """待写入作业主机的文本密钥文件，不落本地磁盘或日志。"""

    env_key: str
    file_name: str
    content: str


@dataclass
class SecretRuntime:
    """Shell 和动态参数脚本共用的注入载荷。"""

    env: dict[str, str]
    files: list[SecretFile]
    _values: tuple[str, ...]

    def redact(self, text: str | None) -> str | None:
        """按值长度倒序精确替换，避免短密钥破坏长密钥匹配。"""
        if text is None:
            return None
        result = text
        for value in self._values:
            result = result.replace(value, "***")
        return result

    def env_with_file_paths(self, remote_dir: str) -> dict[str, str]:
        return {
            **self.env,
            **{item.env_key: f"{remote_dir}/{item.file_name}" for item in self.files},
        }


def build_secret_runtime(refs: list[dict], credentials: Mapping[int, Credential]) -> SecretRuntime:
    """根据已校验引用构造注入数据，密钥缺失或类型漂移均拒绝执行。"""
    env: dict[str, str] = {}
    files: list[SecretFile] = []
    values: list[str] = []
    for ref in refs:
        credential_id = ref.get("credential_id")
        alias = ref.get("alias")
        credential = credentials.get(credential_id)
        if credential is None or not isinstance(alias, str) or not is_script_secret(credential):
            raise Errors.rejected("脚本密钥凭据不可用，请更新工单模板后重新提交")
        prefix = f"SECRET_{alias}"
        secret = decrypt_text(credential.secret_enc)
        if credential.auth_type == CredentialAuthType.API_TOKEN.value:
            env[prefix] = secret
            values.append(secret)
        elif credential.auth_type == CredentialAuthType.USERNAME_PASSWORD.value:
            if not credential.login_user:
                raise Errors.rejected("脚本密钥凭据配置不完整")
            env[f"{prefix}_USERNAME"] = credential.login_user
            env[f"{prefix}_PASSWORD"] = secret
            values.extend((credential.login_user, secret))
        elif credential.auth_type == CredentialAuthType.SECRET_FILE.value:
            if not credential.file_name:
                raise Errors.rejected("文本密钥文件配置不完整")
            files.append(SecretFile(prefix, credential.file_name, secret))
            values.append(secret)
        else:
            raise Errors.rejected("脚本密钥凭据类型不支持")
    return SecretRuntime(env=env, files=files, _values=tuple(sorted({value for value in values if value}, key=len, reverse=True)))


async def load_secret_runtime(session: AsyncSession, refs: list[dict]) -> SecretRuntime:
    """运行时按工单快照 ID 读取最新密文，不将密文写入数据库快照。"""
    credential_ids = [item.get("credential_id") for item in refs if isinstance(item.get("credential_id"), int)]
    if not credential_ids:
        return SecretRuntime(env={}, files=[], _values=())
    rows = list((await session.execute(select(Credential).where(Credential.id.in_(credential_ids)))).scalars())
    return build_secret_runtime(refs, {credential.id: credential for credential in rows})


async def materialize_secret_files(connection, runtime: SecretRuntime) -> tuple[str | None, dict[str, str]]:
    """在远端随机目录写入 0600 文本密钥文件，并返回包含文件路径的环境变量。"""
    if not runtime.files:
        return None, runtime.env
    remote_dir = f"/tmp/opspilot-secret-{secrets.token_urlsafe(16)}"
    await connection.run(f"mkdir -m 700 {shlex.quote(remote_dir)}", check=True)
    try:
        async with connection.start_sftp_client() as sftp:
            for item in runtime.files:
                remote_path = f"{remote_dir}/{item.file_name}"
                async with sftp.open(remote_path, "w") as remote_file:
                    await remote_file.write(item.content)
                await sftp.chmod(remote_path, 0o600)
    except Exception:
        await cleanup_secret_files(connection, remote_dir)
        raise
    return remote_dir, runtime.env_with_file_paths(remote_dir)


async def cleanup_secret_files(connection, remote_dir: str | None) -> None:
    """清理远端密钥目录；调用方的 finally 保证超时和异常路径也执行。"""
    if not remote_dir:
        return
    try:
        await connection.run(f"rm -rf {shlex.quote(remote_dir)}", check=False)
    except Exception:  # noqa: BLE001 连接中断时无法清理，目录仅含随机临时文件
        pass
