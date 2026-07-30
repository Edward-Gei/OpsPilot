"""M5 执行引擎测试。

覆盖验收点（05-任务拆解 M5）：
- Ansible PLAY RECAP 解析 / 单主机状态映射 / inventory 生成；
- 分批切片 _chunk 与 Jinja2 沙箱脚本渲染；
- 事件总线：发号递增 / since_seq 增量回放；
- Pipeline 调度器（mock SSH 执行器）：
  * 分批推进全成功 → execution/工单 success + batch_no 修正 + 日志落盘；
  * fail_fast 批内失败 → 剩余主机/步骤置 skipped，终态 failed；
  * queued 阶段下发 abort → 检查点①直接 terminated + 工单 interrupted(user_abort)。
"""
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.constants import CredentialAuthType
from app.core.redis import KEY_EXEC_EVENT_LIST
from app.core.security import encrypt_text
from app.engine import ansible_runner, control as ctrl, events, pipeline
from app.models.execution import Execution, ExecutionStep, ExecutionStepHost
from app.models.ticket import Ticket
from tests.conftest import auth_header, login_for_tokens
from tests.test_ticket_api import CRED_PAYLOAD, _create_template, _step, _submit


# ---------- Ansible RECAP 解析 ----------

RECAP_OUTPUT = """
PLAY [targets] *****************************************************************

TASK [Gathering Facts] *********************************************************
ok: [10.9.1.1]
fatal: [10.9.1.2]: FAILED! => {"msg": "boom"}
fatal: [10.9.1.3]: UNREACHABLE! => {"msg": "timed out"}

PLAY RECAP *********************************************************************
10.9.1.1                   : ok=2    changed=1    unreachable=0    failed=0    skipped=0    rescued=0    ignored=0
10.9.1.2                   : ok=1    changed=0    unreachable=0    failed=1    skipped=0    rescued=0    ignored=0
10.9.1.3                   : ok=0    changed=0    unreachable=1    failed=0    skipped=0
"""


class TestAnsibleRecap:
    """PLAY RECAP 解析与主机状态映射（纯函数单测）。"""

    def test_parse_play_recap(self):
        """仅解析 RECAP 段之后的行；统计值转 int。"""
        recap = ansible_runner.parse_play_recap(RECAP_OUTPUT)
        assert set(recap) == {"10.9.1.1", "10.9.1.2", "10.9.1.3"}
        assert recap["10.9.1.1"]["ok"] == 2 and recap["10.9.1.1"]["failed"] == 0
        assert recap["10.9.1.2"]["failed"] == 1
        assert recap["10.9.1.3"]["unreachable"] == 1

    def test_parse_recap_missing(self):
        """无 RECAP（语法错误提前退出）→ 空 dict，由调用方按整批失败处理。"""
        assert ansible_runner.parse_play_recap("ERROR! Syntax Error while loading YAML") == {}

    def test_recap_host_status(self):
        """unreachable 优先于 failed；无失败 → success。"""
        assert ansible_runner.recap_host_status({"ok": 2, "failed": 0, "unreachable": 0}) == ("success", None)
        status, summary = ansible_runner.recap_host_status({"ok": 1, "failed": 2, "unreachable": 0})
        assert status == "failed" and "failed=2" in summary
        status, summary = ansible_runner.recap_host_status({"ok": 0, "failed": 1, "unreachable": 1})
        assert status == "failed" and "unreachable" in summary

    def test_build_inventory_password_and_key(self):
        """密码凭据写 ansible_password；私钥凭据引用 ./key.pem；含 host key 关闭参数。"""
        hosts = [SimpleNamespace(ip="10.9.1.1", ssh_port=2222)]
        pwd_cred = SimpleNamespace(
            auth_type=CredentialAuthType.PASSWORD.value, login_user="root",
            secret_enc=encrypt_text("S3cret!"),
        )
        inv = ansible_runner.build_inventory(hosts, pwd_cred)
        assert "[targets]" in inv
        assert "10.9.1.1 ansible_host=10.9.1.1 ansible_port=2222 ansible_user=root" in inv
        assert "ansible_password=S3cret!" in inv
        assert "StrictHostKeyChecking=no" in inv

        key_cred = SimpleNamespace(
            auth_type=CredentialAuthType.PRIVATE_KEY.value, login_user="deploy",
            secret_enc=encrypt_text("-----BEGIN KEY-----"),
        )
        inv = ansible_runner.build_inventory(hosts, key_cred)
        assert "ansible_ssh_private_key_file=./key.pem" in inv
        assert "ansible_password" not in inv


class TestChunkAndRender:
    """分批切片与脚本参数渲染（纯函数单测）。"""

    def test_chunk(self):
        """size<=0 或 >= 总数 → 单批；否则按 size 均分（末批可不满）。"""
        assert pipeline._chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
        assert pipeline._chunk([1, 2, 3], 0) == [[1, 2, 3]]
        assert pipeline._chunk([1, 2], 5) == [[1, 2]]

    def test_render_script(self):
        """Jinja2 沙箱渲染参数替换；保留脚本尾部换行。"""
        out = pipeline.render_script("systemctl restart {{ svc }}\n", {"svc": "nginx"})
        assert out == "systemctl restart nginx\n"


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


# ---------- Pipeline 调度器（mock 执行器） ----------

async def _multi_host_env(client, host_count: int) -> dict:
    """引擎测试前置：N 台主机 + 1 应用 + 1 凭据。"""
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_h = auth_header(await login_for_tokens(client, "ops1"))
    host_ids = []
    for i in range(1, host_count + 1):
        resp = await client.post("/api/v1/cmdb/hosts", headers=ops_h, json={
            "hostname": f"eng-{i}", "ip": f"10.9.1.{i}", "environment": "prod",
            "status": "online", "ssh_port": 22,
        })
        host_ids.append(resp.json()["data"]["id"])
    resp = await client.post("/api/v1/cmdb/apps", headers=ops_h,
                             json={"name": "引擎测试应用", "deploy_type": "docker",
                                   "host_ids": host_ids})
    app_id = resp.json()["data"]["id"]
    resp = await client.post("/api/v1/credentials", json=CRED_PAYLOAD, headers=admin_h)
    cred_id = resp.json()["data"]["id"]
    return {"admin_h": admin_h, "ops_h": ops_h, "app_id": app_id, "cred_id": cred_id}


def _fake_shell_runner(failed_ips: dict[str, str] | None = None):
    """构造 mock 版 run_shell_on_host：failed_ips 中的 IP 返回 failed，其余 success。"""
    failed_ips = failed_ips or {}

    async def _run(*, ip, port, credential, script, timeout, step_order, log, control):
        log.write(step_order, ip, f"[fake] run on {ip}: {script.splitlines()[-1]}")
        if ip in failed_ips:
            return "failed", 1, failed_ips[ip]
        return "success", 0, None

    return _run


@pytest.fixture
def engine_setup(db_factory, monkeypatch, tmp_path):
    """引擎运行环境：会话工厂指向测试库、日志目录指向临时目录、重建全局信号量。"""
    monkeypatch.setattr(pipeline, "async_session_factory", db_factory)
    monkeypatch.setattr(settings, "exec_log_dir", str(tmp_path))
    pipeline.init_global_semaphore(10)
    return tmp_path


async def _submitted_execution_id(client, env, db_factory, **tpl_override) -> int:
    """免审模板提交一张工单，返回 execution_id。"""
    tpl_id = await _create_template(client, env, **tpl_override)
    await _submit(client, env["ops_h"], tpl_id)
    async with db_factory() as s:
        return (await s.execute(select(Execution.id))).scalar_one()


class TestPipelineScheduler:
    """调度器集成测试：SQLite + fakeredis + mock SSH 执行器。"""

    async def test_batches_all_success(self, client, db_factory, fake_redis,
                                       monkeypatch, engine_setup):
        """4 主机 batch_size=2 全成功：分两批推进，终态 success，日志落盘。"""
        env = await _multi_host_env(client, 4)
        eid = await _submitted_execution_id(
            client, env, db_factory,
            exec_strategy={"concurrency": 5, "batch_size": 2, "timeout": 600},
        )
        monkeypatch.setattr(pipeline.ssh_runner, "run_shell_on_host", _fake_shell_runner())

        await pipeline.run_execution(eid)

        async with db_factory() as s:
            execution = await s.get(Execution, eid)
            assert execution.status == "success"
            assert execution.started_at is not None and execution.finished_at is not None
            ticket = await s.get(Ticket, execution.ticket_id)
            assert (ticket.status, ticket.interrupt_reason) == ("success", None)
            step = (await s.execute(select(ExecutionStep))).scalar_one()
            assert (step.status, step.total_batch, step.current_batch) == ("success", 2, 2)
            assert (step.success_count, step.failed_count) == (4, 0)
            hosts = list((await s.execute(
                select(ExecutionStepHost).order_by(ExecutionStepHost.id))).scalars())
            assert [h.batch_no for h in hosts] == [1, 1, 2, 2]  # 分批修正
            assert all(h.status == "success" and h.exit_code == 0 for h in hosts)

        # 事件回放列表有内容且最后一条是 execution 终态
        tail = await events.fetch_events_since(eid, 0)
        assert tail[-1]["kind"] == "execution_status"
        assert tail[-1]["data"]["status"] == "success"
        # 日志已按 {dir}/{eid}/step_{n}/{ip}.log 落盘
        assert os.path.exists(os.path.join(str(engine_setup), str(eid), "step_1", "10.9.1.1.log"))

    async def test_fail_fast_skips_rest(self, client, db_factory, fake_redis,
                                        monkeypatch, engine_setup):
        """fail_fast：第一批失败后剩余批次/步骤不再派发，pending 统一置 skipped。"""
        env = await _multi_host_env(client, 4)
        eid = await _submitted_execution_id(
            client, env, db_factory,
            steps=[_step(env), _step(env, name="第二步")],
            exec_strategy={"concurrency": 5, "batch_size": 2, "timeout": 600},
        )
        monkeypatch.setattr(pipeline.ssh_runner, "run_shell_on_host",
                            _fake_shell_runner({"10.9.1.1": "boom"}))

        await pipeline.run_execution(eid)

        async with db_factory() as s:
            execution = await s.get(Execution, eid)
            ticket = await s.get(Ticket, execution.ticket_id)
            assert (execution.status, ticket.status) == ("failed", "failed")
            steps = list((await s.execute(
                select(ExecutionStep).order_by(ExecutionStep.step_order))).scalars())
            # 步骤1 失败中断；步骤2 从未启动（保持 pending）
            assert (steps[0].status, steps[0].success_count, steps[0].failed_count) == ("failed", 1, 1)
            assert steps[1].status == "pending"
            hosts = list((await s.execute(
                select(ExecutionStepHost).order_by(ExecutionStepHost.id))).scalars())
            by_status = {}
            for h in hosts:
                by_status[h.status] = by_status.get(h.status, 0) + 1
            # 步骤1批1：1 失败 + 1 成功；步骤1批2（2台）与步骤2全量（4台）→ skipped
            assert by_status == {"failed": 1, "success": 1, "skipped": 6}
            failed_host = next(h for h in hosts if h.status == "failed")
            assert (failed_host.ip, failed_host.error_summary) == ("10.9.1.1", "boom")

    async def test_abort_before_start(self, client, db_factory, fake_redis,
                                      monkeypatch, engine_setup):
        """queued 阶段下发 abort：检查点①直接终止，全部主机 skipped，信号键清理。"""
        env = await _multi_host_env(client, 2)
        eid = await _submitted_execution_id(client, env, db_factory)
        monkeypatch.setattr(pipeline.ssh_runner, "run_shell_on_host", _fake_shell_runner())
        await ctrl.send_signal(eid, ctrl.SIG_ABORT)  # worker 认领前用户已点中止

        await pipeline.run_execution(eid)

        async with db_factory() as s:
            execution = await s.get(Execution, eid)
            ticket = await s.get(Ticket, execution.ticket_id)
            assert execution.status == "terminated"
            assert (ticket.status, ticket.interrupt_reason) == ("interrupted", "user_abort")
            hosts = list((await s.execute(select(ExecutionStepHost))).scalars())
            assert all(h.status == "skipped" for h in hosts)
        # 执行结束后信号键被防御性清理
        assert await fake_redis.get(f"ops:ctrl:{eid}") is None
