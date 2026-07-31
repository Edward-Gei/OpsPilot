"""凭据业务服务：CRUD + AES-256-GCM 密文管理 + 引用保护。

安全约束（PRD CRED-01/02）：
- secret / passphrase 入库前经 core/security.encrypt_text 加密；
- 任何查询接口不返回密文字段，明文仅执行引擎（M5）解密使用；
- 凭据被作业主机 / 模板引用时删除保护（42201）。
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.core.security import encrypt_text
from app.models.cmdb import JobHost
from app.models.job import Credential, TicketTemplate


async def get_credential_or_404(session: AsyncSession, credential_id: int) -> Credential:
    """按 ID 取凭据，不存在抛 40401。"""
    cred = await session.get(Credential, credential_id)
    if cred is None:
        raise Errors.not_found("凭据不存在")
    return cred


async def list_credentials(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    auth_type: str | None = None,
) -> tuple[list[Credential], int]:
    """分页查凭据；keyword 模糊匹配名称/登录用户（响应序列化不含密文）。"""
    query = select(Credential)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(Credential.name.like(like) | Credential.login_user.like(like))
    if auth_type:
        query = query.where(Credential.auth_type == auth_type)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(
        query.order_by(Credential.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def _ensure_name_unique(session: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    """凭据名唯一性校验，冲突抛 40901。"""
    query = select(Credential.id).where(Credential.name == name)
    if exclude_id is not None:
        query = query.where(Credential.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.conflict(f"凭据名 {name} 已存在")


async def create_credential(
    session: AsyncSession,
    *,
    created_by: int,
    name: str,
    login_user: str,
    auth_type: str,
    secret: str,
    passphrase: str | None,
    description: str | None,
) -> Credential:
    """新建凭据：secret/passphrase 加密后入库。"""
    await _ensure_name_unique(session, name)
    cred = Credential(
        name=name,
        login_user=login_user,
        auth_type=auth_type,
        secret_enc=encrypt_text(secret),
        passphrase_enc=encrypt_text(passphrase) if passphrase else None,
        description=description,
        created_by=created_by,
    )
    session.add(cred)
    await session.flush()
    return cred


async def update_credential(
    session: AsyncSession,
    credential_id: int,
    *,
    name: str,
    login_user: str,
    auth_type: str,
    secret: str,
    passphrase: str | None,
    description: str | None,
) -> tuple[Credential, bool]:
    """编辑凭据；secret 传空 = 保留原密文。返回 (凭据, 是否更新了密文)。

    防御：认证方式变更时旧密文语义已失效（密码↔私钥），必须同时提供新 secret。
    """
    cred = await get_credential_or_404(session, credential_id)
    if name != cred.name:
        await _ensure_name_unique(session, name, exclude_id=credential_id)
    if auth_type != cred.auth_type and not secret:
        raise Errors.param("变更认证方式时必须重新填写密文内容")
    secret_changed = bool(secret)
    cred.name = name
    cred.login_user = login_user
    cred.auth_type = auth_type
    cred.description = description
    if secret:
        cred.secret_enc = encrypt_text(secret)
        # 密文更新时口令一并按本次入参覆盖（不传即清空，与新密钥配套）
        cred.passphrase_enc = encrypt_text(passphrase) if passphrase else None
    elif passphrase:
        # 仅补充/修改私钥口令
        cred.passphrase_enc = encrypt_text(passphrase)
    await session.flush()
    return cred, secret_changed


async def delete_credential(session: AsyncSession, credential_id: int) -> Credential:
    """删除凭据；被作业主机或模板引用时拒绝（42201）。"""
    cred = await get_credential_or_404(session, credential_id)
    jh_count = (
        await session.execute(
            select(func.count()).select_from(JobHost)
            .where(JobHost.credential_id == credential_id)
        )
    ).scalar_one()
    if jh_count:
        raise Errors.rejected(f"凭据被 {jh_count} 台作业主机引用，无法删除")
    # credential_refs 为 JSON 数组；模板量小，取非空引用后在 Python 侧匹配
    # credential_id（兼容 MySQL/SQLite，避免方言专属的 JSON_CONTAINS）
    refs_rows = (
        await session.execute(
            select(TicketTemplate.credential_refs)
            .where(TicketTemplate.credential_refs.is_not(None))
        )
    ).scalars()
    tpl_count = sum(
        1 for refs in refs_rows
        if any(ref.get("credential_id") == credential_id for ref in (refs or []))
    )
    if tpl_count:
        raise Errors.rejected(f"凭据被 {tpl_count} 个工单模板引用，无法删除")
    await session.delete(cred)
    await session.flush()
    return cred
