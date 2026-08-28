"""作业主机业务服务：CRUD + 连通性测试 + 启用/禁用（执行范式改造）。

安全约束：
- 登录认证引用凭据管理（credential_id），本模块不再持有任何密文字段；
- 删除保护：被工单模板（ticket_template.job_host_id）引用时 42201。
"""
import asyncio
from datetime import datetime

import asyncssh
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.engine.ssh_runner import open_connection
from app.models.cmdb import JobHost
from app.models.job import Credential, TicketTemplate
from app.services.credential_service import SSH_CREDENTIAL_TYPES

# 连通性测试整体超时（秒）：建连 + 执行 echo ok
_TEST_TIMEOUT = 10


async def get_job_host(session: AsyncSession, job_host_id: int) -> JobHost:
    """按 ID 取作业主机，不存在抛 40401。"""
    jh = await session.get(JobHost, job_host_id)
    if jh is None:
        raise Errors.not_found("作业主机不存在")
    return jh


async def list_job_hosts(
    session: AsyncSession,
    *,
    keyword: str | None = None,
    enabled: bool | None = None,
    page: int,
    page_size: int,
) -> tuple[list[JobHost], int]:
    """分页查作业主机；keyword 模糊匹配名称/IP，enabled 筛选启用状态。"""
    query = select(JobHost)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(JobHost.name.like(like) | JobHost.ip.like(like))
    if enabled is not None:
        query = query.where(JobHost.enabled == enabled)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(
        query.order_by(JobHost.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def _ensure_name_unique(session: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    """作业主机名唯一性校验（DB 唯一约束），冲突抛 40901。"""
    query = select(JobHost.id).where(JobHost.name == name)
    if exclude_id is not None:
        query = query.where(JobHost.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.conflict(f"作业主机名 {name} 已存在")


async def _ensure_ip_port_unique(
    session: AsyncSession, ip: str, ssh_port: int, exclude_id: int | None = None
) -> None:
    """IP+端口唯一性校验（uk_job_host_ip_port），冲突抛 42201。"""
    query = select(JobHost.id).where(JobHost.ip == ip, JobHost.ssh_port == ssh_port)
    if exclude_id is not None:
        query = query.where(JobHost.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.rejected(f"作业主机 {ip}:{ssh_port} 已存在")


async def _ensure_credential_exists(session: AsyncSession, credential_id: int) -> None:
    """作业主机只能关联 SSH 登录凭据，脚本密钥不具备主机认证语义。"""
    credential = await session.get(Credential, credential_id)
    if credential is None:
        raise Errors.not_found("关联凭据不存在")
    if credential.auth_type not in SSH_CREDENTIAL_TYPES:
        raise Errors.param("作业主机只能关联 SSH 凭据")


async def credential_names(
    session: AsyncSession, credential_ids: list[int | None]
) -> dict[int, str]:
    """批量取凭据 id→名称映射（列表/详情序列化补充 credential_name 用）。"""
    ids = {i for i in credential_ids if i}
    if not ids:
        return {}
    rows = await session.execute(
        select(Credential.id, Credential.name).where(Credential.id.in_(ids))
    )
    return dict(rows.all())


async def create_job_host(
    session: AsyncSession, *, data: dict, created_by: int | None
) -> JobHost:
    """新增作业主机；登录认证引用凭据（credential_id 已在 schema 层必填）。"""
    await _ensure_name_unique(session, data["name"])
    await _ensure_ip_port_unique(session, data["ip"], data["ssh_port"])
    await _ensure_credential_exists(session, data["credential_id"])
    jh = JobHost(**data, created_by=created_by)
    session.add(jh)
    await session.flush()
    return jh


async def update_job_host(session: AsyncSession, job_host_id: int, *, data: dict) -> JobHost:
    """编辑作业主机；仅更新 data 中出现的字段，IP+端口唯一性校验排除自身。"""
    jh = await get_job_host(session, job_host_id)
    new_name = data.get("name")
    if new_name and new_name != jh.name:
        await _ensure_name_unique(session, new_name, exclude_id=job_host_id)
    # IP/端口任一变更时按变更后的组合校验唯一性
    new_ip = data.get("ip", jh.ip)
    new_port = data.get("ssh_port", jh.ssh_port)
    if new_ip != jh.ip or new_port != jh.ssh_port:
        await _ensure_ip_port_unique(session, new_ip, new_port, exclude_id=job_host_id)
    if data.get("credential_id") and data["credential_id"] != jh.credential_id:
        await _ensure_credential_exists(session, data["credential_id"])
    for field, value in data.items():
        setattr(jh, field, value)
    await session.flush()
    return jh


async def delete_job_host(session: AsyncSession, job_host_id: int) -> JobHost:
    """删除作业主机；被工单模板引用时拒绝（42201）。"""
    jh = await get_job_host(session, job_host_id)
    tpl_count = (
        await session.execute(
            select(func.count()).select_from(TicketTemplate)
            .where(TicketTemplate.job_host_id == job_host_id)
        )
    ).scalar_one()
    if tpl_count:
        raise Errors.rejected(f"作业主机被 {tpl_count} 个工单模板引用，无法删除")
    await session.delete(jh)
    await session.flush()
    return jh


async def test_connectivity(session: AsyncSession, job_host_id: int) -> dict:
    """连通性测试：按关联凭据 SSH 连接作业主机执行 echo ok，结果落 last_check_* 字段。"""
    jh = await get_job_host(session, job_host_id)
    cred = await session.get(Credential, jh.credential_id) if jh.credential_id else None
    if cred is None:
        checked_at = datetime.now()
        jh.last_check_at = checked_at
        jh.last_check_ok = False
        jh.last_check_msg = "未关联凭据，请先在凭据管理中创建并关联"
        await session.flush()
        return {"ok": False, "message": jh.last_check_msg, "checked_at": checked_at}
    ok, message = False, ""
    conn: asyncssh.SSHClientConnection | None = None
    try:
        async def _probe() -> tuple[bool, str]:
            nonlocal conn
            conn = await open_connection(jh.ip, jh.ssh_port, cred)
            result = await conn.run("echo ok")
            if result.exit_status == 0 and "ok" in str(result.stdout):
                return True, "连接成功"
            return False, f"echo ok 执行异常，退出码 {result.exit_status}"

        ok, message = await asyncio.wait_for(_probe(), timeout=_TEST_TIMEOUT)
    except asyncio.TimeoutError:
        message = f"连接超时（>{_TEST_TIMEOUT}s）"
    except (asyncssh.Error, OSError) as exc:
        message = f"SSH 异常: {exc}" if str(exc) else f"SSH 异常: {type(exc).__name__}"
    except Exception as exc:  # noqa: BLE001 密文解密失败等配置类异常也要落测试结果
        message = f"测试失败: {exc}"
    finally:
        if conn is not None:
            conn.close()
    checked_at = datetime.now()
    jh.last_check_at = checked_at
    jh.last_check_ok = ok
    jh.last_check_msg = message[:255]
    await session.flush()
    return {"ok": ok, "message": jh.last_check_msg, "checked_at": checked_at}


async def set_enabled(session: AsyncSession, job_host_id: int, enabled: bool) -> JobHost:
    """启用/禁用作业主机；禁用后不可被新模板选用（不影响已提交工单快照）。"""
    jh = await get_job_host(session, job_host_id)
    jh.enabled = enabled
    await session.flush()
    return jh
