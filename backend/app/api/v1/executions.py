"""执行记录路由（04-API §7，只读）：权限 execution:read。

执行域为只读查询；控制操作（中止/暂停/恢复/强制中止）在工单控制面
（tickets.py，权限 execution:control）。/events 为前端实时状态的长轮询主通道
（WS 已弃用，见 app/api/ws_deprecated.py），日志实时刷新由 /logs 按 offset
定时增量拉取实现。
"""
import asyncio

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, require_perm
from app.core.response import ok
from app.engine import events as events_mod
from app.engine.logs import read_log_lines
from app.models.auth import User
from app.services import execution_service

router = APIRouter(prefix="/executions", tags=["执行记录"])

# 长轮询：单次挂起上限（秒）与检查间隔（nginx /api 代理 read_timeout=120s，安全余量充足）
_POLL_TIMEOUT = 30
_POLL_INTERVAL = 1.0


@router.get("", summary="执行记录列表")
async def list_executions(
    session: DbSession,
    _: User = Depends(require_perm("execution:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    ticket_no: str | None = None,
    creator_id: int | None = None,
    status: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """分页；筛选：ticket_no/creator/status/时间范围。"""
    items, total = await execution_service.list_executions(
        session, page=page, page_size=page_size, ticket_no=ticket_no,
        creator_id=creator_id, status=status, start=start, end=end,
    )
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/{execution_id}", summary="执行详情")
async def get_execution(
    execution_id: int,
    session: DbSession,
    _: User = Depends(require_perm("execution:read")),
) -> dict:
    """汇总 + 步骤列表（状态/退出码/耗时）。"""
    return ok(await execution_service.get_execution_detail(session, execution_id))


@router.get("/{execution_id}/logs", summary="历史日志")
async def get_execution_logs(
    execution_id: int,
    session: DbSession,
    _: User = Depends(require_perm("execution:read")),
    step_order: int = Query(..., ge=1),
    offset: int = Query(0, ge=0, description="起始行号（0 起）"),
    limit: int = Query(500, ge=1, le=2000),
) -> dict:
    """读日志文件（历史回看，按行偏移增量拉取）。"""
    await execution_service.get_execution_or_404(session, execution_id)
    lines, next_offset, eof = read_log_lines(
        execution_id, step_order, offset=offset, limit=limit
    )
    return ok({"lines": lines, "next_offset": next_offset, "eof": eof})


@router.get("/{execution_id}/events", summary="事件长轮询")
async def poll_execution_events(
    execution_id: int,
    session: DbSession,
    _: User = Depends(require_perm("execution:read")),
    since_seq: int = Query(0, ge=0),
) -> dict:
    """前端实时状态主通道：有新事件立即返回，否则挂起至 30s 超时返回空列表。

    终态执行不挂起（不会再有新事件），直接返回增量后结束轮询。
    """
    execution = await execution_service.get_execution_or_404(session, execution_id)
    finished = execution.status in ("success", "failed", "terminated", "interrupted", "rejected")
    # 挂起等待只读 Redis：先归还数据库连接，避免大量长轮询占满连接池拖垮全部 API
    await session.close()
    deadline = asyncio.get_running_loop().time() + _POLL_TIMEOUT
    while True:
        events = await events_mod.fetch_events_since(execution_id, since_seq)
        if events or finished or asyncio.get_running_loop().time() >= deadline:
            last_seq = events[-1]["seq"] if events else since_seq
            return ok({"events": events, "last_seq": last_seq, "finished": finished})
        await asyncio.sleep(_POLL_INTERVAL)
