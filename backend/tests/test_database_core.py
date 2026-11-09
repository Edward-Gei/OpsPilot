"""数据库任务权限、拒绝留痕、结果与恢复状态回归。"""
from datetime import datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from app.core.response import BizError
from app.core.security import encrypt_text, decrypt_text
from app.database_adapters.base import ResultColumn, RowBatch, RunningOperation, StopConfirmation
from app.models.auth import Permission, RolePermission
from app.models.database_management import (DatabaseAuditEvent, DatabaseInstance, DatabaseInstanceApplication,
    DatabaseRoleInstance, DatabaseTask, DatabaseQuotaBucket, DatabaseResult)
from app.models.job import Credential
from app.schemas.database_management import ActorContext, SqlTaskRequest, ProbeTaskRequest, InstanceWrite
from app.services import database_task_service as tasks
from app.services import database_instance_service as instances
from app.services.database_quota_service import bucket, claim_next_task
from app.services.database_result_service import ResultBuilder, canonical, read_result_page
from app.services.database_execution_service import run_claimed_task, _settle


async def target(db_factory, seed, *, grant=True):
    async with db_factory() as session:
        cred = Credential(name="db-readonly", auth_type="username_password", login_user="readonly",
                          secret_enc=encrypt_text("synthetic-password"))
        session.add(cred)
        await session.flush()
        row = DatabaseInstance(name="db-demo", engine="mysql", environment="demo", host="mysql-target", port=3306,
            credential_id=cred.id, connection_options={"default_database": "demo"})
        session.add(row)
        await session.flush()
        if grant:
            session.add(DatabaseRoleInstance(instance_id=row.id, role_id=seed["roles"]["admin"]))
        await session.commit()
        return row.id, cred.id


def identity(seed, name="admin"):
    return ActorContext(seed["users"][name], name, "127.0.0.1")


async def submit(db_factory, seed, instance_id, sql="SELECT 1"):
    async with db_factory() as session:
        return await tasks.submit_task(session, identity(seed), SqlTaskRequest(kind="sql", instance_id=instance_id, sql=sql))


async def test_admin_still_requires_explicit_instance_grant(db_factory, seed):
    instance_id, _ = await target(db_factory, seed, grant=False)
    async with db_factory() as session:
        assert (await instances.list_instances(session, seed["users"]["admin"], scope="management"))["total"] == 1
        assert (await instances.list_instances(session, seed["users"]["admin"], scope="query"))["total"] == 0
    with pytest.raises(BizError) as error:
        await submit(db_factory, seed, instance_id)
    assert error.value.http_status == 403
    async with db_factory() as session:
        event = (await session.execute(select(DatabaseAuditEvent))).scalar_one()
        assert event.instance_name is None and event.target_snapshot.get("host") is None
        assert event.validation["executed"] is False


async def test_last_select_and_rejected_input_remain_encrypted_and_audited(db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    accepted = await submit(db_factory, seed, instance_id, "SELECT 1; SELECT 'secret;literal' AS message;")
    assert accepted.status == "queued" and "secret;literal" in accepted.executed_statement
    rejected = await submit(db_factory, seed, instance_id, "SELECT 1; DELETE FROM accounts")
    assert rejected.http_status == 422 and rejected.status == "rejected"
    huge = await submit(db_factory, seed, instance_id, "SELECT '"+"中"*24000+"'")
    assert huge.status == "rejected"
    async with db_factory() as session:
        task = await session.get(DatabaseTask, accepted.task_id)
        assert "secret;literal" not in task.submitted_text_enc
        assert decrypt_text(task.submitted_text_enc).startswith("SELECT 1;")
        huge_task = await session.get(DatabaseTask, huge.task_id)
        assert huge_task.submitted_text_enc is None and huge_task.submitted_bytes > 65536
        events = list((await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == rejected.task_id))).scalars())
        assert len(events) == 1 and events[0].validation["executed"] is False
        global_row = await bucket(session, "global", "all")
        assert global_row.queued_count == 1


async def test_queued_cancel_is_idempotent_and_never_releases_another_task(db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    first = await submit(db_factory, seed, instance_id)
    second = await submit(db_factory, seed, instance_id)
    async with db_factory() as session:
        await tasks.request_cancel(session, identity(seed), first.task_id)
        await session.commit()
        await tasks.request_cancel(session, identity(seed), first.task_id)
        await session.commit()
        assert (await bucket(session, "global", "all")).queued_count == 1
        assert (await session.get(DatabaseTask, second.task_id)).status == "queued"
        assert (await session.get(DatabaseTask, first.task_id)).stop_confirmation["reason"] == "not_started"


async def test_metadata_edit_does_not_change_connection_revision(db_factory, seed):
    instance_id, cred_id = await target(db_factory, seed)
    async with db_factory() as session:
        req = InstanceWrite(name="db-demo", engine="mysql", host="mysql-target", port=3306,
            credential_id=cred_id, connection_options={"default_database": "demo"}, cluster_name="cluster-a")
        # 首次规范化TLS默认选项属于实际连接选项变化，后续只改登记字段不变。
        await instances.write_instance(session, seed["users"]["admin"], req, instance_id)
        await session.commit()
        revision = (await session.get(DatabaseInstance, instance_id)).config_revision
        req.cluster_name = "cluster-b"
        await instances.write_instance(session, seed["users"]["admin"], req, instance_id)
        assert (await session.get(DatabaseInstance, instance_id)).config_revision == revision
        req.host = "new-target"
        await instances.write_instance(session, seed["users"]["admin"], req, instance_id)
        assert (await session.get(DatabaseInstance, instance_id)).config_revision == revision+1


def test_canonical_byte_count_duplicate_names_and_lossless_values():
    builder = ResultBuilder(2, 65536)
    builder.set_columns([ResultColumn(0, "x", "int"), ResultColumn(1, "x", "decimal")])
    assert builder.add([9007199254740993, Decimal("1.00000000000001")])
    assert builder.add(["中文", b"\x00\xff"])
    assert not builder.add([1, 2])
    assert builder.returned_bytes == len(canonical({"columns": builder.columns, "rows": builder.rows}).encode())
    assert builder.rows[0][0] == {"$type": "int", "value": "9007199254740993"}
    assert [c["name"] for c in builder.columns] == ["x", "x"]


class FakeAdapter:
    target_connection_cost = control_connection_cost = 1
    def __init__(self, *, confirmed=True):
        self.confirmed = confirmed
        self.opened = self.ran = self.stopped = 0
    async def open_operation(self, connection, prepared, *, deadline):
        self.opened += 1
        return RunningOperation({"test": "identity"}, columns=[ResultColumn(0, "x", "int")])
    async def run(self, handle):
        self.ran += 1
        handle.backend_ref["actual_node"] = "synthetic-node-1"
    async def batches(self, handle):
        yield RowBatch(handle.columns, [[1], [2]])
        handle.completion = StopConfirmation(self.confirmed, "eof" if self.confirmed else "unknown", handle.backend_ref)
    async def request_stop(self, connection, backend_ref, *, reason, deadline):
        self.stopped += 1
        return StopConfirmation(self.confirmed, "verified" if self.confirmed else "unknown", backend_ref)
    async def close(self, handle):
        pass


async def test_executor_publishes_complete_result_and_revoke_blocks_read(db_factory, seed, monkeypatch):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    adapter = FakeAdapter()
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr("app.services.database_execution_service.get_adapter", lambda _: adapter)
    claim = await claim_next_task(db_factory, "executor-a")
    await run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        assert task.status == "succeeded" and task.quota_released
        assert task.backend_ref["actual_node"] == "synthetic-node-1"
        result = await read_result_page(session, identity(seed), task, 1, 50)
        assert result["rows"] == [[1], [2]]
        assert (await bucket(session, "global", "all")).active_count == 0
        assert (await bucket(session, "global", "all")).payload_bytes > 0
        await session.execute(delete(DatabaseRoleInstance).where(DatabaseRoleInstance.instance_id == instance_id))
        await session.commit()
        with pytest.raises(BizError) as error:
            await read_result_page(session, identity(seed), task, 1, 50)
        assert error.value.http_status == 403
    assert adapter.opened == adapter.ran == 1


async def test_unknown_stop_retains_quota_and_old_token_cannot_settle(db_factory, seed, monkeypatch):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    adapter = FakeAdapter(confirmed=False)
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr("app.services.database_execution_service.get_adapter", lambda _: adapter)
    claim = await claim_next_task(db_factory, "executor-a")
    await run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        assert task.status == "recovering" and not task.quota_released
        assert (await bucket(session, "global", "all")).active_count == 1
        task.lease_token = "new-token"
        await session.commit()
    assert not await _settle(db_factory, claim, "failed", StopConfirmation(True, "late", {}))
    async with db_factory() as session:
        assert (await session.get(DatabaseTask, submission.task_id)).status == "recovering"
        assert (await bucket(session, "global", "all")).active_count == 1


async def test_execution_rechecks_grant_before_target_access(db_factory, seed, monkeypatch):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    adapter = FakeAdapter()
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr("app.services.database_execution_service.get_adapter", lambda _: adapter)
    claim = await claim_next_task(db_factory, "executor-a")
    async with db_factory() as session:
        await session.execute(delete(DatabaseRoleInstance).where(DatabaseRoleInstance.instance_id == instance_id))
        await session.commit()
    await run_claimed_task(db_factory, claim)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        assert task.status == "rejected" and task.target_attempted_at is None
        assert (await bucket(session, "global", "all")).active_count == 0
    assert adapter.opened == 0
