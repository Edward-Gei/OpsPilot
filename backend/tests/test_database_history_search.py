"""执行历史只搜索当前用户能看到的非时间展示字段，并在分页前计数。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from conftest import auth_header, login_for_tokens
from app.core.security import encrypt_text
from app.models.auth import Permission, RolePermission
from app.models.database_management import DatabaseResult, DatabaseResultChunk, DatabaseTask
from app.schemas.database_management import ActorContext
from app.services import database_task_service as tasks

NOW = datetime(2026, 11, 9, 12, 0, 0)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


async def seed_search_records(factory, owner_id, other_id):
    """只写隔离测试库；显示摘要与未显示的密文/目标信息使用不同标记。"""
    async with factory() as session:
        rows = {}
        async def add(key, **changes):
            values = dict(owner_id=owner_id, actor_name="hidden-actor-only", instance_id=101, engine="mysql", kind="sql",
                status="succeeded", context={}, target_snapshot={"name": key.replace("_", "-"), "host": "hidden-host-only", "port": 49123,
                "environment": "hidden-env-only"}, credential_snapshot={"login_user": "hidden-login-only"},
                submitted_text_enc=encrypt_text("SELECT 'encrypted-sql-only'"),
                executed_statement_enc=encrypt_text("SELECT 'encrypted-statement-only'"),
                operation_summary="普通操作", config_revision=1, credential_id=102, credential_updated_at=NOW,
                created_at=NOW-timedelta(hours=2), finished_at=NOW-timedelta(minutes=1), quota_released=True,
                result_summary=None)
            values.update(changes)
            row = DatabaseTask(**values)
            session.add(row)
            await session.flush()
            rows[key] = row
            return row
        await add("formatted", id=719, engine="postgresql", context={"database": "ledger", "schema": "public", "table": "orders"},
            target_snapshot={"name": "账务实例_100%\\route", "host": "hidden-host-only", "environment": "hidden-env-only"},
            operation_summary="展示只读", started_at=NOW-timedelta(seconds=6), finished_at=NOW-timedelta(seconds=4.766),
            result_summary={"returned_rows": 1234, "returned_bytes": 1536, "truncated": True, "hidden": "hidden-summary-only"})
        await add("mib", kind="metadata", engine="clickhouse", result_summary={"returned_rows": 12, "returned_bytes": 2097152})
        await add("redis", engine="redis", kind="redis_read", context={"database": 3},
            result_summary={"returned_rows": 0, "returned_bytes": 999})
        await add("mongo", engine="mongodb", kind="mongo_read", context={"database": "documents", "collection": "events"},
            result_summary={"returned_rows": 10, "returned_bytes": 0})
        await add("probe", kind="probe", result_summary={"message": "握手成功", "returned_rows": 999999})
        await add("missing-summary", result_summary={"truncated": True, "expires_at": (NOW+timedelta(minutes=1)).isoformat()})
        await add("expired-fallback", result_summary={"returned_rows": 876543, "returned_bytes": 511,
            "expires_at": (NOW-timedelta(seconds=1)).isoformat()})
        await add("live-result", result_summary={"returned_rows": 5678, "returned_bytes": 1024,
            "expires_at": (NOW-timedelta(seconds=1)).isoformat()})
        await add("expired-result", result_summary={"returned_rows": 987654, "returned_bytes": 511,
            "expires_at": (NOW+timedelta(minutes=1)).isoformat()})
        for state in ("queued", "running", "stopping", "recovering", "failed", "cancelled", "timed_out", "rejected", "queue_expired"):
            await add(state, status=state, finished_at=None if state in ("queued", "running", "stopping", "recovering") else NOW,
                started_at=NOW-timedelta(seconds=2.5) if state == "running" else None)
        await add("terminal-missing-finish", status="failed", started_at=NOW-timedelta(seconds=9), finished_at=None)
        await add("negative-duration", started_at=NOW-timedelta(seconds=1), finished_at=NOW-timedelta(seconds=2),
            result_summary={"returned_rows": 2, "returned_bytes": 3})
        await add("wrong-summary-type", result_summary={"returned_rows": "1234", "returned_bytes": "1024"})
        await add("outside-owner", owner_id=other_id, operation_summary="owner-leak-only")
        await add("outside-retention", operation_summary="retention-leak-only", finished_at=NOW-timedelta(days=31))
        for key, expiry in (("live-result", NOW+timedelta(minutes=1)), ("expired-result", NOW-timedelta(seconds=1))):
            result = DatabaseResult(task_id=rows[key].id, owner_id=owner_id, instance_id=101, ready=True,
                manifest_enc=encrypt_text("{}"), returned_rows=1, expires_at=expiry)
            session.add(result)
            await session.flush()
            session.add(DatabaseResultChunk(result_id=result.id, chunk_no=0, start_row=0, row_count=1,
                payload_enc=encrypt_text('[["encrypted-payload-only"]]'), stored_bytes=42))
        await session.commit()
        return {key: row.id for key, row in rows.items()}


@pytest.fixture
async def history_records(db_factory, seed, monkeypatch):
    monkeypatch.setattr(tasks, "datetime", FrozenDateTime)
    ids = await seed_search_records(db_factory, seed["users"]["admin"], seed["users"]["ops1"])
    return ids, ActorContext(seed["users"]["admin"], "admin")


def search_vectors(ids):
    active = {"queued", "running", "stopping", "recovering"}
    expired = {"expired-fallback", "expired-result"}
    visible = set(ids)-{"outside-owner", "outside-retention"}
    return [
        (f"#{ids['formatted']}", {"formatted"}), (str(ids["formatted"]), {"formatted"}), ("  展示只读  ", {"formatted"}),
        ("%", {"formatted"}), ("_", {"formatted"}), ("\\", {"formatted"}),
        ("账务实例", {"formatted"}), ("PostgreSQL", {"formatted"}),
        ("ledger / public", {"formatted"}), ("orders", {"formatted"}), ("orders /", set()), ("DB 3", {"redis"}),
        ("结构浏览", {"mib"}), ("Redis 读取", {"redis"}), ("MongoDB 查询", {"mongo"}), ("events", {"mongo"}),
        ("SQL 查询", visible-{"mib", "redis", "mongo", "probe"}),
        ("MySQL", visible-{"formatted", "mib", "redis", "mongo"}),
        ("排队中", {"queued"}), ("执行中", {"running"}), ("取消处理中", {"stopping"}),
        ("停止待确认", {"recovering"}), ("已超时", {"timed_out"}), ("已取消", {"cancelled"}),
        ("未执行", {"rejected"}), ("排队过期", {"queue_expired"}),
        ("失败", {"failed", "terminal-missing-finish"}),
        ("已完成", {"formatted", "mib", "redis", "mongo", "probe", "missing-summary", "expired-fallback", "live-result", "expired-result", "negative-duration", "wrong-summary-type"}),
        ("结果暂不可用", active), ("无可查看结果", {"failed", "cancelled", "timed_out", "rejected", "queue_expired", "terminal-missing-finish"}),
        ("不产生结果集", {"probe"}), ("握手成功", {"probe"}),
        ("结果摘要暂不可用", {"missing-summary", "wrong-summary-type"}),
        ("结果已过期", expired), ("操作详情继续保留", expired),
        ("1,234 条 · 1.5 KiB", {"formatted"}), ("2.00 MiB", {"mib"}), ("999 B", {"redis"}),
        ("1.0 KiB", {"live-result"}), ("已截断", {"formatted", "missing-summary"}), ("有效至", {"live-result", "missing-summary"}),
        ("按需查看结果", {"formatted", "mib", "redis", "mongo", "negative-duration", "wrong-summary-type"}),
        ("1.234 s", {"formatted"}), ("2.500 s", {"running"}),
        ("9.000 s", set()), ("-1.000 s", set()),
        ("876,543", set()), ("987,654", set()), ("999,999", set()),
        ("hidden-host-only", set()), ("hidden-env-only", set()), ("hidden-login-only", set()),
        ("hidden-actor-only", set()), ("hidden-summary-only", set()),
        ("encrypted-sql-only", set()), ("encrypted-statement-only", set()), ("encrypted-payload-only", set()),
        ("2026-11-09", set()), ("owner-leak-only", set()), ("retention-leak-only", set()), ("' OR 1=1 --", set()),
    ]


async def verify_search_vectors(factory, actor, ids):
    mismatches = []
    for keyword, keys in search_vectors(ids):
        async with factory() as session:
            page = await tasks.list_tasks(session, actor, keyword=keyword, page_size=100)
        found = {item["task_id"] for item in page["items"]}
        expected = {ids[key] for key in keys}
        if found != expected or page["total"] != len(keys):
            mismatches.append((keyword, found, expected, page["total"]))
    assert not mismatches, mismatches


async def test_history_keyword_matches_only_visible_non_time_fields(db_factory, history_records):
    ids, actor = history_records
    await verify_search_vectors(db_factory, actor, ids)


async def test_history_keyword_filters_before_total_and_pagination(client, history_records):
    ids, _ = history_records
    headers = auth_header(await login_for_tokens(client, "admin"))
    params = {"keyword": "结果已过期", "page": 1, "page_size": 1}
    response = await client.get("/api/v1/database/tasks", headers=headers, params=params)
    assert response.status_code == 200
    first = response.json()["data"]
    second = (await client.get("/api/v1/database/tasks", headers=headers, params={**params, "page": 2})).json()["data"]
    assert first["total"] == second["total"] == 2
    assert {first["items"][0]["task_id"], second["items"][0]["task_id"]} == {ids["expired-fallback"], ids["expired-result"]}
    for filters in ({"engine": "redis"}, {"status": "failed"}, {"kind": "metadata"}, {"instance_id": 999},
                    {"started_from": NOW.isoformat()}, {"started_to": (NOW-timedelta(days=1)).isoformat()}):
        mismatch = await client.get("/api/v1/database/tasks", headers=headers, params={"keyword": "展示只读", **filters})
        assert mismatch.json()["data"]["total"] == 0, filters


async def test_history_keyword_expiry_uses_live_row_then_snapshot_after_cleanup(db_factory, history_records):
    ids, actor = history_records
    async with db_factory() as session:
        await session.execute(delete(DatabaseResult).where(DatabaseResult.task_id == ids["live-result"]))
        await session.commit()
    async with db_factory() as session:
        page = await tasks.list_tasks(session, actor, keyword="结果已过期", page_size=100)
    assert {item["task_id"] for item in page["items"]} == {ids[key] for key in ("expired-fallback", "expired-result", "live-result")}


@pytest.mark.parametrize("removed,keyword", [("database:write", "握手成功"), ("database:query", "展示只读")])
async def test_history_keyword_does_not_bypass_current_probe_permissions(db_factory, seed, history_records, removed, keyword):
    _, actor = history_records
    async with db_factory() as session:
        permission = (await session.execute(select(Permission.id).where(Permission.code == removed))).scalar_one()
        await session.execute(delete(RolePermission).where(RolePermission.role_id == seed["roles"]["admin"], RolePermission.permission_id == permission))
        await session.commit()
    async with db_factory() as session:
        assert (await tasks.list_tasks(session, actor, keyword=keyword))["total"] == 0


async def test_history_keyword_uses_one_request_clock_for_count_and_page(db_factory, history_records, monkeypatch):
    ids, actor = history_records
    class MovingDateTime(datetime):
        calls = 0
        @classmethod
        def now(cls, tz=None):
            current = NOW+timedelta(seconds=cls.calls)
            cls.calls += 1
            return current
    monkeypatch.setattr(tasks, "datetime", MovingDateTime)
    async with db_factory() as session:
        page = await tasks.list_tasks(session, actor, keyword="2.500 s", page_size=1)
    assert MovingDateTime.calls == 1
    assert page["total"] == 1 and page["items"][0]["task_id"] == ids["running"]


async def test_history_keyword_trim_empty_and_maximum_length(client, history_records):
    headers = auth_header(await login_for_tokens(client, "admin"))
    empty = (await client.get("/api/v1/database/tasks", headers=headers, params={"keyword": "   "})).json()["data"]
    normal = (await client.get("/api/v1/database/tasks", headers=headers)).json()["data"]
    assert empty["total"] == normal["total"]
    accepted = await client.get("/api/v1/database/tasks", headers=headers, params={"keyword": "x"*255})
    assert accepted.status_code == 200 and accepted.json()["data"]["total"] == 0
    assert (await client.get("/api/v1/database/tasks", headers=headers, params={"keyword": "x"*256})).status_code == 422


async def test_history_keyword_rejects_user_without_any_history_permission(client, db_factory, seed, history_records):
    async with db_factory() as session:
        ids = select(Permission.id).where(Permission.code.in_(("database:query", "database:write")))
        await session.execute(delete(RolePermission).where(RolePermission.role_id == seed["roles"]["admin"], RolePermission.permission_id.in_(ids)))
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.get("/api/v1/database/tasks", headers=headers, params={"keyword": "展示只读"})
    assert response.status_code == 403 and response.json()["data"] is None
