"""Shell 步骤执行器：asyncssh 直连目标主机执行脚本（02-技术架构 §4.2）。

执行方式：`bash -s` 读 stdin，脚本内容（参数已渲染）经 stdin 注入，
避免在目标机落临时文件；stdout/stderr 逐行流式写入日志通道。

结果状态（HostExecStatus 子集）：
    success    退出码 0
    failed     退出码非 0 / 连接失败
    timeout    超过步骤 timeout（asyncio.wait_for）
    terminated force-abort 掐断会话
"""
import asyncio
import logging

import asyncssh

from app.core.constants import CredentialAuthType, ExecutionStatus, HostExecStatus
from app.core.security import decrypt_text
from app.engine.control import ControlState
from app.engine.logs import LogChannel

logger = logging.getLogger("opspilot.engine.ssh")

# SSH 建连超时（秒），独立于步骤执行超时
_CONNECT_TIMEOUT = 10


async def open_connection(
    ip: str, port: int, credential,
) -> asyncssh.SSHClientConnection:
    """按凭据类型建立 SSH 连接（密码 / 私钥+口令），密文即用即解不留明文变量。"""
    kwargs: dict = {
        "host": ip,
        "port": port,
        "username": credential.login_user,
        "known_hosts": None,          # 内网批量执行不做 host key 校验（V1 决策）
        "connect_timeout": _CONNECT_TIMEOUT,
    }
    if credential.auth_type == CredentialAuthType.PASSWORD.value:
        kwargs["password"] = decrypt_text(credential.secret_enc)
    else:
        passphrase = (
            decrypt_text(credential.passphrase_enc) if credential.passphrase_enc else None
        )
        key = asyncssh.import_private_key(decrypt_text(credential.secret_enc), passphrase)
        kwargs["client_keys"] = [key]
    return await asyncssh.connect(**kwargs)


def _env_prelude(env: dict[str, str]) -> str:
    """凭据 env 前置 export 段：单引号包裹 + `'` 转义为 `'\\''`。

    经 stdin 写入 bash -s，不落盘、不进执行日志；用户脚本内的 set -x
    在其后才生效，追踪不到这些 export 行。
    """
    lines = []
    for key, value in env.items():
        safe = value.replace("'", "'\\''")
        lines.append(f"export {key}='{safe}'")
    return "\n".join(lines)


async def _stream_output(stream, log: LogChannel, step_order: int, prefix: str = "") -> None:
    """逐行读取远端输出写入日志通道（stdout/stderr 各起一个协程）。"""
    async for line in stream:
        log.write(step_order, f"{prefix}{line.rstrip()}" if prefix else line.rstrip())


async def run_shell_on_host(
    *,
    ip: str,
    port: int,
    credential,
    script: str,
    timeout: int,
    step_order: int,
    log: LogChannel,
    control: ControlState,
) -> tuple[str, int | None, str | None]:
    """在单台目标主机执行 Shell 脚本，返回 (状态, 退出码, 失败摘要)。

    - 连接注册到 ControlState，force-abort 时被 watcher 强杀；
    - 超时主动 conn.abort() 掐断，状态置 timeout；
    - 会话被强杀（force_abort 标志已置位）时状态置 terminated。
    """
    conn: asyncssh.SSHClientConnection | None = None
    try:
        conn = await open_connection(ip, port, credential)
        control.register_conn(conn)
        async with conn.create_process("bash -s") as process:
            process.stdin.write(script + "\n")
            process.stdin.write_eof()
            # stdout/stderr 并发流式收集；整体受步骤超时约束
            await asyncio.wait_for(
                asyncio.gather(
                    _stream_output(process.stdout, log, step_order),
                    _stream_output(process.stderr, log, step_order),
                    process.wait(),
                ),
                timeout=timeout,
            )
            exit_code = process.exit_status
        if exit_code == 0:
            return HostExecStatus.SUCCESS.value, 0, None
        summary = f"退出码 {exit_code}"
        log.write(step_order, f"[opspilot] 脚本执行失败：{summary}")
        return HostExecStatus.FAILED.value, exit_code, summary
    except asyncio.TimeoutError:
        # 超时：掐断连接终止远端会话，按失败策略参与统计（02 §4.2）
        if conn is not None:
            conn.abort()
        log.write(step_order, f"[opspilot] 执行超时（>{timeout}s），已终止会话")
        return HostExecStatus.TIMEOUT.value, None, f"执行超时 >{timeout}s"
    except RuntimeError as exc:
        # 凭据解密失败等可识别运行时错误（decrypt_text）：按失败归档，
        # 避免裸异常击穿引擎被误判为系统崩溃
        summary = str(exc)[:500] or "运行时错误"
        log.write(step_order, f"[opspilot] {summary}")
        return HostExecStatus.FAILED.value, None, summary
    except (asyncssh.Error, OSError) as exc:
        if control.force_abort:
            # 会话被 force-abort 强杀导致的连接异常
            log.write(step_order, "[opspilot] 会话已被强制中止")
            return HostExecStatus.TERMINATED.value, None, "强制中止"
        summary = f"SSH 异常: {exc}" if str(exc) else f"SSH 异常: {type(exc).__name__}"
        log.write(step_order, f"[opspilot] {summary}")
        return HostExecStatus.FAILED.value, None, summary[:500]
    finally:
        if conn is not None:
            control.unregister_conn(conn)
            conn.close()


async def run_shell_on_job_host(
    *,
    job_host,
    credential,
    env: dict[str, str] | None = None,
    script: str,
    timeout: int,
    step_order: int,
    log: LogChannel,
    control: ControlState,
) -> tuple[str, int | None, str | None]:
    """SSH→作业主机执行 Shell 脚本，返回 (ExecutionStatus值, 退出码, 失败摘要)。

    登录认证来自作业主机关联的凭据（credential 形参）；env 为模板引用凭据的
    环境变量，经 stdin 前置 export 注入（不落盘不进日志）。

    超时口径：ExecutionStatus 无独立 TIMEOUT 态，超时归档为 failed，
    但 error_summary 明确标注“执行超时”以区分普通失败（与旧版
    run_shell_on_host 的 HostExecStatus.TIMEOUT 语义对齐）。
    """
    conn: asyncssh.SSHClientConnection | None = None
    try:
        conn = await open_connection(job_host.ip, job_host.ssh_port, credential)
        control.register_conn(conn)
        async with conn.create_process("bash -s") as process:
            if env:
                process.stdin.write(_env_prelude(env) + "\n")
            process.stdin.write(script + "\n")
            process.stdin.write_eof()
            # stdout/stderr 并发流式收集；整体受步骤超时约束
            await asyncio.wait_for(
                asyncio.gather(
                    _stream_output(process.stdout, log, step_order),
                    _stream_output(process.stderr, log, step_order),
                    process.wait(),
                ),
                timeout=timeout,
            )
            exit_code = process.exit_status
        if exit_code == 0:
            return ExecutionStatus.SUCCESS.value, 0, None
        summary = f"退出码 {exit_code}"
        log.write(step_order, f"[opspilot] 脚本执行失败：{summary}")
        return ExecutionStatus.FAILED.value, exit_code, summary
    except asyncio.TimeoutError:
        # 超时：掐断连接终止远端会话；无独立 TIMEOUT 态，按 failed 归档
        # 但摘要明确标注超时，保留“超时”语义供运维区分失败原因
        if conn is not None:
            conn.abort()
        log.write(step_order, f"[opspilot] 执行超时（>{timeout}s），已终止会话")
        return ExecutionStatus.FAILED.value, None, f"执行超时：SSH 命令超过 {timeout}s 未完成，会话已终止"
    except RuntimeError as exc:
        # 凭据解密失败等可识别运行时错误（decrypt_text）：按步骤失败归档，
        # 避免裸异常击穿引擎被误判为系统崩溃（system_crash）
        summary = str(exc)[:500] or "运行时错误"
        log.write(step_order, f"[opspilot] {summary}")
        return ExecutionStatus.FAILED.value, None, summary
    except (asyncssh.Error, OSError) as exc:
        if control.force_abort:
            # 会话被 force-abort 强杀导致的连接异常
            log.write(step_order, "[opspilot] 会话已被强制中止")
            return ExecutionStatus.TERMINATED.value, None, "强制中止"
        summary = f"SSH 异常: {exc}" if str(exc) else f"SSH 异常: {type(exc).__name__}"
        log.write(step_order, f"[opspilot] {summary}")
        return ExecutionStatus.FAILED.value, None, summary[:500]
    finally:
        if conn is not None:
            control.unregister_conn(conn)
            conn.close()
