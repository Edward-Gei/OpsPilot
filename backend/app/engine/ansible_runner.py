"""Ansible 步骤执行器：SSH 到作业主机运行 ansible-playbook（02-技术架构 §4.2/§4.4）。

作业主机 = Ansible 控制节点（全局单实例，系统配置 ansible.job_host），
≠ CMDB 目标主机。执行流程：
    1. 读作业主机配置 {ip, port, credential_id, workdir} 并建立 SSH 会话
    2. 生成 inventory（目标主机 + 步骤凭据）与渲染后的 playbook，SFTP 上传到
       {workdir}/opspilot/exec_{eid}_step_{n}/（含私钥文件 0600）
    3. 执行 ansible-playbook，输出流式写入日志通道（伪主机名 "ansible"）
    4. 解析 PLAY RECAP 得到逐目标主机结果；结束后 best-effort 清理工作目录

安全注意：inventory 内含目标机凭据明文（密码或私钥路径），目录权限 0700、
文件 0600，执行完立即删除；作业主机自身凭据走凭据中心 AES-256-GCM 解密。
"""
import asyncio
import logging
import re
import shlex

import asyncssh

from app.core.constants import CredentialAuthType, HostExecStatus
from app.core.security import decrypt_text
from app.engine.control import ControlState
from app.engine.logs import ANSIBLE_LOG_IP, LogChannel
from app.engine.ssh_runner import open_connection

logger = logging.getLogger("opspilot.engine.ansible")

# PLAY RECAP 行格式：host : ok=3 changed=1 unreachable=0 failed=0 skipped=0 ...
_RECAP_LINE = re.compile(
    r"^(?P<host>\S+)\s*:\s*(?P<stats>(?:\w+=\d+\s*)+)$"
)


class JobHostError(Exception):
    """作业主机不可用（未配置 / 连接失败），整步骤按失败处理。"""


def parse_play_recap(output: str) -> dict[str, dict[str, int]]:
    """解析 ansible-playbook 输出中的 PLAY RECAP 段，返回 {host: {ok/failed/unreachable/...}}。

    仅解析 "PLAY RECAP" 标记之后的行；无 RECAP（如 playbook 语法错误提前退出）
    返回空 dict，由调用方按整体失败处理。
    """
    result: dict[str, dict[str, int]] = {}
    in_recap = False
    for raw in output.splitlines():
        line = raw.strip()
        if "PLAY RECAP" in line:
            in_recap = True
            continue
        if not in_recap or not line:
            continue
        m = _RECAP_LINE.match(line)
        if not m:
            continue
        stats = {
            k: int(v)
            for k, v in (pair.split("=") for pair in m.group("stats").split())
        }
        result[m.group("host")] = stats
    return result


def recap_host_status(stats: dict[str, int]) -> tuple[str, str | None]:
    """RECAP 单主机统计 → (HostExecStatus, 失败摘要)。"""
    if stats.get("unreachable", 0) > 0:
        return HostExecStatus.FAILED.value, "主机不可达（unreachable）"
    if stats.get("failed", 0) > 0:
        return HostExecStatus.FAILED.value, f"任务失败 failed={stats['failed']}"
    return HostExecStatus.SUCCESS.value, None


def build_inventory(hosts: list, credential) -> str:
    """生成 inventory.ini：目标主机以 IP 为 inventory 名（与 RECAP 主机名对齐）。

    密码凭据用 ansible_password；私钥凭据引用同目录 key.pem（上传时 0600）。
    """
    lines = ["[targets]"]
    for h in hosts:
        parts = [
            h.ip,
            f"ansible_host={h.ip}",
            f"ansible_port={h.ssh_port}",
            f"ansible_user={credential.login_user}",
        ]
        if credential.auth_type == CredentialAuthType.PASSWORD.value:
            secret = decrypt_text(credential.secret_enc)
            parts.append(f"ansible_password={secret}")
        else:
            parts.append("ansible_ssh_private_key_file=./key.pem")
        lines.append(" ".join(parts))
    lines += [
        "",
        "[targets:vars]",
        # 内网批量执行不做 host key 校验（与 Shell 执行器口径一致）
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'",
    ]
    return "\n".join(lines) + "\n"


async def _upload_text(sftp, remote_path: str, content: str, mode: int = 0o600) -> None:
    """SFTP 写文本文件并设置权限（凭据类文件必须 0600）。"""
    async with sftp.open(remote_path, "w") as f:
        await f.write(content)
    await sftp.chmod(remote_path, mode)


async def run_playbook_step(
    *,
    job_host_cfg: dict,
    job_host_credential,
    step_credential,
    hosts: list,
    playbook_content: str,
    timeout: int,
    execution_id: int,
    step_order: int,
    log: LogChannel,
    control: ControlState,
) -> dict[str, tuple[str, str | None]]:
    """在作业主机执行一次 playbook（一个批次的目标主机），返回 {ip: (状态, 摘要)}。

    整体超时 = 步骤 timeout；超时/强杀/RECAP 缺失时批内主机统一按对应状态处理。
    """
    workdir = (job_host_cfg.get("workdir") or "/tmp").rstrip("/")
    run_dir = f"{workdir}/opspilot/exec_{execution_id}_step_{step_order}"
    conn: asyncssh.SSHClientConnection | None = None
    output_parts: list[str] = []
    try:
        conn = await open_connection(
            job_host_cfg["ip"], int(job_host_cfg.get("port") or 22), job_host_credential
        )
        control.register_conn(conn)
        # 准备工作目录（0700：目录内含目标机凭据）
        await conn.run(f"mkdir -p {shlex.quote(run_dir)} && chmod 700 {shlex.quote(run_dir)}", check=True)
        async with conn.start_sftp_client() as sftp:
            await _upload_text(sftp, f"{run_dir}/inventory.ini", build_inventory(hosts, step_credential))
            await _upload_text(sftp, f"{run_dir}/playbook.yml", playbook_content)
            if step_credential.auth_type == CredentialAuthType.PRIVATE_KEY.value:
                await _upload_text(sftp, f"{run_dir}/key.pem", decrypt_text(step_credential.secret_enc))
        # 执行 playbook：关 host key 校验；输出流式回传
        cmd = (
            f"cd {shlex.quote(run_dir)} && "
            "ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_FORCE_COLOR=0 "
            "ansible-playbook -i inventory.ini playbook.yml 2>&1"
        )
        async with conn.create_process(cmd) as process:

            async def _collect() -> None:
                # 输出同时进日志通道与内存缓冲（RECAP 解析用）
                async for line in process.stdout:
                    text = line.rstrip()
                    output_parts.append(text)
                    log.write(step_order, ANSIBLE_LOG_IP, text)

            await asyncio.wait_for(asyncio.gather(_collect(), process.wait()), timeout=timeout)
        recap = parse_play_recap("\n".join(output_parts))
        if not recap:
            # playbook 未产出 RECAP：语法错误 / inventory 异常等，整批失败
            log.write(step_order, ANSIBLE_LOG_IP, "[opspilot] 未解析到 PLAY RECAP，整批按失败处理")
            return {h.ip: (HostExecStatus.FAILED.value, "playbook 未产出 RECAP") for h in hosts}
        results: dict[str, tuple[str, str | None]] = {}
        for h in hosts:
            stats = recap.get(h.ip)
            if stats is None:
                results[h.ip] = (HostExecStatus.FAILED.value, "RECAP 中无该主机结果")
            else:
                results[h.ip] = recap_host_status(stats)
        return results
    except asyncio.TimeoutError:
        if conn is not None:
            conn.abort()
        log.write(step_order, ANSIBLE_LOG_IP, f"[opspilot] playbook 执行超时（>{timeout}s），已终止会话")
        return {h.ip: (HostExecStatus.TIMEOUT.value, f"执行超时 >{timeout}s") for h in hosts}
    except (asyncssh.Error, OSError) as exc:
        if control.force_abort:
            log.write(step_order, ANSIBLE_LOG_IP, "[opspilot] 作业主机会话已被强制中止")
            return {h.ip: (HostExecStatus.TERMINATED.value, "强制中止") for h in hosts}
        summary = f"作业主机异常: {exc}"[:500]
        log.write(step_order, ANSIBLE_LOG_IP, f"[opspilot] {summary}")
        return {h.ip: (HostExecStatus.FAILED.value, summary) for h in hosts}
    finally:
        if conn is not None:
            control.unregister_conn(conn)
            # best-effort 清理含凭据的工作目录（连接可能已被强杀，忽略失败）
            try:
                await conn.run(f"rm -rf {shlex.quote(run_dir)}", check=False)
            except Exception:  # noqa: BLE001
                pass
            conn.close()


async def test_job_host(job_host_cfg: dict, credential) -> str:
    """作业主机连通性测试：SSH 建连 + `ansible --version` 探测（04-API §11）。

    成功返回 ansible 版本首行；失败抛 JobHostError（含原因）。
    """
    try:
        conn = await open_connection(
            job_host_cfg["ip"], int(job_host_cfg.get("port") or 22), credential
        )
    except (asyncssh.Error, OSError) as exc:
        raise JobHostError(f"SSH 连接失败: {exc}") from exc
    try:
        result = await asyncio.wait_for(conn.run("ansible --version", check=False), timeout=15)
        if result.exit_status != 0:
            raise JobHostError("作业主机未安装 ansible 或不在 PATH 中")
        first_line = (result.stdout or "").strip().splitlines()
        return first_line[0] if first_line else "ansible"
    except asyncio.TimeoutError as exc:
        raise JobHostError("ansible --version 执行超时") from exc
    finally:
        conn.close()
