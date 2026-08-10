"""工单服务：参数校验、流程快照、步骤前审批和执行队列。"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_mod
from app.core.constants import ExecutionStatus, HostExecStatus, NotifyEvent, TicketStatus
from app.core.response import BizError, Errors
from app.engine import control as exec_ctrl
from app.models.auth import Role, User, UserRole
from app.models.cmdb import JobHost
from app.models.execution import Execution, ExecutionStep
from app.models.job import TicketTemplate
from app.models.ticket import Ticket, TicketApproval, TicketStep
from app.services import notify_service, parameter_prepare_service, template_service


async def get_ticket_or_404(session: AsyncSession, ticket_id: int) -> Ticket:
    """按 ID 获取工单。"""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise Errors.not_found("工单不存在")
    return ticket


async def _my_role_ids(session: AsyncSession, user_id: int) -> set[int]:
    return set((await session.execute(select(UserRole.role_id).where(UserRole.user_id == user_id))).scalars())


async def _role_members(session: AsyncSession, role_id: int) -> list[str]:
    return list((await session.execute(
        select(User.username).join(UserRole, UserRole.user_id == User.id).where(UserRole.role_id == role_id)
    )).scalars())


async def list_visible_templates(session: AsyncSession, *, user_id: int) -> list[TicketTemplate]:
    roles = await _my_role_ids(session, user_id)
    rows = (await session.execute(select(TicketTemplate).where(TicketTemplate.status == "enabled").order_by(TicketTemplate.id.desc()))).scalars()
    return [t for t in rows if not t.visible_role_ids or roles.intersection(t.visible_role_ids)]


async def _ensure_template_usable(session: AsyncSession, template_id: int, *, user_id: int) -> TicketTemplate:
    tpl = await template_service.get_template_or_404(session, template_id)
    if tpl.status != "enabled":
        raise Errors.conflict("工单模板已停用，不能提交")
    if tpl.visible_role_ids and not (set(tpl.visible_role_ids) & await _my_role_ids(session, user_id)):
        raise BizError(Errors.OBJECT_DENIED, "模板不在你的可见范围内", 403)
    process = await template_service.get_process_or_404(session, tpl.process_template_id)
    if process.status != "enabled":
        raise Errors.conflict("流程模板已停用，不能提交")
    return tpl


def _validate_params(values: dict, schema: list[dict]) -> dict:
    """合并固定值和用户输入，并校验文本/枚举/必填约束。"""
    definitions = {p["name"]: p for p in schema}
    unknown = set(values) - set(definitions)
    if unknown:
        raise Errors.param(f"参数未定义: {sorted(unknown)}")
    result = {}
    for name, definition in definitions.items():
        if definition["source"] == "fixed":
            result[name] = definition.get("default")
            continue
        value = values.get(name, definition.get("default"))
        if definition["source"] == "generated":
            # 动态参数在 prepare 阶段注入；直接创建时必须显式提供已生成值。
            value = values.get(name, value)
        if definition.get("required") and (value is None or value == ""):
            raise Errors.param(f"必填参数 {name} 未填写")
        if definition.get("input_type") == "enum" and value not in definition.get("options", []):
            raise Errors.param(f"参数 {name} 不是有效枚举值")
        result[name] = "" if value is None else value
    return result


async def get_template_form(session: AsyncSession, template_id: int, *, user_id: int) -> dict:
    tpl = await _ensure_template_usable(session, template_id, user_id=user_id)
    process = await template_service.get_process_or_404(session, tpl.process_template_id)
    steps = await template_service.get_process_steps(session, process.id)
    host = await session.get(JobHost, tpl.job_host_id)
    return {
        "template": {"id": tpl.id, "name": tpl.name, "type": tpl.type, "description": tpl.description,
                     "process_template_id": process.id, "process_name": process.name,
                     "allow_withdraw": tpl.allow_withdraw},
        "params": [p for p in (process.params_schema or []) if p.get("source") != "fixed"],
        "job_host": {"id": host.id, "name": host.name, "ip": host.ip, "ssh_port": host.ssh_port,
                     "workdir": host.workdir} if host else None,
        "steps": [{"step_order": s.step_order, "name": s.name, "script_type": s.script_type,
                   "timeout": s.timeout, "approval_role_id": s.approval_role_id} for s in steps],
        "exec_strategy": process.exec_strategy or {},
        "generator": {"enabled": bool(process.generator_script), "timeout": process.generator_timeout},
    }


async def _next_ticket_no(session: AsyncSession) -> str:
    prefix = f"T{datetime.now():%Y%m%d}-"
    last = (await session.execute(select(Ticket.ticket_no).where(Ticket.ticket_no.like(f"{prefix}%")).order_by(Ticket.ticket_no.desc()).limit(1))).scalar_one_or_none()
    return f"{prefix}{(int(last.rsplit('-', 1)[1]) + 1) if last else 1:04d}"


async def _first_approval_step(session: AsyncSession, ticket: Ticket) -> int | None:
    rows = (await session.execute(select(TicketStep).where(TicketStep.ticket_id == ticket.id).order_by(TicketStep.step_order))).scalars()
    for row in rows:
        if row.approval_role_id_snap:
            return row.step_order
    return None


async def _resolve_receivers(session: AsyncSession, ticket: Ticket, expressions: list[str]) -> list[str]:
    """解析通知规则收件人，保证角色成员和创建人都使用用户名发送。"""
    receivers: list[str] = []
    for expression in expressions:
        if expression == "creator":
            creator = await session.get(User, ticket.creator_id)
            if creator:
                receivers.append(creator.username)
        elif expression == "approver_role":
            step = (await session.execute(
                select(TicketStep).where(
                    TicketStep.ticket_id == ticket.id,
                    TicketStep.step_order == ticket.current_step,
                )
            )).scalar_one_or_none()
            if step and step.approval_role_id_snap:
                receivers.extend(await _role_members(session, step.approval_role_id_snap))
        elif expression.startswith("role:"):
            try:
                receivers.extend(await _role_members(session, int(expression.split(":", 1)[1])))
            except ValueError:
                continue
    return list(dict.fromkeys(receivers))


async def _emit_ticket_event(
    session: AsyncSession,
    ticket: Ticket,
    event: NotifyEvent,
    *,
    title: str,
    content: str,
    default_receivers: list[str],
    variables: dict | None = None,
) -> None:
    """按工单模板规则发射事件，并补齐渠道模板需要的工单变量。"""
    template = await session.get(TicketTemplate, ticket.template_id)
    rule = next(
        (item for item in ((template.notify_rules if template else None) or [])
         if item.get("event") == event.value),
        None,
    )
    receivers = (
        await _resolve_receivers(session, ticket, rule.get("receivers", []))
        if rule and rule.get("receivers")
        else default_receivers
    )
    creator = await session.get(User, ticket.creator_id)
    await notify_service.emit(
        session,
        event,
        receiver=",".join(receivers) or None,
        title=title,
        content=content,
        ref_type="ticket",
        ref_id=ticket.id,
        variables={
            "ticket_no": ticket.ticket_no,
            "ticket_title": ticket.title,
            "job_host_name": (ticket.job_host_snap or {}).get("name", ""),
            "creator": creator.username if creator else str(ticket.creator_id),
            **(variables or {}),
        },
    )


async def _notify_pending_approval(session: AsyncSession, ticket: Ticket) -> None:
    """工单进入审批步骤时通知该步骤角色的全部成员。"""
    step = (await session.execute(
        select(TicketStep).where(
            TicketStep.ticket_id == ticket.id,
            TicketStep.step_order == ticket.current_step,
        )
    )).scalar_one_or_none()
    if step is None or not step.approval_role_id_snap:
        return
    role = await session.get(Role, step.approval_role_id_snap)
    role_name = role.name if role else str(step.approval_role_id_snap)
    await _emit_ticket_event(
        session,
        ticket,
        NotifyEvent.TICKET_PENDING_APPROVAL,
        title=f"工单 {ticket.ticket_no} 等待第 {ticket.current_step} 步骤审批",
        content=f"{ticket.title}（作业主机：{(ticket.job_host_snap or {}).get('name', '')}）待 {role_name} 审批",
        default_receivers=await _role_members(session, step.approval_role_id_snap),
        variables={"node": ticket.current_step, "role": role_name},
    )


async def create_ticket(session: AsyncSession, *, creator: User, template_id: int, params: dict, prepare_id: str | None = None) -> Ticket:
    tpl = await _ensure_template_usable(session, template_id, user_id=creator.id)
    process = await template_service.get_process_or_404(session, tpl.process_template_id)
    steps = await template_service.get_process_steps(session, process.id)
    if not steps:
        raise Errors.conflict("流程模板没有步骤，不能提交")
    prepared_values = {}
    if prepare_id:
        prepared_values = await parameter_prepare_service.consume_parameters(
            session, token=prepare_id, template_id=template_id, params=params or {}
        )
    values = _validate_params({**(params or {}), **prepared_values}, process.params_schema or [])
    host = await session.get(JobHost, tpl.job_host_id)
    flow = await template_service.process_snapshot(session, process.id)
    ticket = Ticket(
        ticket_no=await _next_ticket_no(session), template_id=tpl.id, title=tpl.name, type=tpl.type,
        params=values, job_host_id=tpl.job_host_id,
        job_host_snap={"id": host.id, "name": host.name, "ip": host.ip, "ssh_port": host.ssh_port, "workdir": host.workdir} if host else {},
        process_template_id_snap=process.id, process_name_snap=process.name,
        status=TicketStatus.APPROVING.value, exec_strategy_snap=process.exec_strategy or {},
        flow_snap=flow, allow_withdraw_snap=tpl.allow_withdraw, creator_id=creator.id, submitted_at=datetime.now(),
    )
    session.add(ticket)
    await session.flush()
    for step in steps:
        session.add(TicketStep(
            ticket_id=ticket.id, step_order=step.step_order, step_name_snap=step.name,
            script_type_snap=step.script_type, content_snap=step.content, params=values,
            timeout=step.timeout, approval_role_id_snap=step.approval_role_id,
        ))
    await session.flush()
    first = await _first_approval_step(session, ticket)
    if first is None or first > 1:
        await _start_execution(session, ticket)
    else:
        ticket.current_step = first
        await _notify_pending_approval(session, ticket)
    await session.flush()
    return ticket


async def _start_execution(session: AsyncSession, ticket: Ticket) -> Execution:
    ticket.status = TicketStatus.QUEUED.value
    ticket.current_step = 0
    steps = list((await session.execute(select(TicketStep).where(TicketStep.ticket_id == ticket.id).order_by(TicketStep.step_order))).scalars())
    execution = Execution(ticket_id=ticket.id, status="queued", total_steps=len(steps))
    session.add(execution)
    await session.flush()
    for step in steps:
        session.add(ExecutionStep(execution_id=execution.id, ticket_step_id=step.id, step_order=step.step_order))
    await session.flush()
    try:
        await redis_mod.redis_client.xadd(redis_mod.EXEC_QUEUE, {"execution_id": str(execution.id)})
    except Exception:
        pass
    return execution


async def _ensure_creator_or_admin(session: AsyncSession, ticket: Ticket, actor: User) -> None:
    if ticket.creator_id == actor.id:
        return
    is_admin = (await session.execute(select(Role.id).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == actor.id, Role.code == "admin").limit(1))).scalar_one_or_none()
    if is_admin is None:
        raise BizError(Errors.OBJECT_DENIED, "仅工单创建人或管理员可控制执行", 403)


_CONTROL_ALLOWED = {
    exec_ctrl.SIG_ABORT: ("queued", "running", "paused"),
    exec_ctrl.SIG_PAUSE: ("running",), exec_ctrl.SIG_RESUME: ("paused",),
    exec_ctrl.SIG_FORCE_ABORT: ("running", "paused"),
}


async def control_execution(session: AsyncSession, ticket_id: int, *, actor: User, signal: str):
    ticket = await get_ticket_or_404(session, ticket_id)
    await _ensure_creator_or_admin(session, ticket, actor)
    if ticket.status not in _CONTROL_ALLOWED[signal]:
        raise Errors.conflict(f"当前状态不允许 {signal}")
    execution = (await session.execute(select(Execution).where(Execution.ticket_id == ticket.id).order_by(Execution.id.desc()).limit(1))).scalar_one_or_none()
    if execution is None:
        raise Errors.conflict("工单尚未创建执行实例")
    await exec_ctrl.send_signal(execution.id, signal)
    return ticket, execution


async def _role_allowed(session: AsyncSession, user_id: int, role_id: int) -> bool:
    return (await session.execute(select(UserRole.user_id).where(UserRole.user_id == user_id, UserRole.role_id == role_id).limit(1))).scalar_one_or_none() is not None


async def approve_ticket(session: AsyncSession, ticket_id: int, *, actor: User, action: str, comment: str | None) -> Ticket:
    ticket = await get_ticket_or_404(session, ticket_id)
    if ticket.status != TicketStatus.APPROVING.value or not ticket.current_step:
        raise Errors.conflict("工单当前不在待审批状态")
    step = (await session.execute(select(TicketStep).where(TicketStep.ticket_id == ticket.id, TicketStep.step_order == ticket.current_step))).scalar_one_or_none()
    if step is None or not step.approval_role_id_snap or not await _role_allowed(session, actor.id, step.approval_role_id_snap):
        raise BizError(Errors.OBJECT_DENIED, "你不属于当前步骤审批角色", 403)
    existing = (await session.execute(select(TicketApproval.id).where(TicketApproval.ticket_id == ticket.id, TicketApproval.step_order == ticket.current_step, TicketApproval.action == "approve").limit(1))).scalar_one_or_none()
    if existing:
        raise Errors.conflict("当前步骤已审批")
    session.add(TicketApproval(ticket_id=ticket.id, step_order=ticket.current_step, role_id=step.approval_role_id_snap, approver_id=actor.id, action=action, comment=comment))
    creator = await session.get(User, ticket.creator_id)
    creator_name = creator.username if creator else str(ticket.creator_id)
    if action == "reject":
        ticket.status = TicketStatus.REJECTED.value
        ticket.finished_at = datetime.now()
        execution = (await session.execute(
            select(Execution).where(Execution.ticket_id == ticket.id)
            .order_by(Execution.id.desc()).limit(1)
        )).scalar_one_or_none()
        # 审批接口只接受 approving 工单，因此暂停中的执行不会进入此分支。
        if execution and execution.status in {
            ExecutionStatus.QUEUED.value,
            ExecutionStatus.RUNNING.value,
        }:
            execution.status = ExecutionStatus.REJECTED.value
            execution.finished_at = datetime.now()
            pending_steps = (await session.execute(
                select(ExecutionStep).where(
                    ExecutionStep.execution_id == execution.id,
                    ExecutionStep.status == HostExecStatus.PENDING.value,
                )
            )).scalars()
            for execution_step in pending_steps:
                execution_step.status = HostExecStatus.SKIPPED.value
                execution_step.finished_at = datetime.now()
        await _emit_ticket_event(
            session,
            ticket,
            NotifyEvent.TICKET_REJECTED,
            title=f"工单 {ticket.ticket_no} 已被驳回",
            content=f"{ticket.title}：{actor.username} 驳回，意见：{comment or ''}",
            default_receivers=[creator_name],
            variables={"approver": actor.username, "comment": comment or ""},
        )
        ticket.current_step = 0
    else:
        next_step = (await session.execute(select(TicketStep.step_order).where(TicketStep.ticket_id == ticket.id, TicketStep.step_order > ticket.current_step, TicketStep.approval_role_id_snap.is_not(None)).order_by(TicketStep.step_order).limit(1))).scalar_one_or_none()
        ticket.current_step = next_step or 0
        execution = (await session.execute(select(Execution).where(Execution.ticket_id == ticket.id).order_by(Execution.id.desc()).limit(1))).scalar_one_or_none()
        if execution is None:
            await _start_execution(session, ticket)
        else:
            execution.status = "queued"
            ticket.status = TicketStatus.QUEUED.value
            try:
                await redis_mod.redis_client.xadd(redis_mod.EXEC_QUEUE, {"execution_id": str(execution.id)})
            except Exception:
                pass
        if not next_step:
            await _emit_ticket_event(
                session,
                ticket,
                NotifyEvent.TICKET_APPROVED,
                title=f"工单 {ticket.ticket_no} 审批通过",
                content=f"{ticket.title}：审批通过，已自动进入执行队列",
                default_receivers=[creator_name],
                variables={"approver": actor.username, "comment": comment or ""},
            )
    await session.flush()
    return ticket


async def cancel_ticket(session: AsyncSession, ticket_id: int, *, actor: User) -> Ticket:
    ticket = await get_ticket_or_404(session, ticket_id)
    if ticket.creator_id != actor.id:
        raise BizError(Errors.OBJECT_DENIED, "仅创建人可终止工单", 403)
    if ticket.status not in ("approving", "queued"):
        raise Errors.conflict("当前状态不能终止")
    ticket.status = TicketStatus.CANCELLED.value
    ticket.finished_at = datetime.now()
    ticket.current_step = 0
    execution = (await session.execute(
        select(Execution).where(Execution.ticket_id == ticket.id)
        .order_by(Execution.id.desc()).limit(1)
    )).scalar_one_or_none()
    if execution and execution.status == ExecutionStatus.QUEUED.value:
        execution.status = ExecutionStatus.CANCELLED.value
        execution.finished_at = datetime.now()
        pending_steps = (await session.execute(
            select(ExecutionStep).where(
                ExecutionStep.execution_id == execution.id,
                ExecutionStep.status == HostExecStatus.PENDING.value,
            )
        )).scalars()
        for execution_step in pending_steps:
            execution_step.status = HostExecStatus.SKIPPED.value
            execution_step.finished_at = datetime.now()
    await session.flush()
    return ticket


async def list_tickets(session: AsyncSession, *, page: int, page_size: int, status: str | None = None,
                       creator_id: int | None = None, keyword: str | None = None, start: str | None = None, end: str | None = None):
    query = select(Ticket)
    if status: query = query.where(Ticket.status == status)
    if creator_id: query = query.where(Ticket.creator_id == creator_id)
    if keyword: query = query.where(Ticket.ticket_no.like(f"%{keyword}%") | Ticket.title.like(f"%{keyword}%"))
    if start: query = query.where(Ticket.created_at >= start)
    if end: query = query.where(Ticket.created_at <= f"{end} 23:59:59")
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(query.order_by(Ticket.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return list(rows.scalars()), total


async def todo_tickets(session: AsyncSession, *, user_id: int, page: int, page_size: int):
    roles = await _my_role_ids(session, user_id)
    rows = list((await session.execute(select(Ticket).where(Ticket.status == "approving").order_by(Ticket.id.desc()))).scalars())
    result = []
    for ticket in rows:
        step = (await session.execute(select(TicketStep).where(TicketStep.ticket_id == ticket.id, TicketStep.step_order == ticket.current_step))).scalar_one_or_none()
        if step and step.approval_role_id_snap in roles:
            result.append(ticket)
    total = len(result)
    return result[(page - 1) * page_size: page * page_size], total


async def get_ticket_bundle(session: AsyncSession, ticket_id: int) -> dict:
    ticket = await get_ticket_or_404(session, ticket_id)
    creator = await session.get(User, ticket.creator_id)
    steps = list((await session.execute(select(TicketStep).where(TicketStep.ticket_id == ticket.id).order_by(TicketStep.step_order))).scalars())
    approvals = list((await session.execute(select(TicketApproval, User.display_name, User.username).join(User, User.id == TicketApproval.approver_id, isouter=True).where(TicketApproval.ticket_id == ticket.id).order_by(TicketApproval.id))).all())
    execution = (await session.execute(select(Execution).where(Execution.ticket_id == ticket.id).order_by(Execution.id.desc()).limit(1))).scalar_one_or_none()
    return {"ticket": ticket, "creator": creator, "steps": steps, "approvals": approvals, "execution": execution,
            "job_host": ticket.job_host_snap}
