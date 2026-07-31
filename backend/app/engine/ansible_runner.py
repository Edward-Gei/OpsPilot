"""Ansible 步骤执行器：SSH 到作业主机运行 ansible-playbook（执行范式改造后）。

Ansible 步骤 = Shell 特例：SSH→作业主机→SFTP 上传渲染后的 playbook→
执行 `ansible-playbook -i localhost, -c local`→仅取退出码判定成败，
不解析 PLAY RECAP（不再有逐目标主机结果归档）。

工作目录：{job_host.workdir}/opspilot/exec_{eid}_step_{n}/，执行完 best-effort 清理。
"""
import asyncio
import logging
import shlex

import asyncssh

from app.core.constants import ExecutionStatus
from app.engine.control import ControlState
from app.engine.logs import LogChannel
from app.engine.ssh_runner import open_connection

logger = logging.getLogger("opspilot.engine.ansible")


async def run_ansible_on_job_host(
    *,
    job_host,
    playbook: str,
    timeout: int,
    step_order: int,
    log: LogChannel,
    control: ControlState,
    execution_id: int,
) -> tuple[str, int | None, str | None]:
    """SSH→作业主机→SFTP 上传 playbook→执行 ansible-playbook→仅取退出码。

    返回 (ExecutionStatus值, 退出码, 失败摘要)。
    """
    workdir = (job_host.workdir or "/tmp").rstrip("/")
    run_dir = f"{workdir}/opspilot/exec_{execution_id}_step_{step_order}"
    conn: asyncssh.SSHClientConnection | None = None
    try:
        conn = await open_connection(job_host.ip, job_host.ssh_port, job_host)
        control.register_conn(conn)
        await conn.run(f"mkdir -p {shlex.quote(run_dir)}", check=True)
        async with conn.start_sftp_client() as sftp:
            async with sftp.open(f"{run_dir}/playbook.yml", "w") as f:
                await f.write(playbook)
        # 本机连接执行（Shell 特例口径）：输出流式回传，仅退出码判定成败
        cmd = (
            f"cd {shlex.quote(run_dir)} && "
            "ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_FORCE_COLOR=0 "
            "ansible-playbook -i localhost, -c local playbook.yml 2>&1"
        )
        async with conn.create_process(cmd) as process:

            async def _collect() -> None:
                async for line in process.stdout:
                    log.write(step_order, line.rstrip())

            await asyncio.wait_for(asyncio.gather(_collect(), process.wait()), timeout=timeout)
        exit_code = process.exit_status
        if exit_code == 0:
            return ExecutionStatus.SUCCESS.value, 0, None
        summary = f"ansible-playbook 退出码 {exit_code}"
        log.write(step_order, f"[opspilot] {summary}")
        return ExecutionStatus.FAILED.value, exit_code, summary
    except asyncio.TimeoutError:
        if conn is not None:
            conn.abort()
        log.write(step_order, f"[opspilot] ansible-playbook 超时（>{timeout}s），已终止会话")
        # 无独立 TIMEOUT 态，按 failed 归档但摘要明确标注超时（与 ssh_runner 口径一致）
        return ExecutionStatus.FAILED.value, None, f"执行超时：ansible-playbook 超过 {timeout}s 未完成，会话已终止"
    except RuntimeError as exc:
        # 凭据解密失败等可识别运行时错误（decrypt_text）：按步骤失败归档，
        # 避免裸异常击穿引擎被误判为系统崩溃（system_crash）
        summary = str(exc)[:500] or "运行时错误"
        log.write(step_order, f"[opspilot] {summary}")
        return ExecutionStatus.FAILED.value, None, summary
    except (asyncssh.Error, OSError) as exc:
        if control.force_abort:
            log.write(step_order, "[opspilot] 会话已被强制中止")
            return ExecutionStatus.TERMINATED.value, None, "强制中止"
        summary = f"作业主机异常: {exc}"[:500]
        log.write(step_order, f"[opspilot] {summary}")
        return ExecutionStatus.FAILED.value, None, summary
    finally:
        if conn is not None:
            control.unregister_conn(conn)
            # best-effort 清理工作目录（连接可能已被强杀，忽略失败）
            try:
                await conn.run(f"rm -rf {shlex.quote(run_dir)}", check=False)
            except Exception:  # noqa: BLE001
                pass
            conn.close()
