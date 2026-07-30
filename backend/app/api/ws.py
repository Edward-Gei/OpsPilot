"""执行实时通道 WS 网关（04-API §8）：/ws/executions/{id}?token=。

协议（服务端 → 客户端，JSON）：
    {"type":"snapshot","seq":N,"data":{执行全量状态+主机明细}}   连接建立后首推
    {"type":"log","step":n,"ip":"...","lines":[...]}             100ms 聚合日志
    {"type":"event","seq":N,"data":{"kind":...,...},"ts":"..."}  状态事件
    {"type":"ping"}                                              30s 心跳
客户端 → 服务端：{"type":"pong"}；{"type":"subscribe_log","step":1,"ip":"..."}
（切换日志焦点：服务端只推焦点主机 log，event 全推；未设置焦点时全推）。

鉴权：token 来自 query 参数（WS 无法带 Authorization 头），复刻
get_current_user + require_perm("execution:read") 逻辑；失败关闭码
4401（未认证）/ 4403（无权限）/ 4404（执行不存在）。
断线重连：客户端记录收到的最大 seq，重连前用 REST events?since_seq= 补齐。
"""
import asyncio
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core import redis as redis_mod
from app.core.database import async_session_factory
from app.core.security import SCOPE_ACCESS, decode_token
from app.engine import events as events_mod
from app.models.auth import User
from app.services import execution_service, rbac_service

logger = logging.getLogger("opspilot.ws")

router = APIRouter()

# 心跳间隔（秒）：与 04-API §8 约定一致
_PING_INTERVAL = 30


async def _authenticate(token: str, execution_id: int) -> tuple[int | None, dict | None]:
    """WS 鉴权 + snapshot 预装配：返回 (关闭码, snapshot)；关闭码为 None 表示通过。"""
    try:
        payload = decode_token(token, SCOPE_ACCESS)
    except Exception:  # noqa: BLE001 token 非法/过期统一 4401
        return 4401, None
    async with async_session_factory() as session:
        user = await session.get(User, int(payload["sub"]))
        if user is None or user.status != "active":
            return 4401, None
        perms = await rbac_service.get_user_perms(session, user.id)
        if "execution:read" not in perms:
            return 4403, None
        try:
            snapshot = await execution_service.get_execution_detail(session, execution_id)
            hosts, _total = await execution_service.list_hosts(session, execution_id)
        except Exception:  # noqa: BLE001 40401 等业务异常 → 4404
            return 4404, None
        snapshot["hosts"] = hosts
    return None, snapshot


@router.websocket("/ws/executions/{execution_id}")
async def execution_ws(
    websocket: WebSocket,
    execution_id: int,
    token: str = Query(""),
) -> None:
    """执行实时通道：snapshot 首推 → PubSub 转发（log 按焦点过滤，event 全推）。"""
    await websocket.accept()
    close_code, snapshot = await _authenticate(token, execution_id)
    if close_code is not None:
        await websocket.close(code=close_code)
        return
    # snapshot 的 seq 基准：此后事件 seq 均大于该值，客户端凭此去重/补齐
    seq = await events_mod.current_seq(execution_id)
    await websocket.send_json({"type": "snapshot", "seq": seq, "data": snapshot})

    # 日志焦点：{"step":n,"ip":...}；None = 不过滤全推
    focus: dict | None = None
    pubsub = redis_mod.redis_client.pubsub()
    await pubsub.subscribe(
        redis_mod.KEY_EXEC_LOG.format(eid=execution_id),
        redis_mod.KEY_EXEC_EVENT.format(eid=execution_id),
    )
    log_channel = redis_mod.KEY_EXEC_LOG.format(eid=execution_id)

    async def _forward() -> None:
        """PubSub → WS 转发：log 按焦点过滤；event 转协议格式全推。"""
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                body = json.loads(message["data"])
            except (TypeError, json.JSONDecodeError):
                continue
            if message.get("channel") == log_channel:
                if focus and not (
                    body.get("step_order") == focus.get("step")
                    and body.get("ip") == focus.get("ip")
                ):
                    continue
                await websocket.send_json({
                    "type": "log", "step": body.get("step_order"),
                    "ip": body.get("ip"), "lines": body.get("lines") or [],
                })
            else:
                await websocket.send_json({
                    "type": "event", "seq": body.get("seq"),
                    "data": {"kind": body.get("kind"), **(body.get("data") or {})},
                    "ts": body.get("ts"),
                })

    async def _heartbeat() -> None:
        """30s 心跳：nginx read_timeout 3600s，心跳主要用于客户端断线感知。"""
        while True:
            await asyncio.sleep(_PING_INTERVAL)
            await websocket.send_json({"type": "ping"})

    forward_task = asyncio.get_running_loop().create_task(_forward())
    ping_task = asyncio.get_running_loop().create_task(_heartbeat())
    try:
        # 接收循环：subscribe_log 切换日志焦点；pong 忽略
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "subscribe_log":
                focus = {"step": msg.get("step"), "ip": msg.get("ip")}
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 连接层异常按断开处理
        logger.debug("WS 连接异常断开 execution=%s", execution_id, exc_info=True)
    finally:
        for task in (forward_task, ping_task):
            task.cancel()
        await asyncio.gather(forward_task, ping_task, return_exceptions=True)
        try:
            await pubsub.aclose()
        except Exception:  # noqa: BLE001 清理失败无需上抛
            pass
