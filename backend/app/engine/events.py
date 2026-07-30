"""执行事件总线：状态变化事件的发布 / 回放（04-API §7/§8）。

事件模型：
    {"seq": 递增序号, "kind": "execution_status|step_status|host_status", "data": {...}, "ts": ISO时间}

三条通道：
- Redis INCR `ops:event:seq:{eid}` 生成单调递增 seq（WS/长轮询断线补发凭据）；
- Redis List `ops:events:{eid}` 保留最近 N 条供回放（长轮询 since_seq / WS 断线重连）；
- Redis PubSub `ops:event:{eid}` 实时推送给 WS 网关。
"""
import json
import logging
from datetime import datetime

from app.core import redis as redis_mod

logger = logging.getLogger("opspilot.engine.events")

# 事件回放列表最多保留条数（覆盖单次执行全部状态变化的合理上限）
_EVENT_LIST_MAX = 2000
# 事件键 TTL：3 天（执行结束后历史状态以 DB 为准，Redis 仅服务实时通道）
_EVENT_TTL = 3 * 24 * 3600

KIND_EXECUTION = "execution_status"
KIND_STEP = "step_status"
KIND_HOST = "host_status"


async def publish_event(execution_id: int, kind: str, data: dict) -> int:
    """发布一条执行事件：发号 → 入回放列表 → PubSub 推送；返回 seq。"""
    r = redis_mod.redis_client
    seq_key = redis_mod.KEY_EXEC_EVENT_SEQ.format(eid=execution_id)
    list_key = redis_mod.KEY_EXEC_EVENT_LIST.format(eid=execution_id)
    seq = await r.incr(seq_key)
    payload = json.dumps(
        {"seq": seq, "kind": kind, "data": data, "ts": datetime.now().isoformat()},
        ensure_ascii=False,
    )
    pipe = r.pipeline()
    pipe.rpush(list_key, payload)
    pipe.ltrim(list_key, -_EVENT_LIST_MAX, -1)
    pipe.expire(list_key, _EVENT_TTL)
    pipe.expire(seq_key, _EVENT_TTL)
    pipe.publish(redis_mod.KEY_EXEC_EVENT.format(eid=execution_id), payload)
    await pipe.execute()
    return seq


async def current_seq(execution_id: int) -> int:
    """当前最大事件序号（WS snapshot 的 seq 基准）。"""
    val = await redis_mod.redis_client.get(
        redis_mod.KEY_EXEC_EVENT_SEQ.format(eid=execution_id)
    )
    return int(val) if val else 0


async def fetch_events_since(execution_id: int, since_seq: int) -> list[dict]:
    """取回放列表中 seq > since_seq 的事件（长轮询 / WS 断线补发）。"""
    raw = await redis_mod.redis_client.lrange(
        redis_mod.KEY_EXEC_EVENT_LIST.format(eid=execution_id), 0, -1
    )
    events: list[dict] = []
    for item in raw:
        try:
            evt = json.loads(item)
        except json.JSONDecodeError:
            continue
        if evt.get("seq", 0) > since_seq:
            events.append(evt)
    return events
