"""工单路由（04-API设计 §6，V2）：ticket:read / ticket:write / ticket:approve 权限点控制。

工单中心只能使用模板：选模板 → 填参数 → 提交（标题=模板名，无草稿，提交即生效）。
不提供模板/审批流/表单字段/通知/策略的任何配置能力。
对象级规则：撤回仅创建人（40302）；审批校验当前节点角色归属。

注意路由顺序：/templates 静态路径必须先于 /{ticket_id} 动态路径注册。
"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.engine import control as exec_ctrl
from app.models.auth import User
from app.schemas.ticket import ApproveRequest, TicketCreateRequest
from app.services import ticket_service

router = APIRouter(prefix="/tickets", tags=["工单"])


def _ticket_brief(t, creator_names: dict[int, str] | None = None) -> dict:
    """工单主表统一序列化（列表/待办共用，不含快照明细）。"""
    return {
        "id": t.id,
        "ticket_no": t.ticket_no,
        "template_id": t.template_id,
        "template_version": t.template_version_snap,
        "type": t.type,
        "title": t.title,
        "app_id": t.app_id,
        "app_name": t.app_name_snap,
        "status": t.status,
        "current_node": t.current_node,
        "total_nodes": len(t.flow_snap or []),
        "creator_id": t.creator_id,
        "creator_name": (creator_names or {}).get(t.creator_id, str(t.creator_id)),
        "submitted_at": t.submitted_at.isoformat() if t.submitted_at else None,
        "finished_at": t.finished_at.isoformat() if t.finished_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def _creator_name_map(session, tickets) -> dict[int, str]:
    """批量取创建人显示名（列表行展示用，避免 N+1 查询）。"""
    ids = {t.creator_id for t in tickets}
    if not ids:
        return {}
    rows = await session.execute(
        select(User.id, User.display_name, User.username).where(User.id.in_(ids))
    )
    return {uid: (dn or un) for uid, dn, un in rows}


# ---------- 可用模板与提交表单（静态路径，先于 /{ticket_id} 注册） ----------

@router.get("/templates", summary="可用模板列表")
async def list_usable_templates(
    session: DbSession,
    actor: User = Depends(require_perm("ticket:write")),
) -> dict:
    """可提交模板：enabled + visible_role_ids 过滤（空=所有 ticket:write 角色可用）。"""
    tpls = await ticket_service.list_visible_templates(session, user_id=actor.id)
    return ok({"items": [
        {"id": t.id, "name": t.name, "type": t.type, "description": t.description,
         "app_id": t.app_id, "approval_enabled": t.approval_enabled,
         "current_version": t.current_version}
        for t in tpls
    ]})


@router.get("/templates/{template_id}/form", summary="提交表单描述")
async def get_template_form(
    template_id: int,
    session: DbSession,
    actor: User = Depends(require_perm("ticket:write")),
) -> dict:
    """汇总参数（各步骤 params_schema 同名合并，排除 fixed）+ 主机/步骤/审批节点/策略只读预览。"""
    form = await ticket_service.get_template_form(session, template_id, user_id=actor.id)
    return ok(form)


# ---------- 提交 / 列表 / 待办 ----------

@router.post("", summary="提交工单")
async def create_ticket(
    req: TicketCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("ticket:write")),
) -> dict:
    """提交工单：只填参数，标题=模板名；服务端固化五重快照后进入审批或直接执行。"""
    ticket = await ticket_service.create_ticket(
        session, creator=actor, template_id=req.template_id, params=req.params
    )
    audit.log(module="ticket", action="ticket.create", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="ticket", target_id=str(ticket.id), target_name=ticket.ticket_no,
              detail={"template_id": req.template_id, "status": ticket.status,
                      "nodes": len(ticket.flow_snap or [])})
    return ok({"id": ticket.id, "ticket_no": ticket.ticket_no,
               "status": ticket.status, "current_node": ticket.current_node})


@router.get("", summary="工单列表")
async def list_tickets(
    session: DbSession,
    _: User = Depends(require_perm("ticket:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    creator_id: int | None = None,
    app_id: int | None = None,
    keyword: str | None = Query(None, description="模糊匹配标题/工单号"),
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """分页查工单（status/creator/app/keyword/时间范围）。"""
    tickets, total = await ticket_service.list_tickets(
        session, page=page, page_size=page_size, status=status,
        creator_id=creator_id, app_id=app_id, keyword=keyword, start=start, end=end,
    )
    names = await _creator_name_map(session, tickets)
    return ok({"items": [_ticket_brief(t, names) for t in tickets], "total": total,
               "page": page, "page_size": page_size})


@router.get("/todo", summary="待我审批")
async def todo_tickets(
    session: DbSession,
    actor: User = Depends(require_perm("ticket:approve")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    """待办列表：当前节点角色 ∩ 我的角色；total 兼作菜单角标计数（FLOW-06）。"""
    tickets, total = await ticket_service.todo_tickets(
        session, user_id=actor.id, page=page, page_size=page_size
    )
    names = await _creator_name_map(session, tickets)
    return ok({"items": [_ticket_brief(t, names) for t in tickets], "total": total,
               "page": page, "page_size": page_size})


# ---------- 详情 / 审批 / 撤回 ----------

@router.get("/{ticket_id}", summary="工单详情")
async def get_ticket(
    ticket_id: int,
    session: DbSession,
    _: User = Depends(require_perm("ticket:read")),
) -> dict:
    """详情（只读）：基本信息 + 参数 + 主机快照 + 步骤快照 + 审批时间线 + execution 概要。"""
    bundle = await ticket_service.get_ticket_bundle(session, ticket_id)
    t = bundle["ticket"]
    creator = bundle["creator"]
    execution = bundle["execution"]
    data = _ticket_brief(t)
    data.update({
        "params": t.params or {},
        "exec_strategy": t.exec_strategy_snap or {},
        "flow_snap": t.flow_snap or [],
        "allow_withdraw": t.allow_withdraw_snap,
        "creator_name": (creator.display_name or creator.username) if creator else str(t.creator_id),
        "hosts": bundle["hosts"],
        "steps": [
            {
                "step_order": s.step_order,
                "step_name": s.step_name_snap,
                "script_type": s.script_type_snap,
                "content_snap": s.content_snap,
                "params": s.params or {},
                "credential_id": s.credential_id,
                "timeout": s.timeout,
            }
            for s in bundle["steps"]
        ],
        # 审批时间线：display_name 优先，兼容审批人被删除的极端情况
        "approvals": [
            {
                "node_order": a.node_order,
                "action": a.action,
                "comment": a.comment,
                "approver_id": a.approver_id,
                "approver_name": display_name or username or str(a.approver_id),
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a, display_name, username in bundle["approvals"]
        ],
        "execution": {
            "id": execution.id,
            "status": execution.status,
            "total_steps": execution.total_steps,
            "total_hosts": execution.total_hosts,
            "triggered_by": execution.triggered_by,
            "created_at": execution.created_at.isoformat() if execution.created_at else None,
        } if execution else None,
    })
    return ok(data)


@router.post("/{ticket_id}/approve", summary="审批工单")
async def approve_ticket(
    ticket_id: int,
    req: ApproveRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("ticket:approve")),
) -> dict:
    """审批通过/驳回：校验当前节点角色归属；末节点通过自动进入执行队列。"""
    ticket = await ticket_service.approve_ticket(
        session, ticket_id, actor=actor, action=req.action, comment=req.comment
    )
    audit.log(module="ticket", action=f"ticket.{req.action}", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="ticket", target_id=str(ticket.id), target_name=ticket.ticket_no,
              detail={"status": ticket.status, "comment": req.comment})
    return ok({"status": ticket.status, "current_node": ticket.current_node})


@router.post("/{ticket_id}/cancel", summary="撤回工单")
async def cancel_ticket(
    ticket_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("ticket:write")),
) -> dict:
    """撤回：仅创建人、approving 状态、且模板允许撤回（TICKET-05）。"""
    ticket = await ticket_service.cancel_ticket(session, ticket_id, actor=actor)
    audit.log(module="ticket", action="ticket.cancel", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="ticket", target_id=str(ticket.id), target_name=ticket.ticket_no)
    return ok({"status": ticket.status})


# ---------- 执行控制面（M5-5：权限 execution:control，仅创建人或 admin） ----------

async def _control(
    ticket_id: int, request: Request, session: DbSession, actor: User, signal: str,
) -> dict:
    """四控制接口公共逻辑：状态校验+发信号（异步生效）+审计（含当时状态）。"""
    ticket, execution = await ticket_service.control_execution(
        session, ticket_id, actor=actor, signal=signal
    )
    # 控制类操作全部审计：操作人/IP/工单/下发时状态（04-API §6）
    audit.log(module="execution", action=f"execution.{signal}", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="ticket", target_id=str(ticket.id), target_name=ticket.ticket_no,
              detail={"execution_id": execution.id, "status_at_signal": ticket.status})
    return ok({"status": ticket.status, "execution_id": execution.id, "signal": signal})


@router.post("/{ticket_id}/abort", summary="中止执行")
async def abort_ticket(
    ticket_id: int, request: Request, session: DbSession,
    actor: User = Depends(require_perm("execution:control")),
) -> dict:
    """中止：queued/running/paused；在跑目标不打断，未派发置 skipped，工单归 interrupted。"""
    return await _control(ticket_id, request, session, actor, exec_ctrl.SIG_ABORT)


@router.post("/{ticket_id}/pause", summary="暂停执行")
async def pause_ticket(
    ticket_id: int, request: Request, session: DbSession,
    actor: User = Depends(require_perm("execution:control")),
) -> dict:
    """暂停：仅 running；在跑目标跑完后停住（paused 非终态），不再派发。"""
    return await _control(ticket_id, request, session, actor, exec_ctrl.SIG_PAUSE)


@router.post("/{ticket_id}/resume", summary="恢复执行")
async def resume_ticket(
    ticket_id: int, request: Request, session: DbSession,
    actor: User = Depends(require_perm("execution:control")),
) -> dict:
    """恢复：仅 paused；从停住处继续派发（含批间暂停放行）。"""
    return await _control(ticket_id, request, session, actor, exec_ctrl.SIG_RESUME)


@router.post("/{ticket_id}/force-abort", summary="强制中止执行")
async def force_abort_ticket(
    ticket_id: int, request: Request, session: DbSession,
    actor: User = Depends(require_perm("execution:control")),
) -> dict:
    """强制中止：running/paused；在中止基础上强杀在跑 SSH 会话（高风险，独立审计）。"""
    return await _control(ticket_id, request, session, actor, exec_ctrl.SIG_FORCE_ABORT)
