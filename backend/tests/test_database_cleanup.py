"""原生拒绝留痕及物理连接关闭后的配额结算回归。"""
import asyncio
from datetime import datetime

import pytest
from sqlalchemy import select

from conftest import auth_header, login_for_tokens
from app.database_adapters.base import StopConfirmation
from app.models.database_management import DatabaseAuditEvent, DatabaseInstance, DatabaseResult, DatabaseTask
from app.services import database_execution_service as execution
from app.services.database_quota_service import bucket, claim_next_task
from test_database_core import FakeAdapter, submit, target


@pytest.mark.parametrize("operation", [
    {"type": "find", "filter": {"$where": "sleep(1)"}},
    {"type": "find", "filter": {"nested": {"$function": {"body": "return 1"}}}},
    {"type": "find", "projection": {"amount": {"$add": [1, 2]}}},
    {"type": "distinct", "field": "$unsafe"},
])
async def test_api_native_semantic_rejection_is_persisted_after_authorization(client, db_factory, seed, operation):
    instance_id, _ = await target(db_factory, seed)
    async with db_factory() as session:
        (await session.get(DatabaseInstance, instance_id)).engine = "mongodb"
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.post("/api/v1/database/tasks", headers=headers, json={
        "kind": "mongo_read", "instance_id": instance_id,
        "context": {"database": "demo", "collection": "orders"}, "operation": operation})
    assert response.status_code == 422
    data = response.json()["data"]
    assert data is not None, "semantic rejection must reach the durable task/audit path"
    assert data["status"] == "rejected" and data["task_id"] > 0
    async with db_factory() as session:
        task = await session.get(DatabaseTask, data["task_id"])
        assert task.target_attempted_at is None and task.quota_released
        event = (await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == task.id))).scalar_one()
        assert event.event_type == "rejected" and event.validation["executed"] is False
        assert event.submitted_text_enc and task.submitted_text_enc
        assert (await bucket(session, "global", "all")).queued_count == 0


@pytest.mark.parametrize("operation", [
    {"type": "hash_page", "key": "demo:hash", "count": 10},
    {"type": "set_page", "key": "demo:set", "count": 10},
    {"type": "list_page", "key": "demo:list", "offset": 0, "count": 10},
    {"type": "zset_page", "key": "demo:zset", "offset": 0, "count": 10},
])
async def test_api_redis_pagination_passes_schema_and_native_preparation(client, db_factory, seed, operation):
    instance_id, _ = await target(db_factory, seed)
    async with db_factory() as session:
        (await session.get(DatabaseInstance, instance_id)).engine = "redis"
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.post("/api/v1/database/tasks", headers=headers, json={
        "kind": "redis_read", "instance_id": instance_id, "context": {"database": 0}, "operation": operation})
    assert response.status_code == 202, response.json()
    async with db_factory() as session:
        task = await session.get(DatabaseTask, response.json()["data"]["task_id"])
        assert task.status == "queued" and task.prepared_operation_enc


class ClosingAdapter(FakeAdapter):
    def __init__(self, *, fail=False):
        super().__init__()
        self.close_entered, self.allow_close = asyncio.Event(), asyncio.Event()
        self.fail = fail
        self.is_open = False

    async def open_operation(self, connection, prepared, *, deadline):
        self.is_open = True
        return await super().open_operation(connection, prepared, deadline=deadline)

    async def close(self, handle):
        self.close_entered.set()
        await self.allow_close.wait()
        if self.fail:
            raise OSError("synthetic close failure")
        self.is_open = False


async def start_closing_task(db_factory, seed, monkeypatch, adapter):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr(execution, "get_adapter", lambda _: adapter)
    claim = await claim_next_task(db_factory, "cleanup-executor")
    running = asyncio.create_task(execution.run_claimed_task(db_factory, claim))
    await asyncio.wait_for(adapter.close_entered.wait(), 3)
    return submission.task_id, claim, running


async def test_success_keeps_all_quota_and_result_unpublished_until_close(db_factory, seed, monkeypatch):
    adapter = ClosingAdapter()
    task_id, _, running = await start_closing_task(db_factory, seed, monkeypatch, adapter)
    try:
        async with db_factory() as session:
            task = await session.get(DatabaseTask, task_id)
            quota = await bucket(session, "global", "all")
            assert adapter.is_open and not task.quota_released
            assert task.status == "running" and quota.active_count == quota.target_sockets == 1
            assert (await session.execute(select(DatabaseResult).where(DatabaseResult.task_id == task_id))).scalar_one_or_none() is None
    finally:
        adapter.allow_close.set()
        await asyncio.wait_for(running, 3)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "succeeded" and task.quota_released and not adapter.is_open
        assert task.backend_ref["local_cleanup"]["closed"] is True
        assert (await bucket(session, "global", "all")).target_sockets == 0


async def test_failed_close_cannot_be_released_by_target_terminal_evidence(db_factory, seed, monkeypatch):
    adapter = ClosingAdapter(fail=True)
    task_id, claim, running = await start_closing_task(db_factory, seed, monkeypatch, adapter)
    adapter.allow_close.set()
    await asyncio.wait_for(running, 3)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "recovering" and not task.quota_released
        assert task.error_code == "local_cleanup_unconfirmed" and adapter.is_open
        assert task.backend_ref["local_cleanup"]["closed"] is False
        assert (await bucket(session, "global", "all")).target_sockets == 1
    assert not await execution._settle(db_factory, claim, "failed", StopConfirmation(True, "target_ended", {}))
    async with db_factory() as session:
        assert not (await session.get(DatabaseTask, task_id)).quota_released
        assert (await bucket(session, "global", "all")).target_sockets == 1


async def test_close_timeout_keeps_physical_quota(db_factory, seed, monkeypatch):
    monkeypatch.setattr(execution, "LOCAL_CLOSE_TIMEOUT", .05)
    adapter = ClosingAdapter()
    task_id, _, running = await start_closing_task(db_factory, seed, monkeypatch, adapter)
    await asyncio.wait_for(running, 3)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "recovering" and adapter.is_open
        assert task.error_code == "local_cleanup_unconfirmed" and not task.quota_released
        assert (await bucket(session, "global", "all")).target_sockets == 1


async def test_old_lease_may_only_record_its_own_close_fact(db_factory, seed, monkeypatch):
    adapter = ClosingAdapter()
    task_id, old_claim, running = await start_closing_task(db_factory, seed, monkeypatch, adapter)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        task.lease_token = "replacement-owner-token"
        task.status = "recovering"
        await session.commit()
    adapter.allow_close.set()
    await asyncio.wait_for(running, 3)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.lease_token == "replacement-owner-token" and not task.quota_released
        assert task.backend_ref["local_cleanup"]["closed"] is True
        assert (await bucket(session, "global", "all")).target_sockets == 1
    assert not await execution._settle(db_factory, old_claim, "succeeded", StopConfirmation(True, "late", {}))


async def test_runner_has_finished_before_close_and_failed_control_retains_quota(db_factory, seed, monkeypatch):
    class RunningAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.reading, self.read_finished = asyncio.Event(), asyncio.Event()
            self.closed_after_reader = False
        async def batches(self, handle):
            self.reading.set()
            try:
                await asyncio.Event().wait()
                yield
            finally:
                self.read_finished.set()
        async def request_stop(self, connection, backend_ref, *, reason, deadline):
            # 控制SQL已结束，但控制连接finally清理抛异常，不提供关闭事实。
            from app.database_adapters.base import AdapterError
            raise AdapterError("control_close_unconfirmed", "control close failed",
                               StopConfirmation(True, "target_ended", backend_ref))
        async def close(self, handle):
            self.closed_after_reader = self.read_finished.is_set()
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    adapter = RunningAdapter()
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr(execution, "get_adapter", lambda _: adapter)
    claim = await claim_next_task(db_factory, "cleanup-executor")
    running = asyncio.create_task(execution.run_claimed_task(db_factory, claim))
    await asyncio.wait_for(adapter.reading.wait(), 3)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        task.cancel_requested_at = datetime.now()
        await session.commit()
    await asyncio.wait_for(running, 3)
    assert adapter.closed_after_reader
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        quota = await bucket(session, "global", "all")
        assert task.status == "recovering" and task.error_code == "control_cleanup_unconfirmed"
        assert task.stop_confirmation["confirmed"] is True
        assert task.backend_ref["local_cleanup"]["closed"] is True
        assert quota.control_sockets == quota.target_sockets == 1


@pytest.mark.parametrize("closed", [False, True])
async def test_control_close_fact_is_required_even_when_stop_is_confirmed(db_factory, seed, closed):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    class ControlAdapter:
        control_connection_cost = 1
        async def request_stop(self, connection, backend_ref, *, reason, deadline):
            return StopConfirmation(True, "target_ended", backend_ref, {"control_closed": closed})
    ref = {"instance_id": instance_id, "task_id": submission.task_id, "executor_id": "control-executor"}
    confirmation = await execution.stop_target(db_factory, None, ControlAdapter(), ref, "user_cancel")
    assert confirmation.confirmed
    async with db_factory() as session:
        quota = await bucket(session, "global", "all")
        assert quota.control_sockets == int(not closed)
        control = await bucket(session, "control", submission.task_id)
        assert control.active_count == int(not closed)
