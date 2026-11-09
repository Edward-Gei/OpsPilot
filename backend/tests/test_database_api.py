"""API 结果隔离和台账并发边界的定向验收。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from conftest import auth_header, login_for_tokens
from app.core.security import encrypt_text
from app.models.auth import Permission, Role, RolePermission, UserRole
from app.models.database_management import (DatabaseAuditEvent, DatabaseInstance, DatabaseRoleInstance,
    DatabaseQuotaBucket, DatabaseTask, DatabaseResult)
from app.models.job import Credential
from app.schemas.database_management import ActorContext
from app.services import database_task_service as tasks
from app.services.database_quota_service import acquire_control, bucket, claim_next_task, release_control
from app.services.database_maintenance_service import run_maintenance_once
from app.services.database_audit_service import list_audit, audit_detail
from app.services.database_execution_service import run_claimed_task
from test_database_core import target, submit, identity, FakeAdapter


async def test_api_rejection_commits_evidence_and_admin_cannot_read_other_owner(client, db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.post("/api/v1/database/tasks", headers=headers,
        json={"kind": "sql", "instance_id": instance_id, "sql": "SELECT 1; UPDATE t SET x=1"})
    assert response.status_code == 422
    task_id = response.json()["data"]["task_id"]
    async with db_factory() as session:
        assert (await session.get(DatabaseTask, task_id)).status == "rejected"
        assert (await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == task_id))).scalar_one().terminal_at
        task = await session.get(DatabaseTask, task_id)
        task.owner_id = seed["users"]["ops1"]
        await session.commit()
    assert (await client.get(f"/api/v1/database/tasks/{task_id}", headers=headers)).status_code == 403
    assert (await client.get(f"/api/v1/database/tasks/{task_id}/result", headers=headers)).status_code == 403
    audit = await client.get(f"/api/v1/database/audit/logs/{task_id}", headers=headers)
    assert audit.status_code == 200
    assert "rows" not in audit.json()["data"] and "payload_enc" not in str(audit.json())


async def test_row_probe_requires_write_but_not_query_scope(client, db_factory, seed):
    instance_id, _ = await target(db_factory, seed, grant=False)
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.post("/api/v1/database/tasks", headers=headers,
        json={"kind": "probe", "instance_id": instance_id})
    assert response.status_code == 202
    assert response.json()["data"]["status"] == "queued"
    assert (await client.post("/api/v1/database/tasks", headers=headers,
        json={"kind": "probe", "instance_id": instance_id, "sql": "SELECT secret FROM t"})).status_code == 422


async def test_cold_instance_claimed_beyond_arbitrary_hot_queue_prefix(db_factory, seed, monkeypatch):
    instance_id, cred_id = await target(db_factory, seed)
    first = await submit(db_factory, seed, instance_id)
    async with db_factory() as session:
        original = await session.get(DatabaseTask, first.task_id)
        payload = {c.name: getattr(original, c.name) for c in DatabaseTask.__table__.columns if c.name not in ("id", "created_at", "updated_at")}
        for _ in range(55):
            session.add(DatabaseTask(**payload))
        cold = DatabaseInstance(name="cold", engine="mysql", environment="demo", host="cold", port=3306,
                                credential_id=cred_id, connection_options={})
        session.add(cold)
        await session.flush()
        cold_task = DatabaseTask(**{**payload, "instance_id": cold.id})
        session.add(cold_task)
        await session.flush()
        cold_id = cold_task.id
        (await bucket(session, "instance", instance_id)).active_count = 5
        await session.commit()
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: FakeAdapter())
    claim = await claim_next_task(db_factory, "executor-test")
    assert claim.task_id == cold_id


async def test_crashed_control_lease_keeps_accounting_and_audit(db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    token = await acquire_control(db_factory, instance_id, 3, task_id=submission.task_id, owner="dead-executor")
    assert token
    async with db_factory() as session:
        control = await bucket(session, "control", submission.task_id)
        control.lease_expires_at = datetime.now()-timedelta(seconds=1)
        await session.commit()
    await run_maintenance_once(db_factory, "maintenance-a")
    async with db_factory() as session:
        control = await bucket(session, "control", submission.task_id)
        assert control.owner == "recovering:dead-executor" and control.active_count == 1
        assert (await bucket(session, "global", "all")).control_sockets == 3
        event = (await session.execute(select(DatabaseAuditEvent).where(
            DatabaseAuditEvent.task_id == submission.task_id, DatabaseAuditEvent.event_type == "control_recovering"))).scalar_one()
        assert event.validation["confirmed"] is False
    assert not await release_control(db_factory, instance_id, 3, task_id=submission.task_id, token="old-token")
    assert not await acquire_control(db_factory, instance_id, 3, task_id=submission.task_id)


async def test_completed_result_expiry_remains_410_after_physical_cleanup(db_factory, seed, monkeypatch):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    adapter = FakeAdapter()
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr("app.services.database_execution_service.get_adapter", lambda _: adapter)
    claim = await claim_next_task(db_factory, "executor-a")
    await run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        result = (await session.execute(select(DatabaseResult).where(DatabaseResult.task_id == submission.task_id))).scalar_one()
        result.expires_at = datetime.now()-timedelta(minutes=1)
        task = await session.get(DatabaseTask, submission.task_id)
        task.result_summary = {**task.result_summary, "expires_at": result.expires_at.isoformat()}
        await session.commit()
    await run_maintenance_once(db_factory, "maintenance-a")
    async with db_factory() as session:
        from app.services.database_result_service import read_result_page
        from app.core.response import BizError
        task = await session.get(DatabaseTask, submission.task_id)
        assert (await session.execute(select(DatabaseResult).where(DatabaseResult.task_id == task.id))).scalar_one_or_none() is None
        with pytest.raises(BizError) as error:
            await read_result_page(session, identity(seed), task, 1, 50)
        assert error.value.http_status == 410
        assert (await bucket(session, "global", "all")).payload_bytes == 0


async def test_history_deletion_does_not_delete_independent_audit(db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id, "DELETE FROM t")
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        task.finished_at = datetime.now()-timedelta(days=31)
        await session.commit()
    await run_maintenance_once(db_factory, "maintenance-a")
    async with db_factory() as session:
        assert await session.get(DatabaseTask, submission.task_id) is None
        page = await list_audit(session)
        assert page["total"] == 1
        assert (await audit_detail(session, submission.task_id))["submitted_text"] == "DELETE FROM t"
