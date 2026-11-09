"""连接探活不要求只读设置，业务查询仍保留只读事务。"""
from datetime import datetime
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database_adapters.base import ConnectionSpec, PreparedOperation
from app.database_adapters.mysql import MySQLAdapter
from app.database_adapters.postgresql import PostgreSQLAdapter, _connect
from app.models.database_management import DatabaseInstance, DatabaseTask
from app.schemas.database_management import ProbeTaskRequest
from app.services import database_task_service as tasks
from app.services.database_quota_service import finish_task
from test_database_core import identity, target


def operation(kind):
    return PreparedOperation(10, kind, {}, {"operation": "databases"}, "SELECT 1", "fixture", 1000, 5242880)


@pytest.mark.parametrize("kind", ["probe", "sql", "metadata"])
async def test_mysql_only_business_operations_initialize_read_only_transaction(kind):
    queries = []

    class Cursor:
        description = [("connected", 1)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, statement, *args):
            queries.append(statement)

        async def fetchone(self):
            return (42, "server-fixture")

    client = MagicMock()
    client.cursor.return_value = Cursor()
    adapter = MySQLAdapter()
    with patch("app.database_adapters.mysql._connect", AsyncMock(return_value=client)):
        handle = await adapter.open_operation(ConnectionSpec("mysql", "fixture", 3306, 1, "user", "secret"),
            operation(kind), deadline=time.monotonic() + 3)
        await adapter.run(handle)
    assert ("START TRANSACTION READ ONLY" in queries) is (kind != "probe")
    assert handle.backend_ref["connection_id"] == 42
    if kind == "probe":
        assert queries[-1].endswith("SELECT 1 AS connected")
        assert handle.summary == {"message": "连接和认证成功"}


@pytest.mark.parametrize("read_only", [False, True])
async def test_postgres_probe_connection_omits_read_only_startup_setting(read_only):
    spec = ConnectionSpec("postgresql", "fixture", 5432, 1, "user", "secret")
    connect = AsyncMock(return_value=MagicMock())
    with patch("app.database_adapters.postgresql.asyncpg.connect", connect):
        if read_only:
            await _connect(spec, "demo", "opspilot-fixture", time.monotonic() + 3)
        else:
            await _connect(spec, "demo", "opspilot-fixture", time.monotonic() + 3, read_only=False)
    settings = connect.call_args.kwargs["server_settings"]
    assert ("default_transaction_read_only" in settings) is read_only
    assert settings["application_name"] == "opspilot-fixture"
    assert int(settings["statement_timeout"]) > 0


@pytest.mark.parametrize("kind", ["probe", "sql", "metadata"])
async def test_postgres_probe_uses_plain_transaction_and_ignores_default_schema(kind):
    client = MagicMock()
    client.transaction.return_value.start = AsyncMock()
    client.execute = AsyncMock()

    async def identity_row(query, *args):
        if "inet_server_addr" in query:
            return {"address": "127.0.0.1", "port": 5432, "postmaster_started": datetime(2026, 1, 1)}
        return {"pid": 42, "backend_start": datetime(2026, 1, 1)}

    client.fetchrow = AsyncMock(side_effect=identity_row)
    statement = MagicMock()
    statement.get_attributes.return_value = [SimpleNamespace(name="connected", type=SimpleNamespace(name="int4"))]
    client.prepare = AsyncMock(return_value=statement)
    adapter = PostgreSQLAdapter()
    connect = AsyncMock(return_value=client)
    with patch("app.database_adapters.postgresql._connect", connect):
        handle = await adapter.open_operation(ConnectionSpec("postgresql", "fixture", 5432, 1, "user", "secret",
            {"default_schema": "business"}), operation(kind), deadline=time.monotonic() + 3)
        await adapter.run(handle)
    assert client.transaction.call_args.kwargs == ({} if kind == "probe" else {"readonly": True})
    assert connect.call_args.kwargs["read_only"] is (kind != "probe")
    queries = [call.args[0] for call in client.execute.call_args_list]
    assert any("search_path" in query for query in queries) is (kind != "probe")
    assert handle.backend_ref["pid"] == 42
    if kind == "probe":
        assert client.prepare.call_args.args[0].endswith("SELECT 1 AS connected")
        assert handle.summary == {"message": "连接和认证成功"}


async def test_probe_task_and_inventory_summary_only_describe_connection(db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    async with db_factory() as session:
        submitted = await tasks.submit_task(session, identity(seed), ProbeTaskRequest(kind="probe", instance_id=instance_id))
        task = await session.get(DatabaseTask, submitted.task_id)
        assert task.operation_summary == "连接状态检测"
        assert await finish_task(session, task, "succeeded")
        instance = await session.get(DatabaseInstance, instance_id)
        assert instance.last_probe_status == "succeeded"
        assert instance.last_probe_message == "连接检测成功"
