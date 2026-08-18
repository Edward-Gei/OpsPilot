import json

import pytest
from redis.exceptions import WatchError

from app.engine import todo_events


async def test_publish_for_users_and_replay_since(fake_redis):
    first = await todo_events.publish_for_users([11, 22, 11])
    await todo_events.publish_for_users([11])

    assert first == {11: 1, 22: 1}
    assert [event["seq"] for event in await todo_events.fetch_since(11, 0)] == [1, 2]
    assert [event["seq"] for event in await todo_events.fetch_since(11, 1)] == [2]
    assert await todo_events.fetch_since(11, 2) == []
    assert [event["seq"] for event in await todo_events.fetch_since(22, 0)] == [1]
    assert (await todo_events.fetch_since(11, 0))[0]["kind"] == "todo.changed"
    assert await fake_redis.ttl("ops:todo:event:seq:11") > 0
    assert await fake_redis.ttl("ops:todo:events:11") > 0


async def test_replay_is_sequence_ordered_and_bounded(fake_redis, monkeypatch):
    monkeypatch.setattr(todo_events, "_EVENT_LIST_MAX", 2)
    key = "ops:todo:events:31"
    await fake_redis.rpush(
        key,
        json.dumps({"seq": 2, "kind": "todo.changed"}),
        json.dumps({"seq": 1, "kind": "todo.changed"}),
    )
    assert [event["seq"] for event in await todo_events.fetch_since(31, 0)] == [1, 2]

    await todo_events.publish_for_users([32])
    await todo_events.publish_for_users([32])
    await todo_events.publish_for_users([32, 32])
    assert [event["seq"] for event in await todo_events.fetch_since(32, 0)] == [2, 3]


async def test_publish_stops_after_bounded_watch_retries(fake_redis, monkeypatch):
    attempts = 0

    class AlwaysConflictedPipeline:
        async def watch(self, key):
            return None

        async def get(self, key):
            return None

        def multi(self):
            return None

        def incr(self, key):
            return None

        def rpush(self, key, payload):
            return None

        def ltrim(self, key, start, end):
            return None

        def expire(self, key, ttl):
            return None

        async def execute(self):
            nonlocal attempts
            attempts += 1
            raise WatchError("conflict")

        async def reset(self):
            return None

    monkeypatch.setattr(fake_redis, "pipeline", lambda: AlwaysConflictedPipeline())

    with pytest.raises(WatchError, match="conflict"):
        await todo_events.publish_for_users([41])
    assert attempts == 3
