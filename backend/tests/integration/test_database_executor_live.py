"""独立平台库 + 真实目标：取消、总时限、失联恢复和认证失败。"""
import asyncio
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import time

import asyncmy
import asyncpg
import pytest
from sqlalchemy import select

from app.core.security import encrypt_text
from app.models.auth import Permission, Role, RolePermission, User, UserRole
from app.models.database_management import DatabaseAuditEvent, DatabaseInstance, DatabaseRoleInstance, DatabaseTask
from app.models.job import Credential
from app.schemas.database_management import ActorContext, SqlTaskRequest
from app.services import config_service
from app.services.database_task_service import submit_task, request_cancel
from app.services.database_quota_service import bucket, claim_next_task
from app.services.database_execution_service import run_claimed_task, _settle
from app.services.database_maintenance_service import run_maintenance_once
from app.database_adapters.base import StopConfirmation
from test_database_coordination import real_coordination

pytestmark = pytest.mark.integration
SLOW_SQL = "SELECT SUM(a.id*b.id*c.id*d.id*e.id) FROM orders a CROSS JOIN orders b CROSS JOIN orders c CROSS JOIN orders d CROSS JOIN orders e"


async def prepare(factory, engine, *, timeout=60, bad_password=False):
    path = Path(os.environ.get("DATABASE_TEST_TARGETS_FILE", "/fixtures/targets.json"))
    if not path.exists():
        pytest.fail("real five-engine fixtures are required")
    spec = json.loads(path.read_text())[engine]
    async with factory() as session:
        permission = Permission(code="database:query", name="query", module="database")
        role = Role(code="db-reader", name="db-reader", is_builtin=False)
        user = User(username="core-live", display_name="core-live", source="local", status="active", must_change_password=False)
        session.add_all([permission, role, user])
        await session.flush()
        session.add_all([RolePermission(role_id=role.id, permission_id=permission.id), UserRole(user_id=user.id, role_id=role.id)])
        credential = Credential(name="synthetic-live", auth_type="username_password", login_user=spec["login_user"],
            secret_enc=encrypt_text("definitely-invalid-synthetic" if bad_password else spec["secret"]))
        session.add(credential)
        await session.flush()
        instance = DatabaseInstance(name="live-target", engine=engine, environment="demo", host=spec["host"],
            port=spec["port"], credential_id=credential.id, connection_options=spec["options"])
        session.add(instance)
        await session.flush()
        session.add(DatabaseRoleInstance(instance_id=instance.id, role_id=role.id))
        await config_service.set_config(session, "database.execution_timeout_seconds", timeout, user.id)
        await session.commit()
        return ActorContext(user.id, user.username), instance.id, spec


async def marked_query_active(engine, spec, task_id):
    if engine == "mysql":
        connection = await asyncmy.connect(host=spec["host"], port=spec["port"], user=spec["login_user"], password=spec["secret"])
        try:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE INFO LIKE %s AND COMMAND='Query'",
                    (f"/*opspilot:{task_id}:%",))
                return (await cursor.fetchone())[0] > 0
        finally:
            connection.close()
    connection = await asyncpg.connect(host=spec["host"], port=spec["port"], user=spec["login_user"], password=spec["secret"], database="acceptance", ssl=False)
    try:
        return await connection.fetchval("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE $1 AND state='active'", f"opspilot:{task_id}:%") > 0
    finally:
        await connection.close()


async def wait_active(engine, spec, task_id, factory):
    deadline = time.monotonic()+8
    while time.monotonic() < deadline:
        if await marked_query_active(engine, spec, task_id):
            return
        await asyncio.sleep(.05)
    async with factory() as session:
        task = await session.get(DatabaseTask, task_id)
        state = (task.status, task.error_code, task.stop_confirmation)
    raise AssertionError(f"real user query never appeared active on target: {state}")


@pytest.mark.parametrize("engine", ["mysql", "postgresql"])
async def test_real_core_cancel_and_quota_release(real_coordination, engine):
    api, executor, _ = real_coordination
    actor, instance_id, spec = await prepare(api, engine)
    async with api() as session:
        submitted = await submit_task(session, actor, SqlTaskRequest(kind="sql", instance_id=instance_id,
            context={"database": "acceptance"}, sql=SLOW_SQL))
    assert submitted.status == "queued"
    claim = await claim_next_task(executor, "live-executor")
    running = asyncio.create_task(run_claimed_task(executor, claim))
    try:
        await wait_active(engine, spec, submitted.task_id, api)
        async with api() as session:
            result = await request_cancel(session, actor, submitted.task_id)
            await session.commit()
            assert result["status"] == "stopping"
        await asyncio.wait_for(running, 15)
        async with api() as session:
            task = await session.get(DatabaseTask, submitted.task_id)
            assert task.status == "cancelled", (task.status, task.error_code)
            assert task.stop_confirmation["confirmed"] and task.quota_released
            assert (await bucket(session, "global", "all")).active_count == 0
            assert (await bucket(session, "global", "all")).control_operations == 0
        assert not await marked_query_active(engine, spec, submitted.task_id)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("engine", ["mysql", "postgresql"])
async def test_real_core_one_second_timeout(real_coordination, engine):
    api, executor, _ = real_coordination
    actor, instance_id, spec = await prepare(api, engine, timeout=1)
    async with api() as session:
        submitted = await submit_task(session, actor, SqlTaskRequest(kind="sql", instance_id=instance_id,
            context={"database": "acceptance"}, sql=SLOW_SQL))
    claim = await claim_next_task(executor, "timeout-executor")
    started = time.monotonic()
    await asyncio.wait_for(run_claimed_task(executor, claim), 15)
    assert time.monotonic()-started < 10
    async with api() as session:
        task = await session.get(DatabaseTask, submitted.task_id)
        assert task.status == "timed_out", (task.status, task.error_code, task.stop_confirmation)
        assert task.stop_reason == "execution_timeout" and task.stop_confirmation["confirmed"]
        assert (await bucket(session, "global", "all")).active_count == 0
    assert not await marked_query_active(engine, spec, submitted.task_id)


async def test_real_core_lost_lease_cancels_original_without_replay(real_coordination):
    api, old_executor, new_executor = real_coordination
    actor, instance_id, spec = await prepare(api, "mysql")
    async with api() as session:
        submitted = await submit_task(session, actor, SqlTaskRequest(kind="sql", instance_id=instance_id,
            context={"database": "acceptance"}, sql=SLOW_SQL))
    old_claim = await claim_next_task(old_executor, "old-executor")
    running = asyncio.create_task(run_claimed_task(old_executor, old_claim))
    try:
        await wait_active("mysql", spec, submitted.task_id, api)
        async with api() as session:
            task = await session.get(DatabaseTask, submitted.task_id)
            task.lease_expires_at = datetime.now()-timedelta(seconds=1)
            await session.commit()
        await run_maintenance_once(new_executor, "new-executor")
        await asyncio.wait_for(running, 10)
        async with api() as session:
            task = await session.get(DatabaseTask, submitted.task_id)
            assert task.backend_ref["local_cleanup"]["closed"] is True
            # 新持有者证明目标结束后，仍需原执行器实际关闭本地连接才能结算。
            # 关闭事实会唤醒下一轮维护，不允许旧task token直接发布终态。
            if task.status == "recovering":
                assert not task.quota_released
                task.lease_expires_at = datetime.now()-timedelta(seconds=1)
                await session.commit()
        await run_maintenance_once(new_executor, "new-executor")
        async with api() as session:
            task = await session.get(DatabaseTask, submitted.task_id)
            assert task.status == "failed" and task.quota_released
            assert task.lease_token != old_claim.lease_token
            attempts = list((await session.execute(select(DatabaseAuditEvent).where(
                DatabaseAuditEvent.task_id == task.id, DatabaseAuditEvent.event_type == "target_attempted"))).scalars())
            assert len(attempts) == 1
            assert (await bucket(session, "global", "all")).active_count == 0
        assert not await _settle(old_executor, old_claim, "failed", StopConfirmation(True, "old-late", {}))
        assert not await marked_query_active("mysql", spec, submitted.task_id)
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_real_core_authentication_rejection_is_failed_not_recovering(real_coordination):
    api, executor, _ = real_coordination
    actor, instance_id, _ = await prepare(api, "mysql", bad_password=True)
    async with api() as session:
        submitted = await submit_task(session, actor, SqlTaskRequest(kind="sql", instance_id=instance_id,
            context={"database": "acceptance"}, sql="SELECT 1"))
    claim = await claim_next_task(executor, "auth-failed-executor")
    await run_claimed_task(executor, claim)
    async with api() as session:
        task = await session.get(DatabaseTask, submitted.task_id)
        assert task.status == "failed" and task.error_code == "target_authentication_failed"
        assert task.stop_confirmation["confirmed"] and task.quota_released
        assert (await bucket(session, "global", "all")).active_count == 0
