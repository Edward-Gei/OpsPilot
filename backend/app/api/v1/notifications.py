"""站内通知路由（04-API 设计 §10.1，NOTIFY-06）：登录即可，仅操作本人数据。

顶栏铃铛消费：未读数 30 秒轮询、下拉面板最近列表、单条/全部已读。
不挂权限点——站内信按收件人落库，天然只含"与我相关"的消息。
"""
from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession
from app.core.response import ok
from app.services import notify_service

router = APIRouter(prefix="/notifications", tags=["站内通知"])


@router.get("", summary="我的站内信")
async def list_notifications(
    session: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    only_unread: bool = Query(False),
) -> dict:
    """按创建时间倒序分页；only_unread=true 只看未读。"""
    items, total = await notify_service.list_user_notifications(
        session, user.id, page=page, page_size=page_size, only_unread=only_unread,
    )
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/unread-count", summary="未读数")
async def get_unread_count(session: DbSession, user: CurrentUser) -> dict:
    """铃铛角标轮询接口：只返回计数，保持轻量。"""
    return ok({"count": await notify_service.unread_count(session, user.id)})


@router.put("/read-all", summary="全部已读")
async def read_all(session: DbSession, user: CurrentUser) -> dict:
    """把本人全部未读置为已读，返回本次置已读条数。"""
    count = await notify_service.mark_all_read(session, user.id)
    await session.commit()
    return ok({"count": count})


@router.put("/{notification_id}/read", summary="单条已读")
async def read_one(notification_id: int, session: DbSession, user: CurrentUser) -> dict:
    """校验归属后置已读；非本人/不存在统一 40401，重复调用幂等。"""
    await notify_service.mark_read(session, user.id, notification_id)
    await session.commit()
    return ok()
