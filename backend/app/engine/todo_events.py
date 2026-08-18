"""用户待办变更事件的发号与回放。"""
import json

from app.core import redis as redis_mod

_EVENT_LIST_MAX = 2000
_EVENT_TTL = 3 * 24 * 3600


async def publish_for_users(user_ids: list[int]) -> dict[int, int]:
    """为每个用户发布一条待办变更事件并返回本次生成的序号。"""
    sequences: dict[int, int] = {}
    for user_id in dict.fromkeys(user_ids):
        r = redis_mod.redis_client
        seq_key = redis_mod.KEY_TODO_EVENT_SEQ.format(user_id=user_id)
        list_key = redis_mod.KEY_TODO_EVENT_LIST.format(user_id=user_id)
        seq = await r.incr(seq_key)
        payload = json.dumps({"seq": seq, "kind": "todo.changed"})
        pipe = r.pipeline()
        pipe.rpush(list_key, payload)
        pipe.ltrim(list_key, -_EVENT_LIST_MAX, -1)
        pipe.expire(list_key, _EVENT_TTL)
        pipe.expire(seq_key, _EVENT_TTL)
        await pipe.execute()
        sequences[user_id] = seq
    return sequences


async def fetch_since(user_id: int, since_seq: int) -> list[dict]:
    """回放指定用户序号之后仍保留在 Redis 中的待办事件。"""
    raw = await redis_mod.redis_client.lrange(
        redis_mod.KEY_TODO_EVENT_LIST.format(user_id=user_id), 0, -1
    )
    events: list[dict] = []
    for item in raw:
        try:
            event = json.loads(item)
        except json.JSONDecodeError:
            continue
        if event.get("seq", 0) > since_seq:
            events.append(event)
    return events
