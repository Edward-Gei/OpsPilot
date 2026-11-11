"""连接检测使用独立预算，关闭证据决定是否可以释放物理额度。"""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
import time

import pytest
from sqlalchemy import select

from app.database_adapters.base import AdapterError, StopConfirmation, bounded, local_closed_error
from app.models.database_management import DatabaseAuditEvent, DatabaseInstance, DatabaseTask
from app.schemas.database_management import ProbeTaskRequest
from app.services import config_service
from app.services import database_execution_service as execution
from app.services import database_maintenance_service as maintenance
from app.services import database_task_service as tasks
from app.services.database_quota_service import bucket, claim_next_task
from test_database_core import FakeAdapter, identity, submit, target


async def claim_probe(factory, seed, instance_id, *, kind="probe"):
    if kind == "probe":
        async with factory() as session:
            submitted = await tasks.submit_task(session, identity(seed), ProbeTaskRequest(kind="probe", instance_id=instance_id))
    else:
        submitted = await submit(factory, seed, instance_id)
    return submitted, await claim_next_task(factory, "probe-timeout-executor")


@pytest.mark.parametrize("configured", [1, 60, 120])
async def test_probe_claim_has_five_second_budget_without_changing_query_budget(db_factory, seed, configured):
    instance_id, _ = await target(db_factory, seed)
    async with db_factory() as session:
        await config_service.set_config(session, "database.execution_timeout_seconds", configured, seed["users"]["admin"])
        await session.commit()
    for kind, seconds in (("probe", 5), ("sql", configured)):
        submitted, claim = await claim_probe(db_factory, seed, instance_id, kind=kind)
        async with db_factory() as session:
            task = await session.get(DatabaseTask, submitted.task_id)
            assert (task.deadline_at-task.started_at).total_seconds() == seconds
            assert claim.policy_snapshot["execution_timeout_seconds"] == seconds


class OpeningFailure(FakeAdapter):
    def __init__(self, *, local_closed, confirmed=False, slow=False, cleanup_delay=0):
        super().__init__()
        self.local_closed, self.confirmed = local_closed, confirmed
        self.slow, self.cleanup_delay, self.closed = slow, cleanup_delay, False

    async def open_operation(self, connection, prepared, *, deadline):
        if self.slow:
            try:
                await bounded(asyncio.Event().wait(), deadline)
            except TimeoutError as exc:
                await asyncio.sleep(self.cleanup_delay)
                self.closed = self.local_closed
                if self.local_closed:
                    raise local_closed_error(exc) from exc
                raise
        self.closed = self.local_closed
        raise AdapterError("target_connection_failed", "合成连接失败", StopConfirmation(
            self.confirmed, "connection_not_established" if self.confirmed else "target_stop_unconfirmed", {},
            {"local_closed": self.local_closed}))


async def install_failure(factory, seed, monkeypatch, adapter, *, kind="probe", short_deadline=False):
    instance_id, _ = await target(factory, seed)
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr(execution, "get_adapter", lambda _: adapter)
    submitted, claim = await claim_probe(factory, seed, instance_id, kind=kind)
    if short_deadline:
        deadline = datetime.now()+timedelta(seconds=.08)
        async with factory() as session:
            (await session.get(DatabaseTask, submitted.task_id)).deadline_at = deadline
            await session.commit()
        claim = replace(claim, deadline_at=deadline)
    return instance_id, submitted.task_id, claim


@pytest.mark.parametrize("kind,closed,confirmed,expected", [
    ("probe", True, False, "failed"), ("probe", True, True, "failed"),
    ("probe", False, False, "recovering"), ("probe", False, True, "recovering"),
    ("sql", True, False, "recovering"),
])
async def test_open_failure_only_closed_probe_may_finish_without_remote_stop_confirmation(
    db_factory, seed, monkeypatch, kind, closed, confirmed, expected):
    adapter = OpeningFailure(local_closed=closed, confirmed=confirmed)
    instance_id, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter, kind=kind)
    await execution.run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == expected
        assert task.quota_released is (expected == "failed")
        assert task.stop_confirmation["confirmed"] is confirmed
        assert task.backend_ref["local_cleanup"]["closed"] is closed
        quota = await bucket(session, "global", "all")
        assert quota.active_count == quota.target_sockets == (0 if expected == "failed" else 1)
        assert quota.reserved_bytes == (0 if expected == "failed" else task.result_reserved_bytes)
        instance = await session.get(DatabaseInstance, instance_id)
        assert instance.last_probe_status == ("failed" if kind == "probe" and expected == "failed" else "untested")
        events = list((await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == task_id))).scalars())
        assert any(event.event_type == ("finished" if expected == "failed" else "recovering") for event in events)


async def test_slow_probe_open_preserves_internal_timeout_cleanup_evidence(db_factory, seed, monkeypatch):
    adapter = OpeningFailure(local_closed=True, slow=True, cleanup_delay=.03)
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter, short_deadline=True)
    started = time.monotonic()
    await execution.run_claimed_task(db_factory, claim)
    assert time.monotonic()-started < 1
    assert adapter.closed, "outer timeout must allow the inner deadline cleanup to finish"
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "timed_out" and task.quota_released
        assert task.stop_confirmation["confirmed"] is False
        assert task.stop_reason == "execution_timeout"
        assert (await bucket(session, "global", "all")).target_sockets == 0


async def test_probe_cleanup_grace_is_bounded_and_unknown_close_keeps_quota(db_factory, seed, monkeypatch):
    adapter = OpeningFailure(local_closed=True, slow=True, cleanup_delay=10)
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter, short_deadline=True)
    started = time.monotonic()
    await asyncio.wait_for(execution.run_claimed_task(db_factory, claim), 2)
    assert time.monotonic()-started < 1.8
    assert not adapter.closed
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "recovering" and not task.quota_released
        assert task.backend_ref["local_cleanup"]["closed"] is False
        assert (await bucket(session, "global", "all")).target_sockets == 1


async def test_probe_timeout_after_handle_closes_locally_without_another_control_connection(db_factory, seed, monkeypatch):
    class SlowRun(FakeAdapter):
        async def run(self, handle):
            await asyncio.sleep(10)
        async def request_stop(self, *args, **kwargs):
            raise AssertionError("probe must not open another control connection after its I/O budget")
    adapter = SlowRun()
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter, short_deadline=True)
    await execution.run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "timed_out" and task.quota_released
        assert task.stop_confirmation["confirmed"] is False
        assert task.backend_ref["local_cleanup"]["closed"] is True


@pytest.mark.parametrize("hang", [False, True])
async def test_probe_failed_or_slow_close_stays_recovering_with_bounded_cleanup(db_factory, seed, monkeypatch, hang):
    allow_close = asyncio.Event()
    existing_cleanups = set(execution._probe_cleanups)
    class UnclosedProbe(FakeAdapter):
        async def run(self, handle):
            raise TimeoutError()
        async def close(self, handle):
            if hang:
                await allow_close.wait()
            raise OSError("close failed")
    adapter = UnclosedProbe()
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter)
    started = time.monotonic()
    await asyncio.wait_for(execution.run_claimed_task(db_factory, claim), 2)
    assert time.monotonic()-started < 1.8
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "recovering" and not task.quota_released
        assert task.backend_ref["local_cleanup"]["closed"] is False
        assert (await bucket(session, "global", "all")).target_sockets == 1
    pending_cleanups = set(execution._probe_cleanups)-existing_cleanups
    allow_close.set()
    if pending_cleanups:
        await asyncio.wait_for(asyncio.gather(*pending_cleanups), 2)


async def test_probe_user_cancel_after_handle_releases_only_confirmed_local_connections(db_factory, seed, monkeypatch):
    entered = asyncio.Event()
    class ActiveProbe(FakeAdapter):
        async def run(self, handle):
            entered.set()
            await asyncio.sleep(10)
    adapter = ActiveProbe()
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter)
    running = asyncio.create_task(execution.run_claimed_task(db_factory, claim))
    await asyncio.wait_for(entered.wait(), 1)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        task.cancel_requested_at = datetime.now()
        await session.commit()
    await asyncio.wait_for(running, 2)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "cancelled" and task.quota_released
        assert task.stop_confirmation["confirmed"] is False
        assert task.backend_ref["local_cleanup"]["closed"] is True
        assert (await bucket(session, "global", "all")).target_sockets == 0


async def test_closed_probe_never_releases_unconfirmed_control_quota(db_factory, seed, monkeypatch):
    adapter = OpeningFailure(local_closed=True)
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter)
    async with db_factory() as session:
        (await bucket(session, "control", task_id)).active_count = 1
        await session.commit()
    await execution.run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == "recovering" and task.error_code == "control_cleanup_unconfirmed"
        assert not task.quota_released
        assert (await bucket(session, "global", "all")).target_sockets == 1


@pytest.mark.parametrize("stage", ["open", "open_error", "run", "close"])
async def test_noncooperative_probe_io_cannot_block_executor_and_late_resources_are_closed(
    db_factory, seed, monkeypatch, stage):
    release, entered, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    existing_cleanups = set(execution._probe_cleanups)

    async def delayed():
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                pass

    class LateProbe(FakeAdapter):
        batches_started = 0
        async def open_operation(self, *args, **kwargs):
            if stage in ("open", "open_error"):
                await delayed()
                if stage == "open_error":
                    closed.set()
                    raise local_closed_error(TimeoutError())
            return await super().open_operation(*args, **kwargs)
        async def run(self, handle):
            self.ran += 1
            if stage == "run":
                await delayed()
            elif stage == "close":
                raise TimeoutError()
        async def batches(self, handle):
            self.batches_started += 1
            async for batch in super().batches(handle):
                yield batch
        async def close(self, handle):
            if stage == "close":
                await delayed()
            closed.set()

    adapter = LateProbe()
    monkeypatch.setattr(execution, "PROBE_CLEANUP_GRACE", .05)
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter, short_deadline=True)
    running = asyncio.create_task(execution.run_claimed_task(db_factory, claim))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        done, _ = await asyncio.wait({running}, timeout=.5)
        assert running in done, "probe cancellation must not hold the executor slot indefinitely"
        async with db_factory() as session:
            task = await session.get(DatabaseTask, task_id)
            assert task.status == "recovering" and not task.quota_released
            assert task.backend_ref["local_cleanup"]["closed"] is False
            assert (await bucket(session, "global", "all")).target_sockets == 1
        assert not closed.is_set()
    finally:
        release.set()
        await asyncio.wait_for(running, 2)
        cleanups = set(execution._probe_cleanups)-existing_cleanups
        if cleanups:
            await asyncio.wait_for(asyncio.gather(*cleanups), 2)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.quota_released
        assert (await bucket(session, "global", "all")).target_sockets == 0
    assert closed.is_set() and task.status == "timed_out"
    assert task.backend_ref["local_cleanup"]["closed"] is True
    assert task.stop_confirmation["confirmed"] is False
    assert adapter.ran == (0 if stage in ("open", "open_error") else 1)
    assert adapter.batches_started == 0, "cancelled runner must not advance to another target operation"


@pytest.mark.parametrize("control_active", [False, True])
async def test_probe_local_cleanup_after_lease_change_only_maintenance_can_settle(
    db_factory, seed, monkeypatch, control_active):
    adapter = OpeningFailure(local_closed=False)
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter)
    await execution.run_claimed_task(db_factory, claim)
    future_lease = datetime.now()+timedelta(seconds=30)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        task.lease_token, task.lease_expires_at = "new-owner-token", future_lease
        if control_active:
            (await bucket(session, "control", task_id)).active_count = 1
        await session.commit()
    assert await execution._record_local_cleanup(db_factory, claim, True)
    assert await execution._record_local_cleanup(db_factory, claim, False)
    assert not await execution._settle(db_factory, claim, "timed_out", None)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.lease_token == "new-owner-token" and task.lease_expires_at == future_lease
        assert task.backend_ref["local_cleanup"]["closed"] is True
        assert not task.quota_released
        task.stop_reason, task.lease_expires_at = "execution_timeout", datetime.now()-timedelta(seconds=1)
        await session.commit()

    async def no_target_connection(*args, **kwargs):
        raise AssertionError("closed probes must recover without opening target or control connections")
    monkeypatch.setattr(maintenance, "load_connection", no_target_connection)
    monkeypatch.setattr(maintenance, "stop_target", no_target_connection)
    await maintenance.run_maintenance_once(db_factory, "maintenance-owner")
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert task.status == ("recovering" if control_active else "timed_out")
        assert task.quota_released is not control_active
        assert task.stop_confirmation["confirmed"] is False
        assert (await bucket(session, "global", "all")).target_sockets == int(control_active)


async def test_late_probe_handle_close_cannot_settle_replacement_lease(db_factory, seed, monkeypatch):
    release, entered, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    existing_cleanups = set(execution._probe_cleanups)
    class LateHandle(FakeAdapter):
        async def open_operation(self, *args, **kwargs):
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass
            return await super().open_operation(*args, **kwargs)
        async def close(self, handle):
            closed.set()
    monkeypatch.setattr(execution, "PROBE_CLEANUP_GRACE", .05)
    adapter = LateHandle()
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter, short_deadline=True)
    running = asyncio.create_task(execution.run_claimed_task(db_factory, claim))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        done, _ = await asyncio.wait({running}, timeout=.5)
        assert running in done
        future_lease = datetime.now()+timedelta(seconds=30)
        async with db_factory() as session:
            task = await session.get(DatabaseTask, task_id)
            task.lease_token, task.lease_expires_at = "recovery-in-progress", future_lease
            await session.commit()
    finally:
        release.set()
        await asyncio.wait_for(running, 2)
        cleanups = set(execution._probe_cleanups)-existing_cleanups
        if cleanups:
            await asyncio.wait_for(asyncio.gather(*cleanups), 2)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert closed.is_set() and adapter.ran == 0
        assert task.backend_ref["local_cleanup"]["closed"] is True
        assert task.lease_token == "recovery-in-progress" and task.lease_expires_at == future_lease
        assert task.status == "recovering" and not task.quota_released
        assert (await bucket(session, "global", "all")).target_sockets == 1


async def test_probe_failure_before_open_task_still_records_recovering(db_factory, seed, monkeypatch):
    adapter = FakeAdapter()
    _, task_id, claim = await install_failure(db_factory, seed, monkeypatch, adapter)
    def cannot_start_watcher(*args):
        raise RuntimeError("synthetic task creation failure")
    monkeypatch.setattr(execution, "_watch", cannot_start_watcher)
    await execution.run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, task_id)
        assert adapter.opened == 0 and task.status == "recovering"
        assert task.backend_ref["local_cleanup"]["closed"] is False
        assert not task.quota_released
