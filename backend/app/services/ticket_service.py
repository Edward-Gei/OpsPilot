"""工单业务服务（V2）：提交五重快照 / 7 态状态机 / 节点审批推进（03-数据库设计 §5）。

工单中心只能使用模板：提交时仅传 {template_id, params}，标题使用模板名；
五重快照——作业主机、步骤脚本内容、审批节点、执行策略、
模板版本；此后 CMDB/模板变更不影响已提交工单。无草稿态，提交即生效。

M5 执行链：末节点通过/免审提交 → 工单置 queued、预建 Execution/Step 子表
（全 pending）并 XADD ops:exec:queue，由 worker 认领推进；控制面 abort/pause/
resume/force-abort 走 ops:ctrl 信号（engine.control）。M6 前通知只落 record。
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_mod
from app.core.constants import NotifyEvent, TicketStatus
from app.core.response import BizError, Errors
from app.engine import control as exec_ctrl
from app.models.auth import Role, User, UserRole
from app.models.cmdb import JobHost
from app.models.execution import Execution, ExecutionStep
from app.models.job import Credential, TicketTemplate
from app.models.ticket import Ticket, TicketApproval, TicketStep
from app.services import notify_service, template_service


# ---------- 基础查询 ----------

async def get_ticket_or_404(session: AsyncSession, ticket_id: int) -> Ticket:
    """按 ID 取工单，不存在抛 40401。"""
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise Errors.not_found("工单不存在")
    return ticket


def _ensure_creator(ticket: Ticket, actor: User) -> None:
    """对象级校验：仅创建人可撤回（40302，04-API §6）。"""
    if ticket.creator_id != actor.id:
        raise BizError(Errors.OBJECT_DENIED, "仅工单创建人可执行该操作", 403)


async def _next_ticket_no(session: AsyncSession) -> str:
    """生成当日递增工单号：T20260727-0001（03 §5.1）。"""
    prefix = f"T{datetime.now().strftime('%Y%m%d')}-"
    last = (
        await session.execute(
            select(Ticket.ticket_no)
            .where(Ticket.ticket_no.like(f"{prefix}%"))
            .order_by(Ticket.ticket_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


async def _my_role_ids(session: AsyncSession, user_id: int) -> set[int]:
    """用户角色 id 集合（模板可见范围/审批归属校验共用）。"""
    return set(
        (await session.execute(select(UserRole.role_id).where(UserRole.user_id == user_id))).scalars()
    )


async def _role_members(session: AsyncSession, role_id: int) -> list[str]:
    """角色全部成员用户名（通知收件人解析用）。"""
    return list(
        (
            await session.execute(
                select(User.username).join(UserRole, UserRole.user_id == User.id)
                .where(UserRole.role_id == role_id)
            )
        ).scalars()
    )


# ---------- 可用模板与提交表单 ----------

async def list_visible_templates(session: AsyncSession, *, user_id: int) -> list[TicketTemplate]:
    """可提交模板列表：enabled + visible_role_ids 过滤（空=所有 ticket:write 角色）。"""
    my_roles = await _my_role_ids(session, user_id)
    rows = await session.execute(
        select(TicketTemplate).where(TicketTemplate.status == "enabled").order_by(TicketTemplate.id.desc())
    )
    return [
        t for t in rows.scalars()
        if not t.visible_role_ids or set(t.visible_role_ids) & my_roles
    ]


async def _ensure_template_usable(
    session: AsyncSession, template_id: int, *, user_id: int
) -> TicketTemplate:
    """提交前校验：模板存在、enabled、在我的可见范围内。"""
    tpl = await template_service.get_template_or_404(session, template_id)
    if tpl.status != "enabled":
        raise Errors.conflict("模板已禁用，不可提交工单")
    if tpl.visible_role_ids and not (set(tpl.visible_role_ids) & await _my_role_ids(session, user_id)):
        raise BizError(Errors.OBJECT_DENIED, "模板不在你的可提交范围内", 403)
    return tpl


def _merge_params_schema(steps) -> dict[str, dict]:
    """各步骤 params_schema 汇总：同名合并（首现定义为准；任一步骤必填即必填、
    任一步骤固定即固定）；返回 {name: 参数定义}（TPL-03）。"""
    merged: dict[str, dict] = {}
    for step in steps:
        for p in step.params_schema or []:
            name = p["name"]
            if name in merged:
                if p.get("required"):
                    merged[name]["required"] = True
                if p.get("fixed"):
                    merged[name]["fixed"] = True
            else:
                merged[name] = dict(p)
    return merged


async def _flow_preview(session: AsyncSession, tpl: TicketTemplate) -> list[dict]:
    """审批节点预览/快照：[{node, role_id, role_name, approve_mode}]；免审为 []。"""
    if not tpl.approval_enabled:
        return []
    nodes = await template_service.get_template_nodes(session, tpl.id)
    role_ids = {n.role_id for n in nodes}
    names = dict(
        (await session.execute(select(Role.id, Role.name).where(Role.id.in_(role_ids)))).all()
    ) if role_ids else {}
    return [
        {"node": n.node_order, "role_id": n.role_id,
         "role_name": names.get(n.role_id, str(n.role_id)), "approve_mode": n.approve_mode}
        for n in nodes
    ]


async def get_template_form(session: AsyncSession, template_id: int, *, user_id: int) -> dict:
    """提交表单描述：汇总参数（排除 fixed）+ 作业主机/步骤/审批节点/策略只读预览（04 §6）。"""
    tpl = await _ensure_template_usable(session, template_id, user_id=user_id)
    steps = await template_service.get_template_steps(session, template_id)
    jh = await session.get(JobHost, tpl.job_host_id)
    merged = _merge_params_schema(steps)
    return {
        "template": {
            "id": tpl.id, "name": tpl.name, "type": tpl.type, "description": tpl.description,
            "current_version": tpl.current_version, "approval_enabled": tpl.approval_enabled,
            "allow_withdraw": tpl.allow_withdraw,
        },
        # 固定值参数提交人不可见不可改（TPL-03）
        "params": [p for p in merged.values() if not p.get("fixed")],
        "job_host": {"id": jh.id, "name": jh.name, "ip": jh.ip, "ssh_port": jh.ssh_port, "workdir": jh.workdir} if jh else None,
        "steps": [
            {"step_order": s.step_order, "name": s.name, "script_type": s.script_type,
             "timeout": s.timeout}
            for s in steps
        ],
        "flow": await _flow_preview(session, tpl),
        "exec_strategy": tpl.exec_strategy or {},
    }


def _validate_submit_params(values: dict, merged: dict[str, dict]) -> dict:
    """校验提交参数：未定义/固定值键拒绝、必填非空、缺省补默认值；返回汇总生效值。"""
    visible = {n: p for n, p in merged.items() if not p.get("fixed")}
    unknown = set(values) - set(visible)
    if unknown:
        raise Errors.param(f"参数未在模板中定义或为固定值: {sorted(unknown)}")
    result: dict = {}
    for name, p in visible.items():
        value = values.get(name)
        if value is None or value == "":
            value = p.get("default")
        if p.get("required") and (value is None or value == ""):
            raise Errors.param(f"必填参数 {name} 未填写")
        result[name] = value if value is not None else ""
    return result


# ---------- 提交（五重快照固化） ----------

async def create_ticket(
    session: AsyncSession, *, creator: User, template_id: int, params: dict,
) -> Ticket:
    """提交工单：只填参数，标题=模板名；服务端固化五重快照后进入审批或直接执行。

    快照顺序：模板版本 → 作业主机快照 → 步骤内容与参数
    （固定值+提交人填写合并）→ 审批节点 → 执行策略。
    """
    tpl = await _ensure_template_usable(session, template_id, user_id=creator.id)
    steps_tpl = await template_service.get_template_steps(session, template_id)
    if not steps_tpl:
        raise Errors.conflict("模板未配置任何步骤，无法提交")
    jh = await session.get(JobHost, tpl.job_host_id)

    merged = _merge_params_schema(steps_tpl)
    user_params = _validate_submit_params(params or {}, merged)
    flow_snap = await _flow_preview(session, tpl)
    if tpl.approval_enabled and not flow_snap:
        raise Errors.conflict("模板审批配置异常：已开启审批但无审批节点")

    # 引用凭据快照：提单冻结 alias→凭据 id/名称，执行时按 id 实时取密文
    ref_ids = [r["credential_id"] for r in (tpl.credential_refs or [])]
    cred_names: dict[int, str] = {}
    if ref_ids:
        rows = await session.execute(
            select(Credential.id, Credential.name).where(Credential.id.in_(ref_ids))
        )
        cred_names = dict(rows.all())
    credential_refs_snap = [
        {"alias": r["alias"], "credential_id": r["credential_id"],
         "credential_name": cred_names.get(r["credential_id"], "")}
        for r in (tpl.credential_refs or [])
    ]

    ticket = Ticket(
        template_id=tpl.id,
        template_version_snap=tpl.current_version,
        title=tpl.name,
        type=tpl.type,
        params=user_params,
        job_host_id=tpl.job_host_id,
        job_host_snap={"id": jh.id, "name": jh.name, "ip": jh.ip,
                       "ssh_port": jh.ssh_port, "workdir": jh.workdir} if jh else {},
        status=TicketStatus.APPROVING.value,
        exec_strategy_snap=tpl.exec_strategy or {},
        credential_refs=credential_refs_snap,
        flow_snap=flow_snap,
        allow_withdraw_snap=tpl.allow_withdraw,
        creator_id=creator.id,
        submitted_at=datetime.now(),
    )
    # 工单号靠 SELECT MAX 递增生成，并发提交可能撞号；依赖 ticket_no 唯一约束兜底，
    # 冲突时回滚到 SAVEPOINT 重取重试（不动外层请求事务），最多 3 次
    for attempt in range(3):
        ticket.ticket_no = await _next_ticket_no(session)
        try:
            async with session.begin_nested():
                session.add(ticket)
                await session.flush()
            break
        except IntegrityError:
            if attempt == 2:
                raise Errors.conflict("工单号生成冲突，请稍后重试")

    # 快照：步骤内容 + 生效参数（固定值取默认值，其余取提交人汇总参数）
    for s in steps_tpl:
        step_params = {
            p["name"]: (p.get("default") or "") if p.get("fixed") else user_params.get(p["name"], "")
            for p in (s.params_schema or [])
        }
        session.add(TicketStep(
            ticket_id=ticket.id, step_order=s.step_order, step_name_snap=s.name,
            script_type_snap=s.script_type, content_snap=s.content, params=step_params,
            timeout=s.timeout,
        ))
    await session.flush()

    if flow_snap:
        # 审批开：进入 approving 并通知第 1 节点审批角色成员（FLOW-06）
        ticket.current_node = 1
        await _notify_pending_approval(session, ticket)
    else:
        # 免审：直接创建执行并入队（03 §5.1 状态机）
        await _start_execution(session, ticket, triggered_by="no_approval")
    await session.flush()
    return ticket


async def _start_execution(session: AsyncSession, ticket: Ticket, *, triggered_by: str) -> Execution:
    """执行入队（M5）：工单置 queued、预建 Execution/ExecutionStep（全 pending）并 XADD 队列。

    在 API 进程一次性生成 ExecutionStep 骨架，worker 认领后只做状态推进，
    避免执行侧写入与查询竞态。"""
    ticket.status = TicketStatus.QUEUED.value
    ticket.current_node = 0
    steps = list(
        (
            await session.execute(
                select(TicketStep).where(TicketStep.ticket_id == ticket.id).order_by(TicketStep.step_order)
            )
        ).scalars()
    )
    execution = Execution(
        ticket_id=ticket.id, status="queued",
        total_steps=len(steps), triggered_by=triggered_by,
    )
    session.add(execution)
    await session.flush()
    for s in steps:
        session.add(ExecutionStep(
            execution_id=execution.id, ticket_step_id=s.id, step_order=s.step_order,
        ))
    await session.flush()
    # 消息体仅带 execution_id，worker 自行回查（02-技术架构 §4.1）
    await redis_mod.redis_client.xadd(redis_mod.EXEC_QUEUE, {"execution_id": str(execution.id)})
    return execution


# ---------- 执行控制面（M5-5：abort / pause / resume / force-abort） ----------

# 各控制信号允许的工单状态（04-API §6：不匹配返回 40901）
_CONTROL_ALLOWED: dict[str, tuple[str, ...]] = {
    exec_ctrl.SIG_ABORT: (
        TicketStatus.QUEUED.value, TicketStatus.RUNNING.value, TicketStatus.PAUSED.value,
    ),
    exec_ctrl.SIG_PAUSE: (TicketStatus.RUNNING.value,),
    exec_ctrl.SIG_RESUME: (TicketStatus.PAUSED.value,),
    exec_ctrl.SIG_FORCE_ABORT: (TicketStatus.RUNNING.value, TicketStatus.PAUSED.value),
}


async def _ensure_creator_or_admin(session: AsyncSession, ticket: Ticket, actor: User) -> None:
    """控制面对象级校验：仅工单创建人或 admin 角色成员（40302）。"""
    if ticket.creator_id == actor.id:
        return
    is_admin = (
        await session.execute(
            select(Role.id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == actor.id, Role.code == "admin")
            .limit(1)
        )
    ).scalar_one_or_none()
    if is_admin is None:
        raise BizError(Errors.OBJECT_DENIED, "仅工单创建人或管理员可控制执行", 403)


async def control_execution(
    session: AsyncSession, ticket_id: int, *, actor: User, signal: str,
) -> tuple[Ticket, Execution]:
    """执行控制统一入口：状态校验后写 ops:ctrl 信号，由调度器检查点异步生效。

    信号语义（02 §4.2）：pause 停止派发在跑跑完后停住；resume 从停住处继续；
    abort 停止派发未派发置 skipped 工单归 interrupted(user_abort)；force_abort
    在 abort 基础上由 watcher 强杀在跑 SSH 会话。接口本身不改工单状态——
    状态迁移统一由 pipeline 落库，保证单一写入方。"""
    ticket = await get_ticket_or_404(session, ticket_id)
    await _ensure_creator_or_admin(session, ticket, actor)
    allowed = _CONTROL_ALLOWED[signal]
    if ticket.status not in allowed:
        raise Errors.conflict(f"当前状态（{ticket.status}）不允许该控制操作")
    execution = (
        await session.execute(
            select(Execution).where(Execution.ticket_id == ticket.id)
            .order_by(Execution.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if execution is None:
        raise Errors.conflict("工单尚未创建执行实例")
    await exec_ctrl.send_signal(execution.id, signal)
    return ticket, execution


# ---------- 通知（按模板 notify_rules 发射，M6 前只落 record） ----------

async def _resolve_receivers(session: AsyncSession, ticket: Ticket, exprs: list[str]) -> list[str]:
    """接收人表达式解析为用户名列表：creator / approver_role（当前节点角色）/ role:<id>。"""
    result: list[str] = []
    for expr in exprs:
        if expr == "creator":
            creator = await session.get(User, ticket.creator_id)
            result.append(creator.username if creator else str(ticket.creator_id))
        elif expr == "approver_role":
            node = next((n for n in (ticket.flow_snap or []) if n["node"] == ticket.current_node), None)
            if node:
                result.extend(await _role_members(session, node["role_id"]))
        elif expr.startswith("role:"):
            result.extend(await _role_members(session, int(expr.split(":", 1)[1])))
    # 去重保序
    return list(dict.fromkeys(result))


async def _emit_ticket_event(
    session: AsyncSession, ticket: Ticket, event: NotifyEvent, *,
    title: str, content: str, default_receivers: list[str],
    variables: dict | None = None,
) -> None:
    """按模板 notify_rules 发射事件：模板配了该事件规则用规则收件人，否则用默认收件人。

    统一组装工单基础模板变量（工单号/标题/作业主机/创建人）后合并调用方额外变量，
    供渠道消息模板（notify_channel.config）占位渲染。
    """
    tpl = await session.get(TicketTemplate, ticket.template_id)
    rule = next(
        (r for r in ((tpl.notify_rules if tpl else None) or []) if r.get("event") == event.value),
        None,
    )
    receivers = (
        await _resolve_receivers(session, ticket, rule["receivers"])
        if rule and rule.get("receivers") else default_receivers
    )
    creator = await session.get(User, ticket.creator_id)
    await notify_service.emit(
        session, event, receiver=",".join(receivers) or None,
        title=title, content=content, ref_type="ticket", ref_id=ticket.id,
        variables={
            "ticket_no": ticket.ticket_no,
            "ticket_title": ticket.title,
            "job_host_name": (ticket.job_host_snap or {}).get("name", ""),
            "creator": creator.username if creator else str(ticket.creator_id),
            **(variables or {}),
        },
    )


async def _notify_pending_approval(session: AsyncSession, ticket: Ticket) -> None:
    """发射"待审批"通知：默认收件人为当前节点审批角色的全部成员（FLOW-06）。"""
    node = next((n for n in (ticket.flow_snap or []) if n["node"] == ticket.current_node), None)
    if node is None:
        return
    await _emit_ticket_event(
        session, ticket, NotifyEvent.TICKET_PENDING_APPROVAL,
        title=f"工单 {ticket.ticket_no} 等待第 {ticket.current_node} 节点审批",
        content=f"{ticket.title}（作业主机：{(ticket.job_host_snap or {}).get('name', '')}）待 {node['role_name']} 审批",
        default_receivers=await _role_members(session, node["role_id"]),
        variables={"node": ticket.current_node, "role": node["role_name"]},
    )


# ---------- 审批 / 撤回 ----------

async def approve_ticket(
    session: AsyncSession, ticket_id: int, *, actor: User, action: str, comment: str | None,
) -> Ticket:
    """审批通过/驳回：校验当前节点角色归属，节点间依次推进（FLOW-02/03/04）。

    - 节点内或签：角色任一成员处理即定节点结果（V1 仅 any 生效）；可批自己创建的工单
    - 通过：非末节点 → 推进 current_node；末节点 → 触发执行入队（打桩）
    - 驳回：意见必填（40001），工单直接关闭为 rejected 终态
    """
    ticket = await get_ticket_or_404(session, ticket_id)
    if ticket.status != TicketStatus.APPROVING.value:
        raise Errors.conflict("工单不在待审批状态")
    flow: list[dict] = ticket.flow_snap or []
    node = next((n for n in flow if n["node"] == ticket.current_node), None)
    if node is None:
        raise Errors.conflict("工单审批节点快照异常")
    if node["role_id"] not in await _my_role_ids(session, actor.id):
        raise BizError(Errors.OBJECT_DENIED, f"当前节点需 {node['role_name']} 角色审批", 403)
    if action == "reject" and not (comment or "").strip():
        raise Errors.param("驳回时必须填写审批意见")

    session.add(TicketApproval(
        ticket_id=ticket.id, node_order=ticket.current_node,
        role_id=node["role_id"], approver_id=actor.id, action=action, comment=comment,
    ))

    creator = await session.get(User, ticket.creator_id)
    creator_name = creator.username if creator else str(ticket.creator_id)
    if action == "reject":
        # 任一节点驳回 → 工单关闭终态，通知创建人（FLOW-04）；先发射再清 current_node，
        # 保证 approver_role 收件人表达式仍能解析到驳回节点角色
        ticket.status = TicketStatus.REJECTED.value
        ticket.finished_at = datetime.now()
        await _emit_ticket_event(
            session, ticket, NotifyEvent.TICKET_REJECTED,
            title=f"工单 {ticket.ticket_no} 已被驳回",
            content=f"{ticket.title}：{actor.username} 驳回，意见：{comment}",
            default_receivers=[creator_name],
            variables={"approver": actor.username, "comment": comment or ""},
        )
        ticket.current_node = 0
    elif ticket.current_node < len(flow):
        # 非末节点通过 → 推进下一节点并通知该节点审批角色成员
        ticket.current_node += 1
        await _notify_pending_approval(session, ticket)
    else:
        # 末节点通过 → 通知创建人并自动进入执行队列（FLOW-03，无手动触发环节）
        await _emit_ticket_event(
            session, ticket, NotifyEvent.TICKET_APPROVED,
            title=f"工单 {ticket.ticket_no} 审批通过",
            content=f"{ticket.title}：审批通过，已自动进入执行队列",
            default_receivers=[creator_name],
            variables={"approver": actor.username, "comment": comment or ""},
        )
        await _start_execution(session, ticket, triggered_by="auto_approve")
    await session.flush()
    return ticket


async def cancel_ticket(session: AsyncSession, ticket_id: int, *, actor: User) -> Ticket:
    """撤回工单：仅创建人、approving 状态、且模板允许撤回（TICKET-05）。"""
    ticket = await get_ticket_or_404(session, ticket_id)
    _ensure_creator(ticket, actor)
    if ticket.status != TicketStatus.APPROVING.value:
        raise Errors.conflict("仅审批中状态可撤回")
    if not ticket.allow_withdraw_snap:
        raise Errors.rejected("该模板不允许撤回工单")
    ticket.status = TicketStatus.CANCELLED.value
    ticket.current_node = 0
    ticket.finished_at = datetime.now()
    await session.flush()
    return ticket


# ---------- 列表 / 待办 / 详情 ----------

async def list_tickets(
    session: AsyncSession, *, page: int, page_size: int,
    status: str | None = None, creator_id: int | None = None,
    keyword: str | None = None, start: str | None = None, end: str | None = None,
) -> tuple[list[Ticket], int]:
    """分页查工单（04 §6：status/creator/keyword/时间范围）。"""
    query = select(Ticket)
    if status:
        query = query.where(Ticket.status == status)
    if creator_id:
        query = query.where(Ticket.creator_id == creator_id)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(Ticket.title.like(like) | Ticket.ticket_no.like(like))
    if start:
        query = query.where(Ticket.created_at >= start)
    if end:
        # 只传日期时把上界补到当天末；括号必不可少，否则三元表达式会把裸字符串传入 where
        query = query.where(Ticket.created_at <= (f"{end} 23:59:59" if len(end) == 10 else end))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(
        query.order_by(Ticket.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def todo_tickets(
    session: AsyncSession, *, user_id: int, page: int, page_size: int,
) -> tuple[list[Ticket], int]:
    """待我审批：当前节点角色 ∩ 我的角色（FLOW-06）。

    flow_snap 为 JSON 快照无法下推 SQL，先取全部审批中工单再内存过滤
    （V1 进行中工单量级有限，可接受）。
    """
    my_roles = await _my_role_ids(session, user_id)
    rows = await session.execute(
        select(Ticket).where(Ticket.status == TicketStatus.APPROVING.value).order_by(Ticket.id.desc())
    )
    matched = [
        t for t in rows.scalars()
        if any(
            n["node"] == t.current_node and n["role_id"] in my_roles
            for n in (t.flow_snap or [])
        )
    ]
    return matched[(page - 1) * page_size: page * page_size], len(matched)


async def get_ticket_bundle(session: AsyncSession, ticket_id: int) -> dict:
    """详情数据装配（只读）：作业主机快照 + 步骤快照 + 审批时间线 + 执行概要。"""
    ticket = await get_ticket_or_404(session, ticket_id)
    steps = list(
        (
            await session.execute(
                select(TicketStep).where(TicketStep.ticket_id == ticket_id).order_by(TicketStep.step_order)
            )
        ).scalars()
    )
    # 审批时间线：附审批人显示名（只 INSERT 表，按时间正序）
    approvals = (
        await session.execute(
            select(TicketApproval, User.display_name, User.username)
            .join(User, User.id == TicketApproval.approver_id, isouter=True)
            .where(TicketApproval.ticket_id == ticket_id)
            .order_by(TicketApproval.id)
        )
    ).all()
    execution = (
        await session.execute(
            select(Execution).where(Execution.ticket_id == ticket_id).order_by(Execution.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    creator = await session.get(User, ticket.creator_id)
    return {
        "ticket": ticket, "job_host": ticket.job_host_snap, "steps": steps,
        "approvals": approvals, "execution": execution, "creator": creator,
    }
