"""M7 安全审计测试。

覆盖验收点（05-任务拆解 M7）：
- /audit/logs 组合检索：时间范围 / 操作人 / 模块 / 动作 / 结果 / keyword(对象名称) + 分页；
- /audit/logs/export：CSV/xlsx 导出内容与筛选一致、上限截断参数、非法 format 40001、
  导出行为自身写审计（audit.export 入队）；
- RBAC：ops 角色无 audit:read / audit:export → 40301；
- 分区滚动纯函数 compute_rollover：预建缺失月份、按保留期 DROP、pmax 永不清理。
"""
import io
from datetime import date, datetime

from openpyxl import load_workbook

from app import audit
from app.audit.partition import compute_rollover, partition_upper_bound
from app.models.audit import AuditLog
from tests.conftest import auth_header, login_for_tokens


async def _seed_logs(db_factory):
    """种子审计数据：跨模块/操作人/结果/时间的六条记录（SQLite 需显式 id）。"""
    rows = [
        # (id, 时间, 操作人, 模块, 动作, 对象名, 结果)
        (1, datetime(2026, 7, 1, 10, 0), "admin", "auth", "login", None, "success"),
        (2, datetime(2026, 7, 2, 11, 0), "admin", "user", "user.create", "tom", "success"),
        (3, datetime(2026, 7, 3, 12, 0), "ops1", "cmdb", "host.create", "web-01", "success"),
        (4, datetime(2026, 7, 4, 13, 0), "ops1", "auth", "login", None, "failed"),
        (5, datetime(2026, 7, 5, 14, 0), None, "execution", "execution.finish", "T20260705", "failed"),
        (6, datetime(2026, 7, 6, 15, 0), "admin", "cmdb", "host.delete", "web-02", "success"),
    ]
    async with db_factory() as session:
        for rid, ts, actor, module, action, target, result in rows:
            session.add(AuditLog(
                id=rid, created_at=ts, actor_name=actor, module=module,
                action=action, target_type="x", target_name=target, result=result,
            ))
        await session.commit()


async def _admin_headers(client):
    """admin 持有 audit:read / audit:export 全部权限。"""
    return auth_header(await login_for_tokens(client, "admin"))


# ---------- /audit/logs 检索 ----------

class TestAuditList:
    async def test_rbac_denied(self, client):
        """ops 角色无 audit:read → 40301。"""
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/audit/logs", headers=headers)
        assert resp.json()["code"] == 40301

    async def test_list_all_desc(self, client, db_factory):
        """默认按时间倒序全量分页。"""
        await _seed_logs(db_factory)
        headers = await _admin_headers(client)
        data = (await client.get("/api/v1/audit/logs", headers=headers)).json()["data"]
        assert data["total"] == 6
        assert [i["id"] for i in data["items"]] == [6, 5, 4, 3, 2, 1]

    async def test_combined_filters(self, client, db_factory):
        """组合筛选：模块 / 结果 / 操作人 / 动作 / keyword / 时间范围。"""
        await _seed_logs(db_factory)
        headers = await _admin_headers(client)

        async def _ids(params):
            body = (await client.get("/api/v1/audit/logs", params=params, headers=headers)).json()
            return [i["id"] for i in body["data"]["items"]]

        assert await _ids({"module": "cmdb"}) == [6, 3]
        assert await _ids({"result": "failed"}) == [5, 4]
        assert await _ids({"actor": "ops"}) == [4, 3]
        assert await _ids({"action": "login"}) == [4, 1]
        # keyword → target_name 模糊匹配
        assert await _ids({"keyword": "web"}) == [6, 3]
        # 时间闭区间 + 组合条件
        assert await _ids({"start": "2026-07-02T00:00:00", "end": "2026-07-04T23:59:59"}) == [4, 3, 2]
        assert await _ids({"module": "auth", "result": "failed"}) == [4]

    async def test_pagination(self, client, db_factory):
        """分页参数生效：page=2/page_size=4 取剩余 2 条。"""
        await _seed_logs(db_factory)
        headers = await _admin_headers(client)
        body = (await client.get("/api/v1/audit/logs",
                                 params={"page": 2, "page_size": 4}, headers=headers)).json()
        assert body["data"]["total"] == 6
        assert [i["id"] for i in body["data"]["items"]] == [2, 1]


# ---------- /audit/logs/export 导出 ----------

class TestAuditExport:
    async def test_rbac_denied(self, client):
        """ops 角色无 audit:export → 40301。"""
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/audit/logs/export", headers=headers)
        assert resp.json()["code"] == 40301

    async def test_bad_format(self, client, db_factory):
        """非法 format → 40001 参数错误。"""
        headers = await _admin_headers(client)
        resp = await client.get("/api/v1/audit/logs/export",
                                params={"format": "pdf"}, headers=headers)
        assert resp.json()["code"] == 40001

    async def test_csv_matches_filter_and_self_audited(self, client, db_factory):
        """CSV 导出内容与筛选一致（cmdb 两条）；导出行为自身入审计队列。"""
        await _seed_logs(db_factory)
        headers = await _admin_headers(client)
        audit.audit_writer._drain()  # 清掉登录等操作的排队事件，聚焦断言导出事件
        resp = await client.get("/api/v1/audit/logs/export",
                                params={"format": "csv", "module": "cmdb"}, headers=headers)
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        lines = resp.text.lstrip("\ufeff").strip().splitlines()
        assert len(lines) == 1 + 2  # 表头 + cmdb 两条
        assert "host.delete" in lines[1] and "web-02" in lines[1]
        assert "host.create" in lines[2] and "web-01" in lines[2]
        # 导出自身写审计：module=audit / action=audit.export，detail 带格式与行数
        queued = audit.audit_writer._drain()
        exports = [e for e in queued if e.module == "audit" and e.action == "audit.export"]
        assert len(exports) == 1
        assert exports[0].detail["format"] == "csv"
        assert exports[0].detail["count"] == 2
        assert exports[0].actor_name == "admin"

    async def test_xlsx_export(self, client, db_factory):
        """xlsx 导出：可被 openpyxl 读回，行数与筛选一致。"""
        await _seed_logs(db_factory)
        headers = await _admin_headers(client)
        resp = await client.get("/api/v1/audit/logs/export",
                                params={"format": "xlsx", "result": "failed"}, headers=headers)
        assert resp.status_code == 200
        wb = load_workbook(io.BytesIO(resp.content), read_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        assert len(rows) == 1 + 2  # 表头 + failed 两条
        assert rows[0][0] == "时间"
        assert {r[4] for r in rows[1:]} == {"execution.finish", "login"}


# ---------- 分区滚动纯函数 ----------

class TestPartitionRollover:
    TODAY = date(2026, 7, 27)

    def test_no_action_when_covered(self):
        """当月+未来 3 月分区齐备且无过期分区 → 无动作。"""
        existing = ["p202607", "p202608", "p202609", "p202610", "pmax"]
        create, drop = compute_rollover(existing, self.TODAY, retention_days=365)
        assert create == [] and drop == []

    def test_prebuild_missing_months(self):
        """缺失的未来月份按升序补齐（含跨年进位）。"""
        create, drop = compute_rollover(["p202607", "pmax"], self.TODAY, retention_days=365)
        assert create == ["p202608", "p202609", "p202610"]
        assert drop == []
        # 跨年：11 月起预建应进位到次年 1、2 月
        create, _ = compute_rollover(["pmax"], date(2026, 11, 15), retention_days=365)
        assert create == ["p202611", "p202612", "p202701", "p202702"]

    def test_drop_expired_only(self):
        """仅 DROP 上界早于保留边界的整月分区；pmax 与临界分区保留。

        today=2026-07-27、retention=365 → 边界 2025-07-27：
        p202506 上界 2025-07-01 ≤ 边界 → 清理；p202507 上界 2025-08-01 > 边界 → 保留。
        """
        existing = ["p202505", "p202506", "p202507", "p202607",
                    "p202608", "p202609", "p202610", "pmax"]
        create, drop = compute_rollover(existing, self.TODAY, retention_days=365)
        assert create == []
        assert drop == ["p202505", "p202506"]

    def test_upper_bound_parse(self):
        """分区名换算上界：常规名取次月 1 日，pmax/异常名返回 None。"""
        assert partition_upper_bound("p202612") == date(2027, 1, 1)
        assert partition_upper_bound("p202601") == date(2026, 2, 1)
        assert partition_upper_bound("pmax") is None
        assert partition_upper_bound("other") is None
