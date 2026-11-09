"""隔离五引擎真实门禁；DATABASE_TEST_TARGETS_FILE 指向合成连接夹具。"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import unittest

from app.database_adapters import get_adapter
from app.database_adapters.base import AdapterError, ConnectionSpec, PreparedOperation
from app.database_sql_policy import validate_sql

_TARGETS = Path(os.environ.get("DATABASE_TEST_TARGETS_FILE", "/fixtures/targets.json"))


@unittest.skipUnless(_TARGETS.exists(), "需要独立五引擎合成目标夹具")
class LiveAdapterTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.targets = json.loads(_TARGETS.read_text(encoding="utf-8"))

    def spec(self, engine):
        return ConnectionSpec(engine=engine, credential_id=1, **self.targets[engine])

    async def collect(self, engine, kind, context=None, parameters=None, sql=None, max_bytes=5242880):
        adapter = get_adapter(engine)
        prepared = PreparedOperation(100, kind, context or {}, parameters or {}, sql, "synthetic acceptance", 1000, max_bytes)
        handle = await adapter.open_operation(self.spec(engine), prepared, deadline=time.monotonic() + 15)
        rows = []
        try:
            await adapter.run(handle)
            async for batch in adapter.batches(handle):
                rows.extend(batch.rows)
            self.assertIsNotNone(handle.completion)
            self.assertTrue(handle.completion.confirmed)
            return handle.columns, rows
        finally:
            await adapter.close(handle)

    async def test_five_engine_probe(self):
        for engine in self.targets:
            with self.subTest(engine=engine):
                columns, rows = await self.collect(engine, "probe")
                self.assertEqual(columns[0].name, "connected")
                self.assertEqual(rows[0][0], 1)

    async def test_last_select_only_and_duplicate_columns(self):
        for engine in ("mysql", "postgresql", "clickhouse"):
            with self.subTest(engine=engine):
                query = "SELECT id,id,amount FROM orders ORDER BY id LIMIT 3" if engine == "clickhouse" else "SELECT id AS same,customer AS same,amount FROM orders ORDER BY id LIMIT 3"
                validated = validate_sql(engine, "SELECT 999 AS ignored;" + query)
                columns, rows = await self.collect(engine, "sql", {"database": "acceptance"}, sql=validated.executed_statement)
                self.assertEqual(columns[0].name, columns[1].name)
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[0][0], 1)
                self.assertEqual(str(rows[0][2]), "19.50")

    async def test_metadata(self):
        for engine in ("mysql", "postgresql", "clickhouse"):
            with self.subTest(engine=engine):
                _, rows = await self.collect(engine, "metadata", {"database": "acceptance", "schema": "public"}, {"operation": "tables"})
                self.assertIn("orders", [row[0] for row in rows])
        _, rows = await self.collect("mongodb", "metadata", {"database": "acceptance"}, {"operation": "collections"})
        self.assertIn("orders", [row[0] for row in rows])

    async def test_mongo_controlled_forms(self):
        context = {"database": "acceptance", "collection": "orders"}
        _, rows = await self.collect("mongodb", "mongo_read", context, {"type": "find", "filter": {"_id": {"$lte": 3}}, "limit": 3})
        self.assertEqual(len(rows), 3)
        _, rows = await self.collect("mongodb", "mongo_read", context, {"type": "count_documents", "filter": {}})
        self.assertEqual(rows[0][0], 150)
        _, rows = await self.collect("mongodb", "mongo_read", context, {"type": "distinct", "field": "status", "filter": {}, "limit": 10})
        self.assertEqual({row[0] for row in rows}, {"completed", "pending"})

    async def test_redis_forms_and_cursor_binding(self):
        _, rows = await self.collect("redis", "redis_read", {"database": 0}, {"type": "scan", "pattern": "acceptance:order:*", "count": 2})
        token = rows[0][-1]
        if token != "0":
            _, next_rows = await self.collect("redis", "redis_read", {"database": 0}, {"type": "scan", "pattern": "acceptance:order:*", "count": 2, "cursor": token})
            self.assertTrue(next_rows)
            with self.assertRaises(AdapterError):
                await self.collect("redis", "redis_read", {"database": 0}, {"type": "scan", "pattern": "other:*", "count": 2, "cursor": token})
        for operation in (
            {"type": "string_preview", "key": "acceptance:order:001", "length": 100},
            {"type": "hash_page", "key": "acceptance:metrics", "count": 10},
            {"type": "list_page", "key": "acceptance:queue", "count": 10},
            {"type": "set_page", "key": "acceptance:tags", "count": 10},
            {"type": "zset_page", "key": "acceptance:ranking", "count": 10},
            {"type": "length", "key": "acceptance:metrics"},
        ):
            with self.subTest(operation=operation["type"]):
                _, values = await self.collect("redis", "redis_read", {"database": 0}, operation)
                self.assertTrue(values)

    async def test_real_sql_packet_guards(self):
        for engine in ("mysql", "postgresql", "clickhouse"):
            with self.subTest(engine=engine), self.assertRaises(AdapterError) as caught:
                query = "SELECT REPEAT('xxxxxxxxxxxxxxxx',524288) AS huge" if engine == "clickhouse" else "SELECT REPEAT('x',8388608) AS huge"
                await self.collect(engine, "sql", {"database": "acceptance"}, sql=query, max_bytes=65536)
            self.assertEqual(caught.exception.code, "protocol_value_too_large", engine)

    async def test_mysql_postgres_cancellation(self):
        for engine in ("mysql", "postgresql"):
            with self.subTest(engine=engine):
                adapter = get_adapter(engine)
                # COUNT 的笛卡尔积可被 MySQL 快速求值；聚合实际行值保证测试真实取消执行。
                query = "SELECT SUM(CAST(a.id AS BIGINT)*b.id*c.id*d.id*e.id) FROM orders a CROSS JOIN orders b CROSS JOIN orders c CROSS JOIN orders d CROSS JOIN orders e"
                prepared = PreparedOperation(101, "sql", {"database": "acceptance"}, {}, query, "cancellation fixture", 1000, 5242880)
                handle = await adapter.open_operation(self.spec(engine), prepared, deadline=time.monotonic() + 20)
                async def consume():
                    await adapter.run(handle)
                    async for _ in adapter.batches(handle):
                        pass
                running = asyncio.create_task(consume())
                try:
                    await asyncio.sleep(0.3)
                    self.assertFalse(running.done(), f"{engine}: fixture finished before cancellation")
                    confirmation = await adapter.request_stop(self.spec(engine), handle.backend_ref, reason="user_cancel", deadline=time.monotonic() + 5)
                    self.assertTrue(confirmation.confirmed, confirmation.reason)
                    self.assertEqual(confirmation.reason, "user_cancel")
                    self.assertEqual(confirmation.evidence["source"], {
                        "mysql": "mysql_marked_query_absent_after_kill",
                        "postgresql": "postgres_query_inactive_after_cancel",
                    }[engine])
                finally:
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
                    await adapter.close(handle)

    async def test_clickhouse_active_cancel_uses_exact_positive_kill_fact(self):
        adapter = get_adapter("clickhouse")
        query = "SELECT SUM(a.id*b.id*c.id*d.id*e.id) FROM orders a CROSS JOIN orders b CROSS JOIN orders c CROSS JOIN orders d CROSS JOIN orders e"
        prepared = PreparedOperation(103, "sql", {"database": "acceptance"}, {}, query, "active cancel fixture", 1000, 5242880)
        handle = await adapter.open_operation(self.spec("clickhouse"), prepared, deadline=time.monotonic() + 20)
        async def consume():
            await adapter.run(handle)
            async for _ in adapter.batches(handle):
                pass
        running = asyncio.create_task(consume())
        try:
            await asyncio.sleep(0.5)
            confirmation = await adapter.request_stop(self.spec("clickhouse"), handle.backend_ref, reason="user_cancel", deadline=time.monotonic() + 5)
            self.assertTrue(confirmation.confirmed, confirmation)
            self.assertIn(confirmation.evidence["source"], ("clickhouse_exact_kill_sync_result", "clickhouse_exact_terminal_query_log"))
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
            await adapter.close(handle)

    async def test_real_bound_accounts_reject_writes(self):
        import asyncmy
        import asyncpg
        import httpx
        from pymongo import AsyncMongoClient
        from pymongo.errors import OperationFailure
        from redis.asyncio import Redis
        from redis.exceptions import ResponseError
        mysql = self.spec("mysql")
        connection = await asyncmy.connect(host=mysql.host, port=mysql.port, user=mysql.login_user, password=mysql.secret, database="acceptance")
        try:
            async with connection.cursor() as cursor:
                with self.assertRaises(asyncmy.errors.MySQLError):
                    await cursor.execute("UPDATE orders SET status=status WHERE id=-1")
        finally:
            connection.close()
        postgres = self.spec("postgresql")
        connection = await asyncpg.connect(host=postgres.host, port=postgres.port, user=postgres.login_user, password=postgres.secret, database="acceptance", ssl=False)
        try:
            with self.assertRaises(asyncpg.PostgresError):
                await connection.execute("UPDATE orders SET status=status WHERE id=-1")
        finally:
            await connection.close()
        mongo = self.spec("mongodb")
        client = AsyncMongoClient(host=mongo.host, port=mongo.port, username=mongo.login_user, password=mongo.secret, authSource="acceptance", directConnection=True)
        try:
            with self.assertRaises(OperationFailure):
                await client.acceptance.orders.update_one({"_id": -1}, {"$set": {"forbidden": True}})
        finally:
            await client.close()
        redis = self.spec("redis")
        client = Redis(host=redis.host, port=redis.port, username=redis.login_user, password=redis.secret)
        try:
            with self.assertRaises(ResponseError):
                await client.set("acceptance:write-denied", "must-not-write")
        finally:
            await client.aclose()
        clickhouse = self.spec("clickhouse")
        async with httpx.AsyncClient(auth=(clickhouse.login_user, clickhouse.secret)) as client:
            response = await client.post(f"http://{clickhouse.host}:{clickhouse.port}/", content=b"INSERT INTO acceptance.orders SELECT * FROM acceptance.orders WHERE 0")
            self.assertNotEqual(response.status_code, 200)
            self.assertIn(response.headers.get("x-clickhouse-exception-code"), ("164", "497"))

    async def test_redis_and_mongo_large_values(self):
        # ru_maxrss 是进程终身高水位，隔离前序测试分配，保持真实目标和 256 MiB 门槛。
        probe = """
import asyncio
import sys
sys.path[:0] = sys.argv[1:3]
from test_database_adapters_live import LiveAdapterTests
LiveAdapterTests.setUpClass()
asyncio.run(LiveAdapterTests()._check_redis_and_mongo_large_values())
"""
        tests_dir = Path(__file__).resolve().parent
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", probe, str(tests_dir), str(tests_dir.parent),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            output, _ = await asyncio.wait_for(child.communicate(), 60)
        except BaseException:
            child.kill()
            await child.wait()
            raise
        self.assertEqual(child.returncode, 0, output.decode("utf-8", errors="replace"))

    async def _check_redis_and_mongo_large_values(self):
        from pymongo import AsyncMongoClient
        from redis.asyncio import Redis
        redis = Redis(host=self.spec("redis").host, password="local-fixture-root")
        mongo = AsyncMongoClient(host=self.spec("mongodb").host, username="root", password="local-fixture-root", directConnection=True)
        try:
            await redis.hset("acceptance:huge-hash", "payload", "x" * 8388608)
            await mongo.acceptance.huge.update_one({"_id": "fixture"}, {"$set": {"payload": "x" * 8388608}}, upsert=True)
            for engine, context, operation in (
                ("redis", {"database": 0}, {"type": "hash_page", "key": "acceptance:huge-hash", "count": 10}),
                ("mongodb", {"database": "acceptance", "collection": "huge"}, {"type": "find", "filter": {}, "limit": 1}),
            ):
                with self.subTest(engine=engine), self.assertRaises(AdapterError) as caught:
                    await self.collect(engine, "redis_read" if engine == "redis" else "mongo_read", context, operation, max_bytes=65536)
                self.assertEqual(caught.exception.code, "protocol_value_too_large")
                self.assertTrue(caught.exception.completion.confirmed)
            # Linux vfork/exec 可继承父进程 rusage 高水位；VmHWM 仅属于当前子进程地址空间。
            peak_kib = next(int(line.split()[1]) for line in Path("/proc/self/status").read_text().splitlines()
                            if line.startswith("VmHWM:"))
            peak_mib = peak_kib / 1024
            print(f"isolated adapter peak RSS {peak_mib:.1f} MiB", flush=True)
            self.assertLess(peak_mib, 256, f"synthetic single-process peak RSS {peak_mib:.1f} MiB")
        finally:
            await redis.delete("acceptance:huge-hash")
            await redis.aclose()
            await mongo.acceptance.huge.delete_one({"_id": "fixture"})
            await mongo.close()

    async def test_stop_evidence_all_engines(self):
        for engine in self.targets:
            with self.subTest(engine=engine):
                adapter = get_adapter(engine)
                prepared = PreparedOperation(102, "probe", {}, {}, None, "stop evidence fixture", 1000, 5242880)
                handle = await adapter.open_operation(self.spec(engine), prepared, deadline=time.monotonic() + 10)
                try:
                    if engine == "clickhouse":
                        await adapter.run(handle)
                        async for _ in adapter.batches(handle):
                            pass
                        import httpx
                        async with httpx.AsyncClient(auth=("root", "local-fixture-root")) as root:
                            response = await root.post(f"http://{self.spec(engine).host}:8123/", content=b"SYSTEM FLUSH LOGS")
                            response.raise_for_status()
                    confirmation = await adapter.request_stop(self.spec(engine), handle.backend_ref, reason="user_cancel", deadline=time.monotonic() + 5)
                    self.assertTrue(confirmation.confirmed, f"{engine}: {confirmation}")
                finally:
                    await adapter.close(handle)


if __name__ == "__main__":
    unittest.main()
