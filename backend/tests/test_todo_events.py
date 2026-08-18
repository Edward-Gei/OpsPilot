from app.engine import todo_events


async def test_publish_for_users_and_replay_since(fake_redis):
    first = await todo_events.publish_for_users([11, 22])
    await todo_events.publish_for_users([11])

    assert first == {11: 1, 22: 1}
    assert [event["seq"] for event in await todo_events.fetch_since(11, 0)] == [1, 2]
    assert [event["seq"] for event in await todo_events.fetch_since(22, 0)] == [1]
    assert (await todo_events.fetch_since(11, 0))[0]["kind"] == "todo.changed"
