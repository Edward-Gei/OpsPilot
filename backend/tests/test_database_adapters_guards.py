"""读取前的协议护栏不能先分配巨型结果再做页面截断。"""
import asyncio
import struct
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.database_adapters.base import AdapterError, ConnectionSpec, connection_not_established
from app.database_adapters.postgresql import BoundedPostgresProtocol
from app.database_adapters.redis import BoundedRESP2Parser


class _Protocol:
    def __init__(self):
        self.received = bytearray()
    def data_received(self, data):
        self.received.extend(data)


class _Transport:
    def __init__(self):
        self.aborted = False
    def abort(self):
        self.aborted = True


class ProtocolGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_decimal256_does_not_use_python_default_precision(self):
        from decimal import Decimal
        from app.database_adapters.clickhouse import _decode_value
        value = Decimal("1234567890123456789012345678901234567890.50")
        self.assertEqual(_decode_value(value, "Decimal(76, 2)"), value)

    def test_secret_repr(self):
        self.assertNotIn("do-not-show", repr(ConnectionSpec("mysql", "db", 3306, 1, "ro", "do-not-show")))

    def test_only_explicit_connect_refusal_is_confirmed(self):
        import errno
        wrapped = RuntimeError("safe generic driver message")
        wrapped.__cause__ = OSError(errno.ECONNREFUSED, "fixture connection refused")
        error = connection_not_established(wrapped)
        self.assertTrue(error.completion.confirmed)
        self.assertIsNone(connection_not_established(TimeoutError()))
        self.assertIsNone(connection_not_established(OSError(errno.ECONNRESET, "fixture reset")))

    def test_postgres_rejects_frame_from_header(self):
        protocol, transport = _Protocol(), _Transport()
        guard = BoundedPostgresProtocol(protocol, transport, 65536)
        header = b"D" + struct.pack("!I", 100000000)
        guard.data_received(header[:2])
        guard.data_received(header[2:] + b"x" * 64)
        self.assertTrue(transport.aborted)
        self.assertIsNotNone(guard.violation)
        self.assertLessEqual(len(protocol.received), 5)

    async def test_redis_rejects_bulk_length_before_body_read(self):
        parser = BoundedRESP2Parser(65536)
        parser.limit_bytes = 65536
        parser._connected = True
        parser._stream = asyncio.StreamReader()
        parser._stream.feed_data(b"$100000000\r\n")
        with self.assertRaises(AdapterError):
            await parser.read_response()

    async def test_proxy_other_backend_absence_never_confirms_stop(self):
        from app.database_adapters import get_adapter
        import time

        class MysqlCursor:
            def __init__(self):
                self.query = ""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def execute(self, query, *args):
                self.query = query
            async def fetchone(self):
                return ("other-server",) if "@@server_uuid" in self.query else None
        mysql = MagicMock()
        mysql.cursor.return_value = MysqlCursor()
        postgres = MagicMock()
        postgres.transaction.return_value.start = AsyncMock()
        postgres.close = AsyncMock()
        async def pg_row(query, *args):
            if "inet_server_addr" in query:
                from datetime import datetime
                return {"address": "10.0.0.2", "port": 5432, "postmaster_started": datetime(2026, 1, 1)}
            return None
        postgres.fetchrow = AsyncMock(side_effect=pg_row)
        redis = MagicMock()
        redis.info = AsyncMock(return_value={"run_id": "other-server"})
        redis.client_list = AsyncMock(return_value=[])
        redis.aclose = AsyncMock()
        mongo = MagicMock()
        mongo.admin.command = AsyncMock(return_value={"topologyVersion": {"processId": "other-server"}})
        cursor = MagicMock()
        cursor.to_list = AsyncMock(return_value=[])
        mongo.admin.aggregate = AsyncMock(return_value=cursor)
        mongo.close = AsyncMock()
        fixtures = [
            ("mysql", "_connect", mysql, {"connection_id": 10, "marker": "opspilot:fixture", "server_uuid": "original-server"}),
            ("postgresql", "_connect", postgres, {"pid": 10, "marker": "opspilot:fixture", "backend_start": "2026-01-01T00:00:00", "server_identity": {"address": "10.0.0.1", "port": 5432, "postmaster_started": "2026-01-01T00:00:00"}}),
            ("redis", "_client", redis, {"client_id": 10, "marker": "opspilot:fixture", "server_run_id": "original-server"}),
            ("mongodb", "_client", mongo, {"comment": "opspilot:fixture", "server_process_id": "original-server"}),
        ]
        for engine, factory, client, reference in fixtures:
            with self.subTest(engine=engine):
                replacement = AsyncMock(return_value=client) if factory == "_connect" else MagicMock(return_value=client)
                with patch(f"app.database_adapters.{engine}.{factory}", replacement):
                    confirmation = await get_adapter(engine).request_stop(
                        ConnectionSpec(engine, "proxy", 1234, 1, "ro", "fixture"),
                        reference, reason="user_cancel", deadline=time.monotonic() + 1,
                    )
                self.assertFalse(confirmation.confirmed)
                self.assertEqual(confirmation.reason, "backend_identity_mismatch")

    async def test_clickhouse_other_node_absence_or_other_query_is_unknown(self):
        import httpx
        import json
        import time
        from app.database_adapters.clickhouse import ClickHouseAdapter
        for response_body in ("", json.dumps({"kill_status": "finished", "query_id": "other", "user": "ro"}) + "\n"):
            def respond(request):
                if request.content.startswith(b"SELECT query_id"):
                    return httpx.Response(200, content=b'["other","ro","QueryFinish",0]\n')
                if request.content.startswith(b"SELECT count"):
                    return httpx.Response(200, content=b"0\n")
                return httpx.Response(200, content=response_body.encode())
            client = httpx.AsyncClient(base_url="http://fixture", transport=httpx.MockTransport(respond))
            with patch("app.database_adapters.clickhouse._client", return_value=client):
                confirmation = await ClickHouseAdapter().request_stop(
                    ConnectionSpec("clickhouse", "proxy", 8123, 1, "ro", "fixture"),
                    {"query_id": "opspilot-unique-original"}, reason="user_cancel", deadline=time.monotonic() + 1,
                )
            self.assertFalse(confirmation.confirmed)


if __name__ == "__main__":
    unittest.main()
