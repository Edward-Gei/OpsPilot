"""执行记录查询服务（只读，04-API §7）：列表 / 详情 / 快照装配。

执行域对外只读——控制操作在工单控制面（ticket_service.control_execution），
状态写入方只有 pipeline / recovery，查询侧不做任何状态修改。
WS 网关的 snapshot 与 REST 详情共用 get_execution_detail 装配逻辑。
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.models.auth import User
from app.models.execution import Execution, ExecutionStep
from app.models.ticket import Ticket, TicketStep


def _iso(dt) -> str | None:
    """datetime → ISO 字符串（空值透传）。"""
    return dt.isoformat() if dt else None


async def get_execution_or_404(session: AsyncSession, execution_id: int) -> Execution:
    """按 ID 取执行实例，不存在抛 40401。"""
    execution = await session.get(Execution, execution_id)
    if execution is None:
        raise Errors.not_found("执行记录不存在")
    return execution


async def list_executions(
    session: AsyncSession, *, page: int, page_size: int,
    ticket_no: str | None = None,
    creator_id: int | None = None, status: str | None = None,
    start: str | None = None, end: str | None = None,
) -> tuple[list[dict], int]:
    """执行记录分页（联工单表取单号/作业主机/发起人，04 §7 筛选项）。"""
    query = (
        select(Execution, Ticket, User.display_name, User.username)
        .join(Ticket, Ticket.id == Execution.ticket_id)
        .join(User, User.id == Ticket.creator_id, isouter=True)
    )
    if ticket_no:
        query = query.where(Ticket.ticket_no.like(f"%{ticket_no}%"))
    if creator_id:
        query = query.where(Ticket.creator_id == creator_id)
    if status:
        query = query.where(Execution.status == status)
    if start:
        query = query.where(Execution.created_at >= start)
    if end:
        query = query.where(Execution.created_at <= (f"{end} 23:59:59" if len(end) == 10 else end))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await session.execute(
            query.order_by(Execution.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    items = [
        {
            "id": e.id, "ticket_id": t.id, "ticket_no": t.ticket_no, "title": t.title,
            "job_host_id": t.job_host_id, "job_host_name": (t.job_host_snap or {}).get("name", ""),
            "creator_id": t.creator_id, "creator_name": dn or un or str(t.creator_id),
            "status": e.status, "total_steps": e.total_steps,
            "triggered_by": e.triggered_by,
            "started_at": _iso(e.started_at), "finished_at": _iso(e.finished_at),
            "created_at": _iso(e.created_at),
        }
        for e, t, dn, un in rows
    ]
    return items, total


async def get_execution_detail(session: AsyncSession, execution_id: int) -> dict:
    """执行详情装配：汇总 + 步骤列表（REST 详情与 WS snapshot 共用）。"""
    execution = await get_execution_or_404(session, execution_id)
    ticket = await session.get(Ticket, execution.ticket_id)
    steps = (
        await session.execute(
            select(ExecutionStep, TicketStep)
            .join(TicketStep, TicketStep.id == ExecutionStep.ticket_step_id, isouter=True)
            .where(ExecutionStep.execution_id == execution_id)
            .order_by(ExecutionStep.step_order)
        )
    ).all()
    return {
        "id": execution.id,
        "ticket_id": execution.ticket_id,
        "ticket_no": ticket.ticket_no if ticket else None,
        "title": ticket.title if ticket else None,
        "job_host_name": (ticket.job_host_snap or {}).get("name", "") if ticket else None,
        "ticket_status": ticket.status if ticket else None,
        "creator_id": ticket.creator_id if ticket else None,
        "interrupt_reason": ticket.interrupt_reason if ticket else None,
        "exec_strategy": (ticket.exec_strategy_snap or {}) if ticket else {},
        "status": execution.status,
        "total_steps": execution.total_steps,
        "triggered_by": execution.triggered_by,
        "started_at": _iso(execution.started_at),
        "finished_at": _iso(execution.finished_at),
        "created_at": _iso(execution.created_at),
        "steps": [
            {
                "step_order": es.step_order,
                "step_name": ts.step_name_snap if ts else f"步骤{es.step_order}",
                "script_type": ts.script_type_snap if ts else None,
                "timeout": ts.timeout if ts else None,
                "status": es.status,
                "exit_code": es.exit_code,
                "error_summary": es.error_summary,
                "started_at": _iso(es.started_at),
                "finished_at": _iso(es.finished_at),
            }
            for es, ts in steps
        ],
    }
