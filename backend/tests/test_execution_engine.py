"""M5 执行引擎测试（执行范式改造后）。

覆盖验收点（05-任务拆解 M5，执行范式改造修订）：
- Jinja2 沙箱脚本参数渲染；
- 事件总线：发号递增 / since_seq 增量回放；
- Pipeline 调度器（mock 作业主机执行器）：
  * 多步骤串行推进全成功 → execution/工单 success + 步骤日志落盘；
  * fail_fast 步骤失败 → 后续步骤保持 pending 不再派发，终态 failed；
  * queued 阶段下发 abort → 检查点①直接 terminated + 工单 interrupted(user_abort)。

说明：执行范式改造后不再有目标主机矩阵/分批（execution_step_host 已下线），
Ansible 步骤退化为作业主机上的 Shell 特例（仅退出码判定），因此旧版
PLAY RECAP 解析 / inventory 生成 / _chunk 分批相关用例一并移除。
"""
import os

import pytest
from sqlalchemy import select

from app import audit
from app.core.config import settings
from app.engine import control as ctrl, events, pipeline
from app.models.execution import Execution, ExecutionStep
from app.models.ticket import Ticket
from tests.conftest import auth_header, login_for_tokens


# ---------- 测试辅助（新范式：作业主机 + 模板 + 免审工单） ----------

JOB_HOST_PAYLOAD = {
    "name": "引擎测试作业主机", "ip": "10.9.9.9", "ssh_port": 22,
    "workdir": "/opt/opspilot/workspace",
}

CRED_PAYLOAD = {
    "name": "引擎测试凭据", "login_user": "root", "auth_type": "password",
    "secret": "S3cret!pass",
}


async def _engine_env(client) -> dict:
    """引擎测试前置：admin/ops 登录 + 1 凭据 + 1 台作业主机（主机关联凭据）。"""
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_h = auth_header(await login_for_tokens(client, "ops1"))
    resp = await client.post("/api/v1/credentials", json=CRED_PAYLOAD, headers=admin_h)
    body = resp.json()
    assert body["code"] == 0, body
    cred_id = body["data"]["id"]
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "credential_id": cred_id},
                             headers=admin_h)
    body = resp.json()
    assert body["code"] == 0, body
    return {"admin_h": admin_h, "ops_h": ops_h, "job_host_id": body["data"]["id"],
            "credential_id": cred_id}


def _step(name: str = "重启服务", **override) -> dict:
    """标准流程步骤（shell + svc 参数）。"""
    return {
        "name": name,
        "script_type": "shell",
        "content": "#!/bin/bash\nsystemctl restart {{ svc }}",
        "params_schema": [
            {"name": "svc", "label": "服务名", "default": "nginx", "required": True}
        ],
        "timeout": 300,
        **override,
    }


async def _create_template(client, env, **override) -> int:
    """创建免审流程及工单模板入口，返回模板 id。"""
    template_name = override.pop("name", "引擎测试模板")
    steps = override.pop("steps", [_step()])
    process = await client.post("/api/v1/process-templates", json={
        "name": f"{template_name} 流程",
        "description": "引擎调度测试流程",
        "exec_strategy": {"timeout": 600, "fail_fast": True, "kill_on_stop": False},
        "steps": [{"name": step["name"], "script_type": step["script_type"],
                   "content": step["content"], "timeout": step["timeout"],
                   "approval_role_id": None} for step in steps],
    }, headers=env["ops_h"])
    process_body = process.json()
    assert process_body["code"] == 0, process_body
    payload = {
        "name": template_name,
        "type": override.pop("type", "daily_ops"),
        "description": override.pop("description", "引擎调度测试"),
        "job_host_id": env["job_host_id"],
        "process_template_id": process_body["data"]["id"],
        "params_schema": [{"name": "svc", "label": "服务名", "source": "user",
                           "input_type": "text", "default": "nginx", "required": True}],
        "notify_rules": [], "visible_role_ids": [],
        **override,
    }
    resp = await client.post("/api/v1/templates", json=payload, headers=env["ops_h"])
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


async def _submitted_execution_id(client, env, db_factory, **tpl_override) -> int:
    """免审模板提交一张工单（直接 queued + 预建执行子表），返回 execution_id。"""
    tpl_id = await _create_template(client, env, **tpl_override)
    resp = await client.post("/api/v1/tickets",
                             json={"template_id": tpl_id, "params": {}},
                             headers=env["ops_h"])
    body = resp.json()
    assert body["code"] == 0, body
    async with db_factory() as s:
        return (await s.execute(select(Execution.id))).scalar_one()


def _fake_job_host_runner(failed_steps: dict[int, str] | None = None):
    """构造 mock 版 run_shell_on_job_host：failed_steps 中的步骤序号返回 failed，其余 success。"""
    failed_steps = failed_steps or {}

    async def _run(*, job_host, credential, script, env=None, timeout,
                   step_order, log, control):
        log.write(step_order, f"[fake] step {step_order} on {job_host.ip}: {script.splitlines()[-1]}")
        if step_order in failed_steps:
            return "failed", 1, failed_steps[step_order]
        return "success", 0, None

    return _run


@pytest.fixture
def engine_setup(db_factory, monkeypatch, tmp_path):
    """引擎运行环境：会话工厂指向测试库、日志目录指向临时目录。"""
    monkeypatch.setattr(pipeline, "async_session_factory", db_factory)
    monkeypatch.setattr(settings, "exec_log_dir", str(tmp_path))
    return tmp_path


# ---------- 脚本渲染（纯函数单测） ----------

class TestRenderScript:
    """Jinja2 沙箱脚本参数渲染。"""

    def test_render_script(self):
        """参数替换生效；保留脚本尾部换行。"""
        out = pipeline.render_script("systemctl restart {{ svc }}\n", {"svc": "nginx"})
        assert out == "systemctl restart nginx\n"

    def test_render_script_empty_params(self):
        """无参数脚本原样输出（params 为 None 也不报错）。"""
        assert pipeline.render_script("echo ok\n", None) == "echo ok\n"


# ---------- 事件总线 ----------

class TestEventBus:
    """事件总线：发号递增 + since_seq 增量回放（fakeredis）。"""

    async def test_publish_and_fetch(self, fake_redis):
        seq1 = await events.publish_event(1001, events.KIND_EXECUTION, {"status": "running"})
        seq2 = await events.publish_event(1001, events.KIND_STEP, {"step_order": 1})
        assert (seq1, seq2) == (1, 2)
        assert await events.current_seq(1001) == 2

        all_events = await events.fetch_events_since(1001, 0)
        assert [e["seq"] for e in all_events] == [1, 2]
        assert all_events[0]["kind"] == "execution_status"
        # 增量：只取 seq > since_seq
        assert [e["seq"] for e in await events.fetch_events_since(1001, 1)] == [2]
        assert await events.fetch_events_since(1001, 2) == []


# ---------- Pipeline 调度器（mock 作业主机执行器） ----------

class TestPipelineScheduler:
    """调度器集成测试：SQLite + fakeredis + mock 作业主机执行器。"""

    async def test_steps_all_success(self, client, db_factory, fake_redis,
                                     monkeypatch, engine_setup):
        """两步骤串行全成功：execution/工单终态 success，退出码归档，日志落盘。"""
        env = await _engine_env(client)
        eid = await _submitted_execution_id(
            client, env, db_factory,
            steps=[_step(), _step("第二步")],
        )
        audit_entries = []
        monkeypatch.setattr(audit, "log", lambda **kwargs: audit_entries.append(kwargs))
        monkeypatch.setattr(pipeline.ssh_runner, "run_shell_on_job_host",
                            _fake_job_host_runner())

        await pipeline.run_execution(eid)

        assert {entry["action"] for entry in audit_entries}.isdisjoint(
            {"execution.start", "execution.finish"}
        )

        async with db_factory() as s:
            execution = await s.get(Execution, eid)
            assert execution.status == "success"
            assert execution.started_at is not None and execution.finished_at is not None
            ticket = await s.get(Ticket, execution.ticket_id)
            assert (ticket.status, ticket.interrupt_reason) == ("success", None)
            steps = list((await s.execute(
                select(ExecutionStep).order_by(ExecutionStep.step_order))).scalars())
            assert [st.step_order for st in steps] == [1, 2]
            assert all(st.status == "success" and st.exit_code == 0
                       and st.error_summary is None for st in steps)

        # 事件回放列表有内容且最后一条是 execution 终态
        tail = await events.fetch_events_since(eid, 0)
        assert tail[-1]["kind"] == "execution_status"
        assert tail[-1]["data"]["status"] == "success"
        # 日志已按 {dir}/{eid}/step_{n}.log 落盘（每步单文件，无主机层级）
        assert os.path.exists(os.path.join(str(engine_setup), str(eid), "step_1.log"))
        assert os.path.exists(os.path.join(str(engine_setup), str(eid), "step_2.log"))

    async def test_fail_fast_skips_rest(self, client, db_factory, fake_redis,
                                        monkeypatch, engine_setup):
        """fail_fast：步骤 1 失败后中断，步骤 2 不再派发（保持 pending），终态 failed。"""
        env = await _engine_env(client)
        eid = await _submitted_execution_id(
            client, env, db_factory,
            steps=[_step(), _step("第二步")],
        )
        monkeypatch.setattr(pipeline.ssh_runner, "run_shell_on_job_host",
                            _fake_job_host_runner({1: "boom"}))

        await pipeline.run_execution(eid)

        async with db_factory() as s:
            execution = await s.get(Execution, eid)
            ticket = await s.get(Ticket, execution.ticket_id)
            assert (execution.status, ticket.status) == ("failed", "failed")
            steps = list((await s.execute(
                select(ExecutionStep).order_by(ExecutionStep.step_order))).scalars())
            # 步骤1 失败中断；步骤2 从未启动（保持 pending）
            assert (steps[0].status, steps[0].exit_code, steps[0].error_summary) == ("failed", 1, "boom")
            assert steps[1].status == "pending"

        tail = await events.fetch_events_since(eid, 0)
        assert tail[-1]["data"]["status"] == "failed"

    async def test_abort_before_start(self, client, db_factory, fake_redis,
                                      monkeypatch, engine_setup):
        """queued 阶段下发 abort：检查点①直接终止，步骤未派发保持 pending，信号键清理。"""
        env = await _engine_env(client)
        eid = await _submitted_execution_id(client, env, db_factory)
        monkeypatch.setattr(pipeline.ssh_runner, "run_shell_on_job_host",
                            _fake_job_host_runner())
        await ctrl.send_signal(eid, ctrl.SIG_ABORT)  # worker 认领前用户已点中止

        await pipeline.run_execution(eid)

        async with db_factory() as s:
            execution = await s.get(Execution, eid)
            ticket = await s.get(Ticket, execution.ticket_id)
            assert execution.status == "terminated"
            assert (ticket.status, ticket.interrupt_reason) == ("interrupted", "user_abort")
            steps = list((await s.execute(select(ExecutionStep))).scalars())
            assert all(st.status == "pending" for st in steps)
        # 执行结束后信号键被防御性清理
        assert await fake_redis.get(f"ops:ctrl:{eid}") is None


# ---------- 凭据环境变量注入（设计 2026-07-31：CRED_<ALIAS>_* 前置 export） ----------

def test_env_prelude_quoting():
    """单引号安全转义：值含单引号/换行（私钥）也能原样还原。"""
    from app.engine.ssh_runner import _env_prelude

    prelude = _env_prelude({"CRED_DB_SECRET": "pa'ss\nline2", "CRED_DB_USER": "root"})
    assert "export CRED_DB_SECRET='pa'\\''ss\nline2'" in prelude
    assert "export CRED_DB_USER='root'" in prelude


def test_build_cred_env():
    """按别名组装 CRED_<ALIAS大写>_USER/_SECRET；无口令不注入 _PASSPHRASE。"""
    from types import SimpleNamespace
    from app.core.security import encrypt_text
    from app.engine.pipeline import build_cred_env

    cred = SimpleNamespace(login_user="root", secret_enc=encrypt_text("s3cret"),
                           passphrase_enc=None)
    env = build_cred_env("mysql", cred)
    assert env == {"CRED_MYSQL_USER": "root", "CRED_MYSQL_SECRET": "s3cret"}
