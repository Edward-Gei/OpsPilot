"""工作台路由：概览聚合与提单趋势（登录即可访问，数据段按权限点裁剪）。

无独立权限点：summary 内部按调用者 perms 决定各段返回与否，
trend 明确依赖 ticket:read（与工单列表同门槛）。
"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, DbSession, require_perm
from app.core.response import ok
from app.models.auth import User
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["工作台"])


@router.get("/summary", summary="工作台概览")
async def dashboard_summary(session: DbSession, user: CurrentUser) -> dict:
    """一次性返回 CMDB/工单/待办/执行/审计各段计数；无权限段为 null。"""
    return ok(await dashboard_service.get_summary(session, user))


@router.get("/ticket-trend", summary="提单数趋势")
async def ticket_trend(
    session: DbSession,
    _: User = Depends(require_perm("ticket:read")),
    granularity: str = Query("day", description="day/week/month/year"),
) -> dict:
    """按粒度分桶补零：day=近30天 / week=近12周 / month=近12月 / year=近5年。"""
    return ok(await dashboard_service.get_ticket_trend(session, granularity))
