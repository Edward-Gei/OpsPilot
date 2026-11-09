"""驱动停止事实和控制连接关闭事实必须独立传递。"""
from datetime import datetime
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.database_adapters import get_adapter
from app.database_adapters.base import AdapterError, ConnectionSpec


@pytest.mark.parametrize("engine", ["mysql", "postgresql", "redis", "mongodb", "clickhouse"])
@pytest.mark.parametrize("fail_close", [False, True])
async def test_control_cleanup_preserves_target_evidence(engine, fail_close, monkeypatch):
    class MysqlCursor:
        query = ""
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def execute(self, query, *args):
            self.query = query
        async def fetchone(self):
            return ("original",) if "@@server_uuid" in self.query else None
    mysql = MagicMock()
    mysql.cursor.return_value = MysqlCursor()
    postgres = MagicMock()
    postgres.transaction.return_value.start = AsyncMock()
    async def pg_row(query, *args):
        return {"address": "10.0.0.1", "port": 5432, "postmaster_started": datetime(2026, 1, 1)} if "inet_server_addr" in query else None
    postgres.fetchrow = AsyncMock(side_effect=pg_row)
    postgres.close = AsyncMock()
    redis = MagicMock()
    redis.info = AsyncMock(return_value={"run_id": "original"})
    redis.client_list = AsyncMock(return_value=[])
    redis.aclose = AsyncMock()
    mongo = MagicMock()
    mongo.admin.command = AsyncMock(return_value={"topologyVersion": {"processId": "original"}})
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=[])
    mongo.admin.aggregate = AsyncMock(return_value=cursor)
    mongo.close = AsyncMock()
    clickhouse = httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(lambda request:
        httpx.Response(200, content=b'{"query_id":"opspilot-original","user":"ro","kill_status":"finished"}\n')))
    fixtures = {
        "mysql": ("_connect", mysql, {"connection_id": 10, "marker": "opspilot:fixture", "server_uuid": "original"}, "close"),
        "postgresql": ("_connect", postgres, {"pid": 10, "marker": "opspilot:fixture", "backend_start": "2026-01-01T00:00:00", "server_identity": {"address": "10.0.0.1", "port": 5432, "postmaster_started": "2026-01-01T00:00:00"}}, "close"),
        "redis": ("_client", redis, {"client_id": 10, "marker": "opspilot:fixture", "server_run_id": "original"}, "aclose"),
        "mongodb": ("_client", mongo, {"comment": "opspilot:fixture", "server_process_id": "original"}, "close"),
        "clickhouse": ("_client", clickhouse, {"query_id": "opspilot-original"}, "aclose"),
    }
    factory, client, ref, close_name = fixtures[engine]
    if fail_close:
        close = MagicMock(side_effect=OSError("synthetic close failed")) if engine == "mysql" else AsyncMock(side_effect=OSError("synthetic close failed"))
        monkeypatch.setattr(client, close_name, close)
    replacement = AsyncMock(return_value=client) if factory == "_connect" else MagicMock(return_value=client)
    monkeypatch.setattr(f"app.database_adapters.{engine}.{factory}", replacement)
    try:
        if fail_close:
            with pytest.raises(AdapterError) as raised:
                await get_adapter(engine).request_stop(ConnectionSpec(engine, "fixture", 1234, 1, "ro", "synthetic"),
                    ref, reason="user_cancel", deadline=time.monotonic()+2)
            assert raised.value.code == "control_cleanup_unconfirmed"
            assert raised.value.completion.confirmed is True
            assert raised.value.completion.evidence.get("control_closed") is not True
        else:
            confirmation = await get_adapter(engine).request_stop(ConnectionSpec(engine, "fixture", 1234, 1, "ro", "synthetic"),
                ref, reason="user_cancel", deadline=time.monotonic()+2)
            assert confirmation.confirmed and confirmation.evidence.get("control_closed") is True
    finally:
        # MockTransport未建立网络；失败替身也须释放其测试客户端。
        await httpx.AsyncClient.aclose(clickhouse)
