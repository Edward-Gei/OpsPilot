"""M5 执行域 API 测试。

覆盖验收点（05-任务拆解 M5 / 04-API §6/§7/§11）：
- 工单控制面四接口状态机：abort=queued/running/paused、pause=仅 running、
  resume=仅 paused、force-abort=running/paused，状态不匹配 40901；
- 控制权限：无 execution:control 40301；非创建人且非 admin 40302；
- 控制接口只发信号不改工单状态（状态迁移由 pipeline 单一写入方负责）；
- 执行记录只读接口：列表筛选 / 详情步骤 / 历史日志 / 事件长轮询；
- 作业主机连通性测试：SSH 异常返回 ok=False（不抛错）。
"""
import os

from sqlalchemy import select, update

from app.core.config import settings
from app.engine import events as events_mod
from app.engine.logs import log_file_path
from app.models.auth import User, UserRole
from app.models.execution import Execution
from app.models.ticket import Ticket
from tests.conftest import TEST_PASSWORD_HASH, auth_header, login_for_tokens
from tests.test_ticket_api import _approver_headers, _base_env, _create_template, _submit


# ---------- 测试辅助 ----------

async def _queued_ticket(client, env, db_factory) -> tuple[dict, int]:
    """免审提交一张工单（queued），返回 (提交返回体, execution_id)。"""
    tpl_id = await _create_template(client, env)
    data = await _submit(client, env["ops_h"], tpl_id)
    async with db_factory() as s:
        eid = (await s.execute(
            select(Execution.id).where(Execution.ticket_id == data["id"])
        )).scalar_one()
    return data, eid


async def _set_ticket_status(db_factory, ticket_id: int, status: str) -> None:
    """直接改工单状态（模拟 pipeline 状态迁移，控制接口状态机测试用）。"""
    async with db_factory() as s:
        await s.execute(update(Ticket).where(Ticket.id == ticket_id).values(status=status))
        await s.commit()


async def _ops2_headers(client, db_factory, seed) -> dict:
    """第二个 ops 用户（有 execution:control 但非工单创建人，40302 测试用）。"""
    async with db_factory() as s:
        u = User(username="ops2", password_hash=TEST_PASSWORD_HASH, display_name="ops2",
                 source="local", status="active", must_change_password=False)
        s.add(u)
        await s.flush()
        s.add(UserRole(user_id=u.id, role_id=seed["roles"]["ops"]))
        await s.commit()
    return auth_header(await login_for_tokens(client, "ops2"))


class TestExecutionControl:
    """工单控制面四接口：状态机 + 信号写入 + 权限。"""

    async def test_state_machine_and_signal(self, client, db_factory, fake_redis):
        """各状态下允许/拒绝矩阵；接受后写 ops:ctrl:{eid} 且不改工单状态。"""
        env = await _base_env(client)
        data, eid = await _queued_ticket(client, env, db_factory)
        tid = data["id"]
        h = env["ops_h"]

        # queued：普通控制的状态机拒绝；force-abort 先由独立权限拒绝
        for op in ("pause", "resume"):
            resp = await client.post(f"/api/v1/tickets/{tid}/{op}", headers=h)
            assert resp.json()["code"] == 40901, op
        assert (await client.post(f"/api/v1/tickets/{tid}/force-abort", headers=h)).json()["code"] == 40301
        # queued：abort 放行（覆盖排队中撤销场景），工单状态保持 queued
        body = (await client.post(f"/api/v1/tickets/{tid}/abort", headers=h)).json()
        assert body["code"] == 0
        assert body["data"] == {"status": "queued", "execution_id": eid, "signal": "abort"}
        assert await fake_redis.get(f"ops:ctrl:{eid}") == "abort"

        # running：pause 放行；force-abort 由 ops 独立权限拒绝；resume 40901
        await _set_ticket_status(db_factory, tid, "running")
        assert (await client.post(f"/api/v1/tickets/{tid}/pause", headers=h)).json()["code"] == 0
        assert await fake_redis.get(f"ops:ctrl:{eid}") == "pause"
        assert (await client.post(f"/api/v1/tickets/{tid}/resume", headers=h)).json()["code"] == 40901
        assert (await client.post(f"/api/v1/tickets/{tid}/force-abort", headers=h)).json()["code"] == 40301

        # paused：resume 放行；pause 40901
        await _set_ticket_status(db_factory, tid, "paused")
        assert (await client.post(f"/api/v1/tickets/{tid}/pause", headers=h)).json()["code"] == 40901
        assert (await client.post(f"/api/v1/tickets/{tid}/resume", headers=h)).json()["code"] == 0
        assert await fake_redis.get(f"ops:ctrl:{eid}") == "resume"

        # 终态：一律 40901
        await _set_ticket_status(db_factory, tid, "success")
        for op in ("abort", "pause", "resume"):
            resp = await client.post(f"/api/v1/tickets/{tid}/{op}", headers=h)
            assert resp.json()["code"] == 40901, op
        assert (await client.post(f"/api/v1/tickets/{tid}/force-abort", headers=h)).json()["code"] == 40301

    async def test_control_permission(self, client, db_factory, seed):
        """approver 无 execution:control 40301；ops2 非创建人 40302；admin 放行。"""
        env = await _base_env(client)
        data, _eid = await _queued_ticket(client, env, db_factory)
        tid = data["id"]

        appr_h = await _approver_headers(client, db_factory, seed)
        assert (await client.post(f"/api/v1/tickets/{tid}/abort", headers=appr_h)).json()["code"] == 40301

        ops2_h = await _ops2_headers(client, db_factory, seed)
        assert (await client.post(f"/api/v1/tickets/{tid}/abort", headers=ops2_h)).json()["code"] == 40302

        assert (await client.post(f"/api/v1/tickets/{tid}/abort",
                                  headers=env["admin_h"])).json()["code"] == 0

    async def test_control_not_found(self, client, db_factory):
        """工单不存在 40401。"""
        env = await _base_env(client)
        resp = await client.post("/api/v1/tickets/99999/abort", headers=env["ops_h"])
        assert resp.json()["code"] == 40401


class TestExecutionReadApis:
    """执行记录只读接口：列表 / 详情 / 日志 / 事件长轮询。"""

    async def test_list_and_detail(self, client, db_factory):
        """列表筛选与详情步骤装配（预建步骤子表全 pending）。"""
        env = await _base_env(client)
        data, eid = await _queued_ticket(client, env, db_factory)

        # 列表：单号模糊筛选命中；status 不匹配为空
        body = (await client.get("/api/v1/executions",
                                 params={"ticket_no": data["ticket_no"]},
                                 headers=env["ops_h"])).json()["data"]
        assert body["total"] == 1
        item = body["items"][0]
        assert (item["id"], item["status"]) == (eid, "queued")
        assert item["creator_name"] == "ops1" and item["job_host_name"] == "tk-agent-01"
        body = (await client.get("/api/v1/executions", params={"status": "running"},
                                 headers=env["ops_h"])).json()["data"]
        assert body["total"] == 0

        # 详情：汇总 + 步骤列表（快照名/类型）
        detail = (await client.get(f"/api/v1/executions/{eid}",
                                   headers=env["ops_h"])).json()["data"]
        assert (detail["status"], detail["ticket_status"]) == ("queued", "queued")
        assert detail["total_steps"] == 1
        assert detail["exec_strategy"]["timeout"] == 600
        assert len(detail["steps"]) == 1
        step = detail["steps"][0]
        assert (step["step_order"], step["step_name"], step["script_type"]) == (1, "重启服务", "shell")
        assert step["status"] == "pending"

        # 不存在 40401
        assert (await client.get("/api/v1/executions/99999",
                                 headers=env["ops_h"])).json()["code"] == 40401

    async def test_logs_endpoint(self, client, db_factory, monkeypatch, tmp_path):
        """历史日志按行偏移增量读取；文件不存在返回空 + eof。"""
        env = await _base_env(client)
        _data, eid = await _queued_ticket(client, env, db_factory)
        monkeypatch.setattr(settings, "exec_log_dir", str(tmp_path))
        path = log_file_path(eid, 1)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("line1\nline2\nline3\n")

        body = (await client.get(f"/api/v1/executions/{eid}/logs",
                                 params={"step_order": 1, "offset": 0, "limit": 2},
                                 headers=env["ops_h"])).json()["data"]
        assert body == {"lines": ["line1", "line2"], "next_offset": 2, "eof": False}
        body = (await client.get(f"/api/v1/executions/{eid}/logs",
                                 params={"step_order": 1, "offset": 2},
                                 headers=env["ops_h"])).json()["data"]
        assert body == {"lines": ["line3"], "next_offset": 3, "eof": True}
        # 文件不存在（步骤尚未产生日志）→ 空 + eof
        body = (await client.get(f"/api/v1/executions/{eid}/logs",
                                 params={"step_order": 2},
                                 headers=env["ops_h"])).json()["data"]
        assert body == {"lines": [], "next_offset": 0, "eof": True}

    async def test_events_long_poll(self, client, db_factory, fake_redis):
        """有增量事件立即返回；终态执行不挂起直接 finished=true。"""
        env = await _base_env(client)
        _data, eid = await _queued_ticket(client, env, db_factory)
        await events_mod.publish_event(eid, events_mod.KIND_EXECUTION, {"status": "running"})

        body = (await client.get(f"/api/v1/executions/{eid}/events",
                                 params={"since_seq": 0},
                                 headers=env["ops_h"])).json()["data"]
        assert body["last_seq"] == 1 and body["finished"] is False
        assert body["events"][0]["data"] == {"status": "running"}

        # 终态：即使无新事件也立即返回 finished=true（前端据此停止轮询）
        async with db_factory() as s:
            await s.execute(update(Execution).where(Execution.id == eid).values(status="success"))
            await s.commit()
        body = (await client.get(f"/api/v1/executions/{eid}/events",
                                 params={"since_seq": 1},
                                 headers=env["ops_h"])).json()["data"]
        assert body == {"events": [], "last_seq": 1, "finished": True}

    async def test_read_permission(self, client, db_factory, seed):
        """approver 有 execution:read 可读；无凭据 40101。"""
        env = await _base_env(client)
        _data, eid = await _queued_ticket(client, env, db_factory)
        appr_h = await _approver_headers(client, db_factory, seed)
        assert (await client.get(f"/api/v1/executions/{eid}", headers=appr_h)).json()["code"] == 0
        assert (await client.get("/api/v1/executions")).json()["code"] == 40101


class TestJobHostTestApi:
    """作业主机连通性测试接口（/api/v1/job-hosts/{id}/test）。"""

    async def test_ssh_failure_and_permission(self, client, db_factory, monkeypatch):
        """SSH 建连异常 → ok=False（不抛错）；ops 无 job_host:write 40301。"""
        env = await _base_env(client)

        async def _fail_conn(ip, port, cred):
            raise OSError("connection refused")

        monkeypatch.setattr("app.services.job_host_service.open_connection", _fail_conn)
        body = (await client.post(f"/api/v1/job-hosts/{env['job_host_id']}/test",
                                  headers=env["admin_h"])).json()
        assert body["code"] == 0
        assert body["data"]["ok"] is False and "SSH 异常" in body["data"]["message"]

        resp = await client.post(f"/api/v1/job-hosts/{env['job_host_id']}/test",
                                 headers=env["ops_h"])
        assert resp.json()["code"] == 40301
