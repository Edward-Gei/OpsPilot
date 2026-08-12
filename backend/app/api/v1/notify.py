"""通知路由（04-API 设计 §10）：读、写、测试分别使用独立通知权限。"""
from fastapi import APIRouter, Depends, Query, Request

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.models.auth import User
from app.schemas.notify import ChannelTestRequest, ChannelUpdateRequest, EventMappingsUpdateRequest
from app.services import notify_service

router = APIRouter(prefix="/notify", tags=["通知"])


@router.get("/channels", summary="渠道配置列表")
async def list_channels(
    session: DbSession,
    _: User = Depends(require_perm("notify:read")),
) -> dict:
    """六类型全量输出；敏感项（SMTP 密码/签名密钥）以 ****** 掩码回显。"""
    return ok({"items": await notify_service.list_channels(session)})


@router.put("/channels/{channel_type}", summary="更新渠道配置")
async def update_channel(
    channel_type: str,
    req: ChannelUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("notify:write")),
) -> dict:
    """保存 enabled+config+secret（secret 不传=不变更）；写审计。"""
    await notify_service.update_channel(
        session, channel_type,
        enabled=req.enabled, config=req.config, secret=req.secret, actor_id=actor.id,
    )
    await session.commit()
    audit.log(module="notify", action="channel.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="notify_channel", target_id=channel_type,
              detail={"enabled": req.enabled})
    return ok()


@router.post("/channels/{channel_type}/test", summary="发送测试消息")
async def test_channel(
    channel_type: str,
    req: ChannelTestRequest,
    session: DbSession,
    _: User = Depends(require_perm("notify:test")),
) -> dict:
    """按当前表单值同步发送测试消息；失败不抛异常，前端按 success 展示。"""
    success, message = await notify_service.test_channel(
        session, channel_type, config=req.config, secret=req.secret, receiver=req.receiver,
        event=req.event,
    )
    return ok({"success": success, "message": message})


@router.get("/events", summary="事件-渠道映射")
async def list_events(
    session: DbSession,
    _: User = Depends(require_perm("notify:read")),
) -> dict:
    """六事件全量输出（含空映射事件，前端渲染矩阵）。"""
    return ok({"items": await notify_service.list_event_mappings(session)})


@router.put("/events", summary="全量提交映射")
async def update_events(
    req: EventMappingsUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("notify:write")),
) -> dict:
    """整表按提交内容重建（未提交事件视为清空）；写审计。"""
    await notify_service.update_event_mappings(session, req.mappings)
    await session.commit()
    audit.log(module="notify", action="events.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              detail={"mappings": req.mappings})
    return ok()


@router.get("/records", summary="发送记录")
async def list_records(
    session: DbSession,
    _: User = Depends(require_perm("notify:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    event: str | None = None,
    channel: str | None = None,
    status: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """发送记录分页；筛选：event/channel/status/时间范围。"""
    items, total = await notify_service.list_records(
        session, page=page, page_size=page_size,
        event=event, channel=channel, status=status, start=start, end=end,
    )
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})
