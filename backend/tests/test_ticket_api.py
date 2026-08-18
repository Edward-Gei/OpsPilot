"""V2 工单中心接口测试。

覆盖验收点ﾀ05-任务拆解 M4，V2 修订 + 执行范式改造ﾉ：
- 可用模板列表：enabled + visible_role_ids 过滤；
- 提交表单描述：各步骤 params_schema 同名合并、fixed 参数不外显；
- 提交只填参数：标题=模板名，多重快照固化（提交后改模板/作业主机不影响工单）；
- 参数校验：未定义键/固定值键/必填缺失 40001，缺省补默认值；
- 节点审批推进 / 越级 40302 / 驳回意见必填 / 驳回关闭 / 撤回（含模板禁止撤回）；
- 末节点通过 → queued + Execution(queued) + 预建执行子表 + XADD ops:exec:queue（M5）；
- 免审模板：提交直接 queued；
- 待办列表 = 当前步骤审批角色 ∩ 我的角色；通知按模板 notify_rules 落 record（M6 打桩）。
"""
from sqlalchemy import select

from app.core.redis import EXEC_QUEUE
from app.engine import todo_events
from app.models.auth import User, UserRole
from app.models.execution import Execution
from app.models.notify import NotificationRecord
from app.services import ticket_service
from tests.conftest import TEST_PASSWORD_HASH, auth_header, login_for_tokens


JOB_HOST_PAYLOAD = {
    "name": "tk-agent-01", "ip": "10.9.0.1", "ssh_port": 22,
    "workdir": "/opt/opspilot/workspace",
}

CRED_PAYLOAD = {
    "name": "tk-cred-01", "login_user": "root", "auth_type": "password",
    "secret": "S3cret!pass",
}


# ---------- 测试辅助 ----------

async def _base_env(client) -> dict:
    """公共前置：admin/ops 登录 + 1 凭据 + 1 作业主机（主机关联凭据）。"""
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_h = auth_header(await login_for_tokens(client, "ops1"))

    resp = await client.post("/api/v1/credentials", json=CRED_PAYLOAD, headers=admin_h)
    cred_id = resp.json()["data"]["id"]
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "credential_id": cred_id},
                             headers=admin_h)
    job_host_id = resp.json()["data"]["id"]
    return {"admin_h": admin_h, "ops_h": ops_h, "job_host_id": job_host_id,
            "credential_id": cred_id}


def _step(env: dict, **override) -> dict:
    """标准流程步骤（shell + svc 参数）。"""
    return {
        "name": "重启服务",
        "script_type": "shell",
        "content": "#!/bin/bash\nsystemctl restart {{ svc }}",
        "params_schema": [
            {"name": "svc", "label": "服务名", "default": "nginx", "required": True}
        ],
        "timeout": 300,
        **override,
    }


def _merge_params(steps: list[dict]) -> list[dict]:
    """把测试步骤中的参数定义迁移为流程模板级参数。"""
    merged: dict[str, dict] = {}
    for step in steps:
        for param in step.get("params_schema", []):
            current = merged.setdefault(param["name"], {
                "name": param["name"], "label": param.get("label"),
                "source": "fixed" if param.get("fixed") else "user",
                "input_type": "text", "options": [],
                "default": param.get("default"), "required": param.get("required", False),
                "description": param.get("description"),
            })
            current["required"] = current.get("required", False) or param.get("required", False)
    return list(merged.values())


async def _create_template(client, env, *, approval_roles: list[int] | None = None, **override) -> int:
    """创建流程模板和工单模板入口，approval_roles 按步骤顺序配置审批角色。"""
    template_name = override.pop("name", "重启 Nginx")
    raw_steps = override.pop("steps", [_step(env)])
    approval_roles = approval_roles or []
    while len(raw_steps) < len(approval_roles):
        raw_steps.append(_step(env, name=f"步骤 {len(raw_steps) + 1}"))
    process_payload = {
        "name": override.pop("process_name", f"{template_name} 流程"),
        "description": "滚动重启流程",
        "exec_strategy": {"timeout": 600, "fail_fast": True, "kill_on_stop": False},
        "steps": [
            {"name": step["name"], "script_type": step["script_type"],
             "content": step["content"], "timeout": step["timeout"],
             "approval_role_id": approval_roles[index] if index < len(approval_roles) else None}
            for index, step in enumerate(raw_steps)
        ],
    }
    process_resp = await client.post("/api/v1/process-templates", json=process_payload, headers=env["ops_h"])
    process_body = process_resp.json()
    assert process_body["code"] == 0, process_body
    payload = {
        "name": template_name,
        "type": override.pop("type", "daily_ops"),
        "description": override.pop("description", "滚动重启"),
        "job_host_id": env["job_host_id"],
        "process_template_id": process_body["data"]["id"],
        "params_schema": _merge_params(raw_steps),
        "notify_rules": override.pop("notify_rules", []),
        "visible_role_ids": override.pop("visible_role_ids", []),
        "allow_withdraw": override.pop("allow_withdraw", True),
        **override,
    }
    resp = await client.post("/api/v1/templates", json=payload, headers=env["ops_h"])
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


async def _submit(client, headers, tpl_id: int, params: dict | None = None) -> dict:
    """提交工单并断言成功，返回 {id, ticket_no, status, current_step}。"""
    resp = await client.post("/api/v1/tickets",
                             json={"template_id": tpl_id, "params": params or {}}, headers=headers)
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


async def _approver_headers(client, db_factory, seed, username="appr1") -> dict:
    """手工插入 approver 角色用户（conftest 种子无审批人）并返回其 Bearer 头。"""
    async with db_factory() as session:
        u = User(username=username, password_hash=TEST_PASSWORD_HASH, display_name=username,
                 source="local", status="active", must_change_password=False)
        session.add(u)
        await session.flush()
        session.add(UserRole(user_id=u.id, role_id=seed["roles"]["approver"]))
        await session.commit()
    return auth_header(await login_for_tokens(client, username))


async def _notify_rows(db_factory) -> list[tuple[str, str | None]]:
    """读取全部通知记录 (event, receiver)（打桩落库断言用）。"""
    async with db_factory() as session:
        rows = await session.execute(
            select(NotificationRecord.event, NotificationRecord.receiver)
            .order_by(NotificationRecord.id)
        )
        return [tuple(r) for r in rows]


class TestUsableTemplatesAndForm:
    """可用模板列表 + 提交表单描述。"""

    async def test_visible_templates_filtered(self, client, seed):
        """enabled + visible_role_ids 过滤；禁用/不在范围内的模板不出现。"""
        env = await _base_env(client)
        open_id = await _create_template(client, env)
        await _create_template(client, env, name="审批人专用",
                               visible_role_ids=[seed["roles"]["approver"]])

        items = (await client.get("/api/v1/tickets/templates",
                                  headers=env["ops_h"])).json()["data"]["items"]
        assert [t["id"] for t in items] == [open_id]

        # 禁用后从可用列表消失
        await client.put(f"/api/v1/templates/{open_id}/status",
                         json={"status": "disabled"}, headers=env["ops_h"])
        items = (await client.get("/api/v1/tickets/templates",
                                  headers=env["ops_h"])).json()["data"]["items"]
        assert items == []

    async def test_form_merges_params_excludes_fixed(self, client, seed):
        """跨步骤同名参数合并（任一必填即必填）；fixed 参数不外显；预览完整。"""
        env = await _base_env(client)
        step2 = _step(env, name="校验端口", content="curl 127.0.0.1:{{ port }} # {{ svc }}",
                      params_schema=[
                          {"name": "svc", "label": "服务名", "required": False},
                          {"name": "port", "default": "8080", "fixed": True},
                      ])
        tpl_id = await _create_template(
            client, env, steps=[_step(env), step2],
            approval_roles=[seed["roles"]["approver"]],
        )

        form = (await client.get(f"/api/v1/tickets/templates/{tpl_id}/form",
                                 headers=env["ops_h"])).json()["data"]
        # svc 合并为一条且必填（首现定义为准）；fixed 的 port 不外显
        assert [p["name"] for p in form["params"]] == ["svc"]
        assert form["params"][0]["required"] is True
        assert form["template"]["name"] == "重启 Nginx"
        assert form["job_host"]["ip"] == "10.9.0.1"
        assert [s["step_order"] for s in form["steps"]] == [1, 2]
        assert form["steps"][0]["approval_role_id"] == seed["roles"]["approver"]
        assert form["steps"][0]["approval_role_name"] == "审批人"
        assert form["exec_strategy"]["timeout"] == 600

    async def test_form_guard(self, client, seed):
        """禁用模板 40901；不在可见范围 40302。"""
        env = await _base_env(client)
        tpl_id = await _create_template(client, env)
        await client.put(f"/api/v1/templates/{tpl_id}/status",
                         json={"status": "disabled"}, headers=env["ops_h"])
        resp = await client.get(f"/api/v1/tickets/templates/{tpl_id}/form", headers=env["ops_h"])
        assert resp.json()["code"] == 40901

        limited = await _create_template(client, env, name="审批人专用",
                                         visible_role_ids=[seed["roles"]["approver"]])
        resp = await client.get(f"/api/v1/tickets/templates/{limited}/form", headers=env["ops_h"])
        assert resp.json()["code"] == 40302


class TestSubmit:
    """提交：五重快照 / 参数校验 / 免审直跑 / 提交守卫。"""

    async def test_submit_snapshot_and_single_node_approve(self, client, db_factory, seed, fake_redis):
        """单步骤审批全链路：提交固化快照 → 待办可见 → 通过 → queued + 入队 + 通知落库。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(
            client, env, approval_roles=[seed["roles"]["approver"]],
            notify_rules=[{"event": "ticket.approved", "receivers": ["creator"], "channels": []}],
        )

        data = await _submit(client, env["ops_h"], tpl_id)
        assert data["status"] == "approving" and data["current_step"] == 1

        # 提交后改模板步骤内容（升版）+ 改作业主机 IP → 工单快照不受影响（多重快照固化）
        template_detail = (await client.get(f"/api/v1/templates/{tpl_id}", headers=env["ops_h"])).json()["data"]
        process_id = template_detail["process_template_id"]
        resp = await client.put(f"/api/v1/process-templates/{process_id}", headers=env["ops_h"], json={
            "name": "重启 Nginx 流程", "description": "滚动重启流程",
            "exec_strategy": {"timeout": 600, "fail_fast": True, "kill_on_stop": False},
            "steps": [{"name": "重启服务", "script_type": "shell", "content": "echo changed",
                       "timeout": 300, "approval_role_id": seed["roles"]["approver"]}],
        })
        assert resp.json()["code"] == 0
        resp = await client.put(f"/api/v1/job-hosts/{env['job_host_id']}", headers=env["admin_h"],
                                json={"ip": "10.9.0.99"})
        assert resp.json()["code"] == 0

        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert detail["title"] == "重启 Nginx"  # 标题=模板名快照
        assert detail["params"] == {"svc": "nginx"}  # 缺省补默认值
        assert detail["steps"][0]["content_snap"].startswith("#!/bin/bash")
        assert detail["job_host"]["ip"] == "10.9.0.1"  # 作业主机快照不受后续改动影响
        assert detail["flow_snap"]["steps"][0]["approval_role_id"] == seed["roles"]["approver"]
        assert detail["steps"][0]["approval_role_id"] == seed["roles"]["approver"]

        # 待办：approver 可见；admin 无 approver 角色不可见；ops 无 ticket:approve 40301
        assert (await client.get("/api/v1/tickets/todo", headers=appr_h)).json()["data"]["total"] == 1
        assert (await client.get("/api/v1/tickets/todo", headers=env["admin_h"])).json()["data"]["total"] == 0
        assert (await client.get("/api/v1/tickets/todo", headers=env["ops_h"])).json()["code"] == 40301

        # 末节点通过 → queued + Execution(queued, auto_approve) + XADD 队列（M5：worker 认领后才置 running）
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve", "comment": "同意"}, headers=appr_h)
        assert resp.json()["data"] == {"status": "queued", "current_step": 0}
        async with db_factory() as session:
            execution = (await session.execute(select(Execution))).scalar_one()
            assert execution.status == "queued"
            assert execution.total_steps == 1
        assert await fake_redis.xlen(EXEC_QUEUE) == 1

        # 通知打桩：待审批（默认收件人=节点角色成员）+ 审批通过（notify_rules 指定 creator）
        rows = await _notify_rows(db_factory)
        assert [e for e, _ in rows] == ["ticket.pending_approval", "ticket.approved"]
        assert rows[0][1] == "appr1" and rows[1][1] == "ops1"

    async def test_submit_param_validation(self, client, seed):
        """未定义键/固定值键 40001；必填缺失 40001；fixed 取默认值落步骤参数。"""
        env = await _base_env(client)
        step2 = _step(env, name="校验端口", content="curl :{{ port }}",
                      params_schema=[{"name": "port", "default": "8080", "fixed": True}])
        tpl_id = await _create_template(client, env, steps=[_step(env), step2])

        # 未定义键 / 固定值键均拒绝
        resp = await client.post("/api/v1/tickets", headers=env["ops_h"],
                                 json={"template_id": tpl_id, "params": {"bad_key": "x"}})
        assert resp.json()["code"] == 40001
        resp = await client.post("/api/v1/tickets", headers=env["ops_h"],
                                 json={"template_id": tpl_id, "params": {"port": "9999"}})
        assert resp.json()["code"] == 40001

        # 必填且无默认值缺失 → 40001
        no_default = await _create_template(
            client, env, name="无默认模板",
            steps=[_step(env, params_schema=[{"name": "ver", "required": True}])])
        resp = await client.post("/api/v1/tickets", headers=env["ops_h"],
                                 json={"template_id": no_default, "params": {}})
        assert resp.json()["code"] == 40001

        # 正常提交：ticket.params 只存提交人参数；步骤参数含 fixed 生效值
        data = await _submit(client, env["ops_h"], tpl_id, {"svc": "mysql"})
        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert detail["params"] == {"svc": "mysql"}
        assert detail["steps"][0]["params"] == {"svc": "mysql", "port": "8080"}
        assert detail["steps"][1]["params"] == {"svc": "mysql", "port": "8080"}

    async def test_submit_prepared_fixed_param(self, client):
        """提交向导携带 prepare_id 时，预生成的 fixed 参数应直接采用默认值。"""
        env = await _base_env(client)
        step = _step(env, params_schema=[{"name": "GIT_REPO", "default": "ops/repo", "fixed": True}])
        tpl_id = await _create_template(client, env, steps=[step])

        prepared = await client.post("/api/v1/tickets/prepare", headers=env["ops_h"],
                                     json={"template_id": tpl_id, "params": {}})
        prepared_data = prepared.json()["data"]
        resp = await client.post(
            "/api/v1/tickets", headers=env["ops_h"],
            json={"template_id": tpl_id, "params": {}, "prepare_id": prepared_data["prepare_id"]},
        )

        assert resp.json()["code"] == 0
        detail = (await client.get(
            f"/api/v1/tickets/{resp.json()['data']['id']}", headers=env["ops_h"]
        )).json()["data"]
        assert detail["params"] == {}
        assert detail["steps"][0]["params"] == {"GIT_REPO": "ops/repo"}

    async def test_no_approval_direct_run(self, client, db_factory, fake_redis):
        """免审模板：提交直接 queued 入队，流程快照无审批角色，无通知事件。"""
        env = await _base_env(client)
        tpl_id = await _create_template(client, env)  # approval_roles=None → 免审

        data = await _submit(client, env["ops_h"], tpl_id)
        assert data["status"] == "queued" and data["current_step"] == 0
        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert not detail["flow_snap"]["steps"][0]["approval_role_id"] and detail["total_steps"] == 1
        assert await fake_redis.xlen(EXEC_QUEUE) == 1
        assert await _notify_rows(db_factory) == []

    async def test_submit_guard(self, client, seed):
        """禁用模板 40901；可见范围外 40302。"""
        env = await _base_env(client)
        tpl_id = await _create_template(client, env)
        await client.put(f"/api/v1/templates/{tpl_id}/status",
                         json={"status": "disabled"}, headers=env["ops_h"])
        resp = await client.post("/api/v1/tickets", headers=env["ops_h"],
                                 json={"template_id": tpl_id, "params": {}})
        assert resp.json()["code"] == 40901

        limited = await _create_template(client, env, name="审批人专用",
                                         visible_role_ids=[seed["roles"]["approver"]])
        resp = await client.post("/api/v1/tickets", headers=env["ops_h"],
                                 json={"template_id": limited, "params": {}})
        assert resp.json()["code"] == 40302


class TestApproveAndCancel:
    """节点审批推进 / 驳回 / 撤回。"""

    async def test_multi_node_flow(self, client, db_factory, seed):
        """两步骤审批：逐步骤推进；非当前步骤角色审批 40302；时间线完整。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(client, env, approval_roles=[
            seed["roles"]["approver"], seed["roles"]["admin"],
        ], steps=[_step(env), _step(env, name="部署服务")])
        data = await _submit(client, env["ops_h"], tpl_id)

        # 第 1 节点需 approver：admin 越级审批 -> 40302
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve"}, headers=env["admin_h"])
        assert resp.json()["code"] == 40302

        # 第 1 步通过 → approving / current_step=2
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve"}, headers=appr_h)
        assert resp.json()["data"] == {"status": "approving", "current_step": 2}

        # 第 2 节点需 admin：approver 再批 -> 40302；admin 通过 → queued
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve"}, headers=appr_h)
        assert resp.json()["code"] == 40302
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve", "comment": "终审通过"}, headers=env["admin_h"])
        assert resp.json()["data"]["status"] == "queued"

        # 审批时间线两条记录且带审批人名
        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert [(a["step_order"], a["action"], a["approver_name"]) for a in detail["approvals"]] == [
            (1, "approve", "appr1"), (2, "approve", "admin"),
        ]

    async def test_approval_changes_publish_todo_events_to_affected_role_members(
        self, client, db_factory, seed, fake_redis,
    ):
        """待审批的分配、转移、驳回和撤回应只通知受影响的审批角色成员。"""
        env = await _base_env(client)
        appr1_h = await _approver_headers(client, db_factory, seed, "appr1")
        await _approver_headers(client, db_factory, seed, "appr2")
        async with db_factory() as session:
            approver_ids = list((await session.execute(
                select(User.id).where(User.username.in_(["appr1", "appr2"])).order_by(User.id)
            )).scalars())
        tpl_id = await _create_template(client, env, approval_roles=[
            seed["roles"]["approver"], seed["roles"]["admin"],
        ], steps=[_step(env), _step(env, name="终审")])

        first = await _submit(client, env["ops_h"], tpl_id)
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[0], 0)] == [1]
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[1], 0)] == [1]
        assert await todo_events.fetch_since(seed["users"]["newbie"], 0) == []

        response = await client.post(
            f"/api/v1/tickets/{first['id']}/approve",
            json={"action": "approve"}, headers=appr1_h,
        )
        assert response.json()["data"] == {"status": "approving", "current_step": 2}
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[0], 0)] == [1, 2]
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[1], 0)] == [1, 2]
        assert [event["seq"] for event in await todo_events.fetch_since(seed["users"]["admin"], 0)] == [1]

        response = await client.post(
            f"/api/v1/tickets/{first['id']}/approve",
            json={"action": "approve", "comment": "终审通过"}, headers=env["admin_h"],
        )
        assert response.json()["data"] == {"status": "queued", "current_step": 0}
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[0], 0)] == [1, 2]
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[1], 0)] == [1, 2]
        assert [event["seq"] for event in await todo_events.fetch_since(seed["users"]["admin"], 0)] == [1, 2]
        assert await todo_events.fetch_since(seed["users"]["newbie"], 0) == []

        rejected = await _submit(client, env["ops_h"], tpl_id)
        response = await client.post(
            f"/api/v1/tickets/{rejected['id']}/approve",
            json={"action": "reject", "comment": "窗口期不合适"}, headers=appr1_h,
        )
        assert response.json()["data"] == {"status": "rejected", "current_step": 0}
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[0], 0)] == [1, 2, 3, 4]
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[1], 0)] == [1, 2, 3, 4]

        cancelled = await _submit(client, env["ops_h"], tpl_id)
        response = await client.post(f"/api/v1/tickets/{cancelled['id']}/cancel", headers=env["ops_h"])
        assert response.json()["data"] == {"status": "cancelled"}
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[0], 0)] == [1, 2, 3, 4, 5, 6]
        assert [event["seq"] for event in await todo_events.fetch_since(approver_ids[1], 0)] == [1, 2, 3, 4, 5, 6]
        assert [event["seq"] for event in await todo_events.fetch_since(seed["users"]["admin"], 0)] == [1, 2]
        assert await todo_events.fetch_since(seed["users"]["newbie"], 0) == []

    async def test_todo_publish_starts_after_approval_assignment_is_committed(
        self, client, db_factory, seed, monkeypatch,
    ):
        """发布待办事件时，独立会话已能看到新审批节点。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(client, env, approval_roles=[
            seed["roles"]["approver"], seed["roles"]["admin"],
        ], steps=[_step(env), _step(env, name="终审")])
        ticket = await _submit(client, env["ops_h"], tpl_id)
        published: list[list[int]] = []

        async def publish_after_commit(user_ids: list[int]):
            async with db_factory() as verification_session:
                todos, total = await ticket_service.todo_tickets(
                    verification_session, user_id=seed["users"]["admin"], page=1, page_size=20,
                )
            assert total == 1
            assert [item.id for item in todos] == [ticket["id"]]
            published.append(user_ids)

        monkeypatch.setattr(todo_events, "publish_for_users", publish_after_commit)
        response = await client.post(
            f"/api/v1/tickets/{ticket['id']}/approve",
            json={"action": "approve"}, headers=appr_h,
        )
        assert response.json()["data"] == {"status": "approving", "current_step": 2}
        assert seed["users"]["admin"] in published[0]

    async def test_todo_publish_failure_does_not_rollback_approval_api(
        self, client, db_factory, seed, monkeypatch,
    ):
        """Redis 发布失败不应改变成功审批请求及其已提交的状态。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(client, env, approval_roles=[seed["roles"]["approver"]])
        ticket = await _submit(client, env["ops_h"], tpl_id)

        async def publish_failure(user_ids: list[int]):
            raise RuntimeError("redis unavailable")

        monkeypatch.setattr(todo_events, "publish_for_users", publish_failure)
        response = await client.post(
            f"/api/v1/tickets/{ticket['id']}/approve",
            json={"action": "approve"}, headers=appr_h,
        )
        assert response.json()["data"] == {"status": "queued", "current_step": 0}
        detail = (await client.get(f"/api/v1/tickets/{ticket['id']}", headers=env["ops_h"])).json()["data"]
        assert detail["status"] == "queued"
        assert [(item["step_order"], item["action"]) for item in detail["approvals"]] == [(1, "approve")]

    async def test_reject_requires_comment_and_closes(self, client, db_factory, seed):
        """驳回意见必填 40001；驳回后 rejected 终态 + 通知创建人。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(
            client, env, approval_roles=[seed["roles"]["approver"]])
        data = await _submit(client, env["ops_h"], tpl_id)

        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "reject"}, headers=appr_h)
        assert resp.json()["code"] == 40001

        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "reject", "comment": "窗口期不合适"}, headers=appr_h)
        assert resp.json()["data"] == {"status": "rejected", "current_step": 0}
        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert detail["finished_at"] is not None
        assert detail["approvals"][0]["comment"] == "窗口期不合适"
        assert "ticket.rejected" in [e for e, _ in await _notify_rows(db_factory)]

        # 终态后不可再审批
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve"}, headers=appr_h)
        assert resp.json()["code"] == 40901

    async def test_cancel_rules(self, client, seed):
        """撤回：非创建人 40302；创建人审批中可撤回；模板禁止撤回 42201。"""
        env = await _base_env(client)
        tpl_id = await _create_template(
            client, env, approval_roles=[seed["roles"]["approver"]])
        data = await _submit(client, env["ops_h"], tpl_id)

        resp = await client.post(f"/api/v1/tickets/{data['id']}/cancel", headers=env["admin_h"])
        assert resp.json()["code"] == 40302
        resp = await client.post(f"/api/v1/tickets/{data['id']}/cancel", headers=env["ops_h"])
        assert resp.json()["data"]["status"] == "cancelled"
        # 终态后不可再撤回
        resp = await client.post(f"/api/v1/tickets/{data['id']}/cancel", headers=env["ops_h"])
        assert resp.json()["code"] == 40901

        # allow_withdraw=False 模板：撤回被拒 42201
        locked = await _create_template(
            client, env, name="禁止撤回模板", allow_withdraw=False,
            approval_roles=[seed["roles"]["approver"]])
        data = await _submit(client, env["ops_h"], locked)
        resp = await client.post(f"/api/v1/tickets/{data['id']}/cancel", headers=env["ops_h"])
        assert resp.json()["code"] == 42201

    async def test_list_filters(self, client, seed):
        """列表筛选：status/keyword 命中；标题=模板名。"""
        env = await _base_env(client)
        tpl_a = await _create_template(
            client, env, approval_roles=[seed["roles"]["approver"]])
        tpl_b = await _create_template(client, env, name="免审模板")
        data_a = await _submit(client, env["ops_h"], tpl_a)
        await _submit(client, env["ops_h"], tpl_b)

        resp = await client.get("/api/v1/tickets", params={"status": "approving"}, headers=env["ops_h"])
        body = resp.json()["data"]
        assert body["total"] == 1 and body["items"][0]["title"] == "重启 Nginx"
        resp = await client.get("/api/v1/tickets", params={"keyword": data_a["ticket_no"]},
                                headers=env["ops_h"])
        body = resp.json()["data"]
        assert body["total"] == 1 and body["items"][0]["ticket_no"] == data_a["ticket_no"]

    async def test_list_time_range(self, client, seed):
        """创建时间范围筛选：秒级 start/end 命中；回归——曾因缺括号把裸字符串传入 where 报 500。"""
        env = await _base_env(client)
        tpl = await _create_template(client, env, name="免审模板")
        await _submit(client, env["ops_h"], tpl)

        async def _total(params: dict) -> int:
            resp = await client.get("/api/v1/tickets", params=params, headers=env["ops_h"])
            return resp.json()["data"]["total"]

        # 秒级范围：包含当前时刻命中，未来区间不命中
        assert await _total({"start": "2000-01-01 00:00:00", "end": "2099-12-31 23:59:59"}) == 1
        assert await _total({"start": "2099-01-01 00:00:00", "end": "2099-12-31 23:59:59"}) == 0
        # 纯日期上界：自动补到当天末
        assert await _total({"end": "2099-12-31"}) == 1
