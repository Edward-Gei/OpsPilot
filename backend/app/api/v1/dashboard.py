"""工作台路由：概览聚合与活动趋势（数据段按权限点裁剪）。

无独立权限点：summary 内部按调用者 perms 决定各段返回与否，
旧工单趋势依赖 ticket:read，活动趋势要求工单或执行读取权限。
"""
from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, DbSession, require_perm
from app.core.response import ok
from app.models.auth import User
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["工作台"])


@router.get("/summary", summary="工作台概览")
async def dashboard_summary(session: DbSession, user: CurrentUser) -> dict:
    """返回业务统计、待办与关注事项；无读取权限的数据段为 null。"""
    return ok(await dashboard_service.get_summary(session, user))


@router.get("/ticket-trend", summary="提单数趋势")
async def ticket_trend(
    session: DbSession,
    _: User = Depends(require_perm("ticket:read")),
    granularity: str = Query("day", description="day/week/month/year"),
) -> dict:
    """按粒度分桶补零：day=近30天 / week=近12周 / month=近12月 / year=近5年。"""
    return ok(await dashboard_service.get_ticket_trend(session, granularity))


@router.get("/activity-trend", summary="工单与完成执行趋势")
async def activity_trend(
    session: DbSession,
    user: CurrentUser,
    granularity: str = Query("day", description="day/week/month/year"),
) -> dict:
    """按自然时间桶补零；至少需一项读取权限，无权限系列为 null。"""
    return ok(await dashboard_service.get_activity_trend(session, user, granularity))
