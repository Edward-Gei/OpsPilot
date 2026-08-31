"""工单模板与流程模板服务：全量更新、启停和引用保护。"""

import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.models.auth import Role
from app.models.cmdb import JobHost
from app.models.job import Credential, ProcessStep, ProcessTemplate, TicketTemplate
from app.models.ticket import Ticket
from app.services.credential_service import is_script_secret

ACTIVE_TICKET_STATUSES = ("approving", "queued", "running", "paused")
_CREDENTIAL_ALIAS_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")


async def _unique(session: AsyncSession, model, name: str, exclude_id: int | None = None) -> None:
    query = select(model.id).where(model.name == name)
    if exclude_id is not None:
        query = query.where(model.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.conflict(f"名称 {name} 已存在")


async def get_process_or_404(session: AsyncSession, process_id: int) -> ProcessTemplate:
    """读取流程模板，不存在时返回统一业务错误。"""
    row = await session.get(ProcessTemplate, process_id)
    if row is None:
        raise Errors.not_found("流程模板不存在")
    return row


async def get_template_or_404(session: AsyncSession, template_id: int) -> TicketTemplate:
    """读取工单模板。"""
    row = await session.get(TicketTemplate, template_id)
    if row is None:
        raise Errors.not_found("工单模板不存在")
    return row


async def get_process_steps(session: AsyncSession, process_id: int) -> list[ProcessStep]:
    rows = await session.execute(
        select(ProcessStep).where(ProcessStep.process_template_id == process_id).order_by(ProcessStep.step_order)
    )
    return list(rows.scalars())


async def _validate_process_refs(session: AsyncSession, data: dict) -> None:
    """校验审批角色存在；角色任意成员通过由审批服务处理。"""
    role_ids = {s.get("approval_role_id") for s in data.get("steps", []) if s.get("approval_role_id")}
    if role_ids:
        found = set((await session.execute(select(Role.id).where(Role.id.in_(role_ids)))).scalars())
        if role_ids - found:
            raise Errors.param(f"审批角色不存在: {sorted(role_ids - found)}")


def _process_snapshot(process: ProcessTemplate, steps: list[ProcessStep]) -> dict:
    return {
        "process_name": process.name,
        "exec_strategy": process.exec_strategy or {},
        "steps": [
            {
                "step_order": s.step_order,
                "name": s.name,
                "script_type": s.script_type,
                "content": s.content,
                "timeout": s.timeout,
                "approval_role_id": s.approval_role_id,
            }
            for s in steps
        ],
    }


async def list_process_templates(session: AsyncSession, *, page: int, page_size: int,
                                 keyword: str | None = None, status: str | None = None):
    query = select(ProcessTemplate)
    if keyword:
        query = query.where(ProcessTemplate.name.like(f"%{keyword}%"))
    if status:
        query = query.where(ProcessTemplate.status == status)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(query.order_by(ProcessTemplate.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return list(rows.scalars()), total


async def create_process_template(session: AsyncSession, *, created_by: int, data: dict) -> ProcessTemplate:
    await _unique(session, ProcessTemplate, data["name"])
    await _validate_process_refs(session, data)
    process = ProcessTemplate(
        name=data["name"], description=data.get("description"), status="enabled",
        exec_strategy=data.get("exec_strategy") or {},
        created_by=created_by,
    )
    session.add(process)
    await session.flush()
    for index, item in enumerate(data["steps"], 1):
        session.add(ProcessStep(process_template_id=process.id, step_order=index, **item))
    await session.flush()
    return process


async def update_process_template(session: AsyncSession, process_id: int, *, data: dict) -> ProcessTemplate:
    process = await get_process_or_404(session, process_id)
    if data["name"] != process.name:
        await _unique(session, ProcessTemplate, data["name"], process_id)
    await _validate_process_refs(session, data)
    for row in await get_process_steps(session, process_id):
        await session.delete(row)
    process.name = data["name"]
    process.description = data.get("description")
    process.exec_strategy = data.get("exec_strategy") or {}
    await session.flush()
    for index, item in enumerate(data["steps"], 1):
        session.add(ProcessStep(process_template_id=process.id, step_order=index, **item))
    await session.flush()
    return process


async def set_process_status(session: AsyncSession, process_id: int, status: str) -> ProcessTemplate:
    process = await get_process_or_404(session, process_id)
    process.status = status
    await session.flush()
    return process


async def delete_process_template(session: AsyncSession, process_id: int) -> ProcessTemplate:
    process = await get_process_or_404(session, process_id)
    refs = (await session.execute(select(TicketTemplate.id).where(TicketTemplate.process_template_id == process_id))).scalars().all()
    if refs:
        raise Errors.rejected("流程模板仍被工单模板引用，无法删除")
    for row in await get_process_steps(session, process_id):
        await session.delete(row)
    await session.delete(process)
    await session.flush()
    return process


async def list_templates(session: AsyncSession, *, page: int, page_size: int, keyword: str | None = None,
                         type_: str | None = None, status: str | None = None,
                         process_template_id: int | None = None):
    query = select(TicketTemplate)
    if keyword:
        query = query.where(TicketTemplate.name.like(f"%{keyword}%"))
    if type_:
        query = query.where(TicketTemplate.type == type_)
    if status:
        query = query.where(TicketTemplate.status == status)
    if process_template_id is not None:
        query = query.where(TicketTemplate.process_template_id == process_template_id)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(query.order_by(TicketTemplate.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return list(rows.scalars()), total


async def _validate_ticket_refs(session: AsyncSession, data: dict, *, update_id: int | None = None) -> ProcessTemplate:
    host = await session.get(JobHost, data["job_host_id"])
    if host is None:
        raise Errors.not_found("作业主机不存在")
    if not host.enabled:
        raise Errors.param("作业主机已停用")
    process = await get_process_or_404(session, data["process_template_id"])
    if process.status != "enabled":
        if update_id is None:
            raise Errors.conflict("停用流程模板不能被新工单模板引用")
        current = await session.get(TicketTemplate, update_id)
        if current is None or current.process_template_id != process.id:
            raise Errors.conflict("停用流程模板不能作为新的流程引用")
    role_ids = set(data.get("visible_role_ids") or [])
    if role_ids:
        found = set((await session.execute(select(Role.id).where(Role.id.in_(role_ids)))).scalars())
        if role_ids - found:
            raise Errors.param(f"可见角色不存在: {sorted(role_ids - found)}")
    await validate_script_secret_refs(session, data.get("credential_refs") or [])
    return process


async def validate_script_secret_refs(session: AsyncSession, refs: list[dict]) -> list[Credential]:
    """校验模板只引用唯一的脚本密钥，防止 SSH 登录凭据进入脚本环境。"""
    aliases = [item.get("alias") for item in refs]
    if any(not isinstance(alias, str) or not _CREDENTIAL_ALIAS_RE.fullmatch(alias) for alias in aliases):
        raise Errors.param("脚本密钥别名仅支持大写字母、数字和下划线")
    if len(aliases) != len(set(aliases)):
        raise Errors.param("脚本密钥别名不能重复")
    credential_ids = [item.get("credential_id") for item in refs]
    if any(not isinstance(credential_id, int) for credential_id in credential_ids):
        raise Errors.param("脚本密钥凭据 ID 不合法")
    if len(credential_ids) != len(set(credential_ids)):
        raise Errors.param("同一凭据不能重复绑定")
    if not credential_ids:
        return []
    rows = list((await session.execute(select(Credential).where(Credential.id.in_(credential_ids)))).scalars())
    by_id = {row.id: row for row in rows}
    missing = set(credential_ids) - set(by_id)
    if missing:
        raise Errors.not_found(f"脚本密钥凭据不存在: {sorted(missing)}")
    invalid = [credential_id for credential_id in credential_ids if not is_script_secret(by_id[credential_id])]
    if invalid:
        raise Errors.param("工单模板只能绑定脚本密钥凭据")
    return rows


async def credential_ref_metadata(session: AsyncSession, refs: list[dict]) -> list[dict]:
    """为已授权的模板/工单读取安全引用元数据，不读取任何密文。"""
    credential_ids = [item.get("credential_id") for item in refs if isinstance(item.get("credential_id"), int)]
    if not credential_ids:
        return []
    rows = await session.execute(select(Credential.id, Credential.name).where(Credential.id.in_(credential_ids)))
    names = dict(rows.all())
    return [
        {
            "alias": item.get("alias"), "credential_id": item["credential_id"],
            "credential_name": item.get("credential_name") or names.get(item["credential_id"]),
        }
        for item in refs if item.get("credential_id") in names or item.get("credential_name")
    ]


async def credential_ref_snapshot(session: AsyncSession, refs: list[dict]) -> list[dict]:
    """提单时固化引用名称，运行期仍按 ID 使用最新密文。"""
    credentials = await validate_script_secret_refs(session, refs)
    names = {credential.id: credential.name for credential in credentials}
    return [
        {"alias": item["alias"], "credential_id": item["credential_id"],
         "credential_name": names[item["credential_id"]]}
        for item in refs
    ]


async def create_template(session: AsyncSession, *, created_by: int, data: dict) -> TicketTemplate:
    await _unique(session, TicketTemplate, data["name"])
    await _validate_ticket_refs(session, data)
    tpl = TicketTemplate(created_by=created_by, **data)
    session.add(tpl)
    await session.flush()
    return tpl


async def update_template(session: AsyncSession, template_id: int, *, data: dict) -> TicketTemplate:
    tpl = await get_template_or_404(session, template_id)
    if data["name"] != tpl.name:
        await _unique(session, TicketTemplate, data["name"], template_id)
    await _validate_ticket_refs(session, data, update_id=template_id)
    for key, value in data.items():
        setattr(tpl, key, value)
    await session.flush()
    return tpl


async def set_status(session: AsyncSession, template_id: int, *, status: str) -> TicketTemplate:
    tpl = await get_template_or_404(session, template_id)
    tpl.status = status
    await session.flush()
    return tpl


async def delete_template(session: AsyncSession, template_id: int) -> TicketTemplate:
    tpl = await get_template_or_404(session, template_id)
    active = (await session.execute(select(Ticket.ticket_no).where(
        Ticket.template_id == template_id, Ticket.status.in_(ACTIVE_TICKET_STATUSES)
    ))).scalars().all()
    if active:
        raise Errors.rejected(f"工单模板仍有执行中的工单: {', '.join(active[:5])}")
    await session.delete(tpl)
    await session.flush()
    return tpl


async def process_snapshot(session: AsyncSession, process_id: int) -> dict:
    process = await get_process_or_404(session, process_id)
    return _process_snapshot(process, await get_process_steps(session, process_id))
