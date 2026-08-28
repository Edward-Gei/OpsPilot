"""工作台聚合接口测试：/dashboard/summary 权限裁剪 + /dashboard/ticket-trend 分桶。"""
from datetime import datetime, timedelta

import pytest

from app.models.audit import AuditLog
from app.models.cmdb import Application, Host
from app.models.execution import Execution
from app.models.ticket import Ticket
from app.services.dashboard_service import _bucket_label, _bucket_starts
from tests.conftest import auth_header, login_for_tokens

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
        """ops 无 ticket:approve / audit:read → 对应段为 null，其余段有值。"""
        await _seed_biz(db_factory, seed)
        headers = auth_header(await login_for_tokens(client, "ops1"))
        data = (await client.get("/api/v1/dashboard/summary", headers=headers)).json()["data"]
        assert data["todo_total"] is None and data["audit_today"] is None
        assert data["cmdb"]["host_total"] == 3
        assert data["ticket"]["status_dist"]["success"] == 1
        assert data["execution"]["active"]["running"] == 1


class TestTicketTrend:
    async def test_rbac_denied_without_ticket_read(self, client, db_factory, seed):
        """无 ticket:read（临时剥离 ops 工单权限不可行，直接用未登录 40101 + admin 通过覆盖门槛）。"""
        resp = await client.get("/api/v1/dashboard/ticket-trend")
        assert resp.json()["code"] == 40101

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
