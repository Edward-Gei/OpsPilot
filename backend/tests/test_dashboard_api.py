"""工作台聚合接口测试：真实业务聚合、权限裁剪与双系列活动趋势。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.models.application_config import ConfigFile, ConfigVersion
from app.models.audit import AuditLog
from app.models.auth import Permission, Role, RolePermission, User, UserRole
from app.models.cmdb import Application, Host
from app.models.domain import DnsRecordSet, DnsZone
from app.models.execution import Execution
from app.models.ticket import Ticket, TicketStep
from app.services import dashboard_service
from app.services.dashboard_service import _bucket_label, _bucket_starts
from tests.conftest import TEST_PASSWORD_HASH, auth_header, login_for_tokens

pytestmark = pytest.mark.asyncio


def _ticket(no: str, status: str, creator_id: int, created_at: datetime) -> Ticket:
    """最小合法工单行（快照字段填充占位值）。"""
    return Ticket(
        ticket_no=no, template_id=1, title=f"工单{no}",
        type="daily_ops", job_host_id=1, job_host_snap={"id": 1, "name": "demo-jh"},
        status=status, creator_id=creator_id, submitted_at=created_at, created_at=created_at,
    )


async def _seed_biz(db_factory, seed) -> None:
    """业务种子：3 主机 / 1 应用 / 4 工单（今日 2 + 上月 2）/ 2 执行 / 1 条今日审计。"""
    now = datetime.now()
    last_month = now - timedelta(days=40)
    async with db_factory() as session:
        session.add_all([
            Host(hostname="h1", ip="10.0.0.1", environment="prod", status="online"),
            Host(hostname="h2", ip="10.0.0.2", environment="prod", status="offline"),
            Host(hostname="h3", ip="10.0.0.3", environment="demo", status="online"),
            Application(name="demo-app", deploy_type="shell"),
        ])
        uid = seed["users"]["ops1"]
        session.add_all([
            _ticket("T1", "success", uid, now),
            _ticket("T2", "running", uid, now),
            _ticket("T3", "failed", uid, last_month),
            _ticket("T4", "approving", uid, last_month),
        ])
        await session.flush()
        session.add_all([
            Execution(ticket_id=1, status="success", total_steps=1, created_at=now),
            Execution(ticket_id=2, status="running", total_steps=2, created_at=now),
            AuditLog(id=1, module="auth", action="login", result="success", created_at=now),
        ])
        await session.commit()


class TestSummary:
    async def test_unauthorized(self, client):
        """未登录 → 40101。"""
        resp = await client.get("/api/v1/dashboard/summary")
        assert resp.json()["code"] == 40101

    async def test_admin_full_sections(self, client, db_factory, seed):
        """admin 全权限：五段全有值，计数与种子一致。"""
        await _seed_biz(db_factory, seed)
        headers = auth_header(await login_for_tokens(client, "admin"))
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        assert data["cmdb"] == {
            "host_total": 3, "host_status": {"online": 2, "offline": 1}, "app_total": 1,
            "host_environment_status": [
                {"environment": "demo", "status": "online", "count": 1},
                {"environment": "prod", "status": "offline", "count": 1},
                {"environment": "prod", "status": "online", "count": 1},
            ],
            "app_deploy_type": {"shell": 1},
        }
        t = data["ticket"]
        assert t["today_total"] == 2
        # 本月：T1/T2；成功率分母仅统计执行终态（success/failed/interrupted）
        assert t["month_total"] == 2 and t["month_success"] == 1 and t["month_finished"] == 1
        assert t["status_dist"] == {"success": 1, "running": 1, "failed": 1, "approving": 1}
        # admin 无 approver 角色归属节点 → 待办为 0 而非 None
        assert data["todo_total"] == 0
        e = data["execution"]
        assert e["active"] == {"queued": 0, "running": 1, "paused": 0}
        assert [r["ticket_no"] for r in e["recent"]] == ["T2", "T1"]
        assert e["recent"][0]["job_host_name"] == "demo-jh"
        assert data["audit_today"] >= 1  # 种子 1 条 + 本次登录审计

    async def test_ops_sections_trimmed(self, client, db_factory, seed):
        """ops 无 ticket:approve 时仍显示配置审批待办数。"""
        await _seed_biz(db_factory, seed)
        headers = auth_header(await login_for_tokens(client, "ops1"))
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        assert data["todo_total"] == 0 and data["audit_today"] is None
        assert data["cmdb"]["host_total"] == 3
        assert data["ticket"]["status_dist"]["success"] == 1
        assert data["execution"]["active"]["running"] == 1


class TestTicketTrend:
    async def test_rbac_denied_without_ticket_read(self, client, db_factory, seed):
        """旧工单趋势仍严格要求 ticket:read。"""
        resp = await client.get("/api/v1/dashboard/ticket-trend")
        assert resp.json()["code"] == 40101
        headers = await _limited_headers(client, db_factory, {"execution:read"})
        resp = await client.get("/api/v1/dashboard/ticket-trend", headers=headers)
        assert resp.status_code == 403 and resp.json()["code"] == 40301

    async def test_bad_granularity(self, client, seed):
        """非法粒度 → 40001。"""
        headers = auth_header(await login_for_tokens(client, "admin"))
        resp = await client.get("/api/v1/dashboard/ticket-trend",
                                params={"granularity": "hour"}, headers=headers)
        assert resp.json()["code"] == 40001

    async def test_day_buckets_zero_filled(self, client, db_factory, seed):
        """day：固定 30 桶补零；今日与 3 天前各命中一桶。"""
        now = datetime.now()
        async with db_factory() as session:
            uid = seed["users"]["ops1"]
            session.add_all([
                _ticket("T1", "success", uid, now),
                _ticket("T2", "success", uid, now),
                _ticket("T3", "failed", uid, now - timedelta(days=3)),
                _ticket("T4", "failed", uid, now - timedelta(days=60)),  # 超窗不计
            ])
            await session.commit()
        headers = auth_header(await login_for_tokens(client, "admin"))
        data = (await client.get("/api/v1/dashboard/ticket-trend",
                                 params={"granularity": "day"}, headers=headers)).json()["data"]
        assert data["granularity"] == "day" and len(data["items"]) == 30
        assert data["items"][-1] == {"period": now.strftime("%m-%d"), "count": 2}
        assert data["items"][-4]["count"] == 1
        assert sum(i["count"] for i in data["items"]) == 3

    async def test_all_granularity_shapes(self, client, seed):
        """week/month/year：桶数 12/12/5，标签格式正确（空库全零）。"""
        headers = auth_header(await login_for_tokens(client, "admin"))
        for g, n in (("week", 12), ("month", 12), ("year", 5)):
            data = (await client.get("/api/v1/dashboard/ticket-trend",
                                     params={"granularity": g}, headers=headers)).json()["data"]
            assert len(data["items"]) == n
            assert all(i["count"] == 0 for i in data["items"])
        now = datetime.now()
        year_data = (await client.get("/api/v1/dashboard/ticket-trend",
                                      params={"granularity": "year"}, headers=headers)).json()["data"]
        assert year_data["items"][-1]["period"] == str(now.year)


class TestBucketHelpers:
    async def test_bucket_starts_month_cross_year(self):
        """月粒度跨年回退：2026-01 往前 12 桶首桶应为 2025-02。"""
        from datetime import date
        starts = _bucket_starts("month", date(2026, 1, 15))
        assert starts[0] == date(2025, 2, 1) and starts[-1] == date(2026, 1, 1)
        assert _bucket_label("month", starts[0]) == "2025-02"

    async def test_bucket_label_week_iso(self):
        """周标签使用 ISO 周号。"""
        from datetime import date
        assert _bucket_label("week", date(2026, 1, 5)) == "2026-W02"


@pytest.fixture
def dashboard_now(monkeypatch):
    """只固定被测统计时钟，避免跨日/月与时间窗边界漂移。"""
    now = datetime(2026, 10, 9, 12)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(dashboard_service, "datetime", FixedDatetime)
    return now


async def _limited_headers(client, db_factory, permissions):
    """使用真实 RBAC 角色，覆盖后端权限裁剪而非替换鉴权结果。"""
    async with db_factory() as session:
        role = Role(code="dashboard-reader", name="工作台测试角色")
        session.add(role)
        user = User(username="dashboard-reader", password_hash=TEST_PASSWORD_HASH,
                    display_name="工作台测试", source="local", status="active")
        session.add(user)
        await session.flush()
        ids = (await session.execute(select(Permission.id).where(Permission.code.in_(permissions)))).scalars()
        session.add_all([RolePermission(role_id=role.id, permission_id=pid) for pid in ids])
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()
    return auth_header(await login_for_tokens(client, "dashboard-reader"))


def _config(name, role_id, now, drift="clean", version=None, status="active"):
    return ConfigFile(name=name, platform_instance_id=1, locator={"private": "hidden-locator"},
                      locator_key=name, content_format="yaml", approval_role_id=role_id,
                      drift_status=drift, current_version_id=version, status=status,
                      last_sync_error="hidden-error-body", updated_at=now)


class TestExpandedSummary:
    async def test_empty_sections(self, client, seed):
        headers = auth_header(await login_for_tokens(client, "admin"))
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        assert data["cmdb"] == {"host_total": 0, "host_status": {}, "app_total": 0,
                                "host_environment_status": [], "app_deploy_type": {}}
        assert data["domain"] == {"zone_total": 0, "record_total": 0, "provider_dist": {},
                                  "sync_status": {}, "record_type_dist": {}}
        assert data["config"] == {"file_total": 0, "status_dist": {}}
        assert data["todo_breakdown"] == {"ticket": 0, "config": 0}
        assert data["attention"] == []
        assert data["audit"]["today_total"] == data["audit_today"]

    async def test_config_states_and_local_dns_record_sets(self, client, db_factory, seed, dashboard_now):
        now = dashboard_now
        async with db_factory() as session:
            role_id = seed["roles"]["ops"]
            session.add_all([
                _config("published", role_id, now, version=1),
                _config("new", role_id, now),
                _config("drift", role_id, now, drift="drifted"),
                _config("missing", role_id, now, drift="remote_missing", version=2),
                _config("failed", role_id, now, drift="sync_failed"),
                _config("archived", role_id, now, drift="drifted", status="archived"),
                DnsZone(id=1, provider="aws_route53", remote_zone_id="Z1", zone_name="one.test",
                        credential_id=999, record_count=999, sync_status="success"),
                DnsZone(id=2, provider="tencent_dnspod", remote_zone_id="Z2", zone_name="two.test",
                        credential_id=999, record_count=999, sync_status="failed"),
                DnsZone(id=3, provider="aws_route53", remote_zone_id="Z3", zone_name="three.test",
                        credential_id=999, record_count=999, sync_status="syncing"),
                DnsRecordSet(zone_id=1, record_key="a", record_name="one.test", record_type="A",
                             values=["192.0.2.1", "192.0.2.2"]),
                DnsRecordSet(zone_id=1, record_key="txt", record_name="one.test", record_type="TXT",
                             values=["hidden-dns-value"]),
                DnsRecordSet(zone_id=2, record_key="ns", record_name="two.test", record_type="NS"),
                DnsRecordSet(zone_id=404, record_key="orphan", record_name="gone.test", record_type="AAAA"),
            ])
            await session.commit()
        headers = auth_header(await login_for_tokens(client, "admin"))
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        assert data["config"] == {"file_total": 5, "status_dist": {
            "clean": 1, "unpublished": 1, "drifted": 1, "remote_missing": 1, "sync_failed": 1,
        }}
        assert sum(data["config"]["status_dist"].values()) == data["config"]["file_total"]
        assert {item["status"] for item in data["attention"] if item["module"] == "config"} == {
            "drifted", "remote_missing", "sync_failed",
        }
        assert data["domain"] == {"zone_total": 3, "record_total": 3,
                                  "provider_dist": {"aws_route53": 2, "tencent_dnspod": 1},
                                  "sync_status": {"success": 1, "failed": 1, "syncing": 1},
                                  "record_type_dist": {"A": 1, "TXT": 1, "NS": 1}}

    async def test_audit_and_ticket_windows_exclude_future(self, client, db_factory, seed, dashboard_now):
        now = dashboard_now
        headers = auth_header(await login_for_tokens(client, "admin"))
        async with db_factory() as session:
            await session.execute(delete(AuditLog))
            for i, (when, module, result) in enumerate([
                (now.replace(hour=0), "domain", "success"), (now, "domain", "failed"),
                (now - timedelta(days=1), "config", "failed"),
                (now + timedelta(seconds=1), "config", "success"),
            ], 1):
                session.add(AuditLog(id=i, module=module, action="test", result=result, created_at=when,
                                     detail={"private": "hidden-audit-body"}))
            uid = seed["users"]["ops1"]
            session.add_all([
                _ticket("today", "success", uid, now),
                _ticket("month", "failed", uid, now.replace(day=1, hour=0)),
                _ticket("before", "failed", uid, now.replace(day=1, hour=0) - timedelta(microseconds=1)),
                _ticket("future", "success", uid, now + timedelta(seconds=1)),
            ])
            await session.commit()
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        assert data["audit"] == {"today_total": 2, "result_dist": {"success": 1, "failed": 1},
                                 "module_dist": {"domain": 2}}
        assert data["audit_today"] == 2
        assert data["ticket"]["today_total"] == 1
        assert data["ticket"]["month_total"] == 2
        assert data["ticket"]["month_finished"] == 2

    async def test_todo_breakdown_matches_approval_roles(self, client, db_factory, seed, dashboard_now):
        async with db_factory() as session:
            role_id = seed["roles"]["ops"]
            file = _config("needs-approval", role_id, dashboard_now)
            ticket = _ticket("approve", "approving", seed["users"]["ops1"], dashboard_now)
            session.add_all([file, ticket])
            await session.flush()
            session.add_all([
                ConfigVersion(config_file_id=file.id, version_no=1, source="draft",
                              status="pending_approval", content_enc="hidden-encrypted-content",
                              approval_role_id=role_id),
                ConfigVersion(config_file_id=file.id, version_no=2, source="draft",
                              status="pending_approval", content_enc="hidden-encrypted-content",
                              approval_role_id=seed["roles"]["admin"]),
                TicketStep(ticket_id=ticket.id, step_order=0, step_name_snap="审批", script_type_snap="shell",
                           content_snap="hidden-script-content", approval_role_id_snap=seed["roles"]["admin"]),
            ])
            await session.commit()
        admin = auth_header(await login_for_tokens(client, "admin"))
        data = (await client.get("/api/v1/dashboard/summary", headers=admin)).json()["data"]
        assert data["todo_breakdown"] == {"ticket": 1, "config": 2}
        assert data["todo_total"] == 3
        ops = auth_header(await login_for_tokens(client, "ops1"))
        data = (await client.get("/api/v1/dashboard/summary", headers=ops)).json()["data"]
        assert data["todo_breakdown"] == {"ticket": 0, "config": 1}
        assert data["todo_total"] == 1

    @pytest.mark.parametrize("permission,section", [
        (None, None), ("cmdb:read", "cmdb"), ("domain:read", "domain"),
        ("config:read", "config"), ("audit:read", "audit"),
    ])
    async def test_sections_require_own_permission(self, client, db_factory, seed, permission, section):
        headers = await _limited_headers(client, db_factory, {permission} if permission else set())
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        for key in ("cmdb", "domain", "config", "audit", "ticket", "execution"):
            assert (data[key] is not None) == (key == section)
        assert (data["audit_today"] is not None) == (section == "audit")
        assert data["attention"] == []

    @pytest.mark.parametrize("permission,expected_modules", [
        (None, {"config", "domain", "execution"}),
        ("config:read", {"config"}), ("domain:read", {"domain"}),
        ("execution:read", {"execution"}), ("ticket:read", set()),
    ])
    async def test_attention_order_limit_permissions_and_safe_fields(
        self, client, db_factory, seed, dashboard_now, permission, expected_modules,
    ):
        now = dashboard_now
        async with db_factory() as session:
            role_id = seed["roles"]["ops"]
            session.add_all([
                _config(f"config-{i}", role_id, now - timedelta(minutes=i + 3), drift="drifted")
                for i in range(8)
            ])
            session.add_all([
                _config("archived", role_id, now, drift="sync_failed", status="archived"),
                _config("healthy", role_id, now, version=1),
                DnsZone(provider="aws_route53", remote_zone_id="failure", zone_name="failed.test",
                        credential_id=999, sync_status="failed", updated_at=now - timedelta(minutes=1),
                        last_sync_error="hidden-domain-error"),
                DnsZone(provider="aws_route53", remote_zone_id="healthy", zone_name="healthy.test",
                        credential_id=999, sync_status="success", updated_at=now),
            ])
            ticket = _ticket("attention", "failed", seed["users"]["ops1"], now - timedelta(days=60))
            ticket.params = {"private": "hidden-ticket-params"}
            session.add(ticket)
            await session.flush()
            session.add_all([
                Execution(ticket_id=ticket.id, status="failed", created_at=ticket.created_at, finished_at=now),
                Execution(ticket_id=ticket.id, status="interrupted", created_at=now - timedelta(minutes=2)),
                Execution(ticket_id=ticket.id, status="success", created_at=now, finished_at=now),
            ])
            await session.commit()
        headers = (await _limited_headers(client, db_factory, {permission}) if permission
                   else auth_header(await login_for_tokens(client, "admin")))
        response = await client.get("/api/v1/dashboard/summary", headers=headers)
        items = response.json()["data"]["attention"]
        assert {item["module"] for item in items} == expected_modules
        assert len(items) == (6 if permission in (None, "config:read") else
                              2 if permission == "execution:read" else 1 if permission == "domain:read" else 0)
        assert all(set(item) == {"module", "id", "name", "status", "updated_at"} for item in items)
        assert [i["updated_at"] for i in items] == sorted([i["updated_at"] for i in items], reverse=True)
        assert "hidden-" not in str(items)
        if permission is None:
            assert [i["name"] for i in items] == ["工单attention", "failed.test", "工单attention",
                                                  "config-0", "config-1", "config-2"]


class TestActivityTrend:
    async def test_unauthenticated_and_no_read_permissions(self, client, db_factory, seed):
        response = await client.get("/api/v1/dashboard/activity-trend")
        assert response.json()["code"] == 40101
        headers = await _limited_headers(client, db_factory, {"cmdb:read"})
        response = await client.get("/api/v1/dashboard/activity-trend", headers=headers)
        assert response.status_code == 403 and response.json()["code"] == 40301

    async def test_bad_granularity(self, client, seed):
        headers = auth_header(await login_for_tokens(client, "admin"))
        response = await client.get("/api/v1/dashboard/activity-trend", params={"granularity": "hour"}, headers=headers)
        assert response.json()["code"] == 40001

    @pytest.mark.parametrize("permission", [None, "ticket:read", "execution:read"])
    async def test_completed_time_zero_fill_and_permission_series(self, client, db_factory, seed, dashboard_now, permission):
        now = dashboard_now
        first = now.replace(hour=0) - timedelta(days=29)
        async with db_factory() as session:
            uid = seed["users"]["ops1"]
            session.add_all([
                _ticket("first", "success", uid, first), _ticket("today", "running", uid, now),
                _ticket("too-old", "failed", uid, first - timedelta(microseconds=1)),
                _ticket("future", "success", uid, now + timedelta(microseconds=1)),
            ])
            await session.flush()
            session.add_all([
                Execution(ticket_id=1, status="success", created_at=first - timedelta(days=30), finished_at=now),
                Execution(ticket_id=1, status="failed", created_at=first, finished_at=first),
                Execution(ticket_id=1, status="terminated", created_at=first, finished_at=now),
                Execution(ticket_id=1, status="interrupted", created_at=first, finished_at=now),
                Execution(ticket_id=1, status="failed", created_at=now),
                Execution(ticket_id=1, status="running", created_at=first, finished_at=now),
                Execution(ticket_id=1, status="success", created_at=first, finished_at=first - timedelta(microseconds=1)),
                Execution(ticket_id=1, status="success", created_at=first, finished_at=now + timedelta(microseconds=1)),
            ])
            await session.commit()
        headers = (await _limited_headers(client, db_factory, {permission}) if permission
                   else auth_header(await login_for_tokens(client, "admin")))
        data = (await client.get("/api/v1/dashboard/activity-trend", headers=headers)).json()["data"]
        assert data["granularity"] == "day" and len(data["items"]) == 30
        tickets = [i["tickets"] for i in data["items"]]
        executions = [i["executions"] for i in data["items"]]
        assert tickets == ([None] * 30 if permission == "execution:read" else [1] + [0] * 28 + [1])
        assert executions == ([None] * 30 if permission == "ticket:read" else [1] + [0] * 28 + [3])
        assert data["items"][-1]["period"] == "10-09"

    @pytest.mark.parametrize("granularity,count,first,label", [
        ("week", 12, datetime(2026, 7, 20), "2026-W41"),
        ("month", 12, datetime(2025, 11, 1), "2026-10"),
        ("year", 5, datetime(2022, 1, 1), "2026"),
    ])
    async def test_granularity_window_boundaries(self, client, db_factory, seed, dashboard_now,
                                                granularity, count, first, label):
        async with db_factory() as session:
            uid = seed["users"]["ops1"]
            session.add_all([_ticket("start", "success", uid, first),
                             _ticket("old", "success", uid, first - timedelta(microseconds=1)),
                             _ticket("now", "success", uid, dashboard_now)])
            session.add_all([Execution(ticket_id=1, status="success", finished_at=first),
                             Execution(ticket_id=1, status="success", finished_at=first - timedelta(microseconds=1))])
            await session.commit()
        headers = auth_header(await login_for_tokens(client, "admin"))
        data = (await client.get("/api/v1/dashboard/activity-trend", headers=headers,
                                 params={"granularity": granularity})).json()["data"]
        assert len(data["items"]) == count
        assert data["items"][0]["tickets"] == data["items"][0]["executions"] == 1
        assert data["items"][-1] == {"period": label, "tickets": 1, "executions": 0}
        assert sum(item["tickets"] for item in data["items"]) == 2
        assert sum(item["executions"] for item in data["items"]) == 1
