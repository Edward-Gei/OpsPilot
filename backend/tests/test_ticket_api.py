"""V2 工单中心接口测试。

覆盖验收点（05-任务拆解 M4，V2 修订）：
- 可用模板列表：enabled + visible_role_ids 过滤；
- 提交表单描述：各步骤 params_schema 同名合并、fixed 参数不外显；
- 提交只填参数：标题=模板名，五重快照固化（提交后改模板/主机不影响工单）；
- 参数校验：未定义键/固定值键/必填缺失 40001，缺省补默认值；
- 节点审批推进 / 越级 40302 / 驳回意见必填 / 驳回关闭 / 撤回（含模板禁止撤回）；
- 末节点通过 → queued + Execution(queued) + 预建执行子表 + XADD ops:exec:queue（M5）；
- 免审模板：提交直接 queued（triggered_by=no_approval）；
- 待办列表 = 当前节点角色 ∩ 我的角色；通知按模板 notify_rules 落 record（M6 打桩）。
"""
from sqlalchemy import select

from app.core.redis import EXEC_QUEUE
from app.models.auth import User, UserRole
from app.models.execution import Execution
from app.models.notify import NotificationRecord
from tests.conftest import TEST_PASSWORD_HASH, auth_header, login_for_tokens


HOST_PAYLOAD = {
    "hostname": "tk-web-01", "ip": "10.9.0.1", "environment": "prod",
    "status": "online", "ssh_port": 22,
}

CRED_PAYLOAD = {
    "name": "tk-root", "login_user": "root", "auth_type": "password", "secret": "S3cret!pass",
}


# ---------- 测试辅助 ----------

async def _base_env(client) -> dict:
    """公共前置：admin/ops 登录 + 1 主机 + 1 应用 + 1 凭据。"""
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_h = auth_header(await login_for_tokens(client, "ops1"))

    resp = await client.post("/api/v1/cmdb/hosts", json=HOST_PAYLOAD, headers=ops_h)
    host_id = resp.json()["data"]["id"]
    resp = await client.post(
        "/api/v1/cmdb/apps",
        json={"name": "订单服务", "deploy_type": "docker", "host_ids": [host_id]},
        headers=ops_h,
    )
    app_id = resp.json()["data"]["id"]
    resp = await client.post("/api/v1/credentials", json=CRED_PAYLOAD, headers=admin_h)
    cred_id = resp.json()["data"]["id"]
    return {"admin_h": admin_h, "ops_h": ops_h, "host_id": host_id,
            "app_id": app_id, "cred_id": cred_id}


def _step(env: dict, **override) -> dict:
    """标准模板步骤（shell + svc 参数）。"""
    return {
        "name": "重启服务",
        "script_type": "shell",
        "content": "#!/bin/bash\nsystemctl restart {{ svc }}",
        "params_schema": [
            {"name": "svc", "label": "服务名", "default": "nginx", "required": True}
        ],
        "credential_id": env["cred_id"],
        "timeout": 300,
        **override,
    }


async def _create_template(client, env, *, nodes: list[dict] | None = None, **override) -> int:
    """创建模板：nodes 传审批节点列表（None=免审），返回模板 id。"""
    payload = {
        "name": "重启 Nginx",
        "type": "ops",
        "description": "滚动重启",
        "app_id": env["app_id"],
        "steps": [_step(env)],
        "exec_strategy": {"concurrency": 5, "batch_size": 0, "timeout": 600},
        "approval_enabled": bool(nodes),
        "approval_nodes": nodes or [],
        **override,
    }
    resp = await client.post("/api/v1/templates", json=payload, headers=env["ops_h"])
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


async def _submit(client, headers, tpl_id: int, params: dict | None = None) -> dict:
    """提交工单并断言成功，返回 {id, ticket_no, status, current_node}。"""
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
            nodes=[{"node_order": 1, "role_id": seed["roles"]["approver"]}],
        )

        form = (await client.get(f"/api/v1/tickets/templates/{tpl_id}/form",
                                 headers=env["ops_h"])).json()["data"]
        # svc 合并为一条且必填（首现定义为准）；fixed 的 port 不外显
        assert [p["name"] for p in form["params"]] == ["svc"]
        assert form["params"][0]["required"] is True
        assert form["template"]["name"] == "重启 Nginx"
        assert [h["ip"] for h in form["hosts"]] == ["10.9.0.1"]
        assert [s["step_order"] for s in form["steps"]] == [1, 2]
        assert form["flow"] == [{"node": 1, "role_id": seed["roles"]["approver"],
                                 "role_name": "审批人", "approve_mode": "any"}]
        assert form["exec_strategy"]["concurrency"] == 5

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
        """单节点全链路：提交固化快照 → 待办可见 → 通过 → queued + 入队 + 通知落库。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(
            client, env, nodes=[{"node_order": 1, "role_id": seed["roles"]["approver"]}],
            notify_rules=[{"event": "ticket.approved", "receivers": ["creator"], "channels": []}],
        )

        data = await _submit(client, env["ops_h"], tpl_id)
        assert data["status"] == "approving" and data["current_node"] == 1

        # 提交后改模板步骤内容（升版）+ 清空应用主机 → 工单快照不受影响（五重快照固化）
        resp = await client.put(f"/api/v1/templates/{tpl_id}", headers=env["ops_h"], json={
            "name": "重启 Nginx", "type": "ops", "description": "滚动重启",
            "app_id": env["app_id"], "steps": [_step(env, content="echo changed")],
            "exec_strategy": {"concurrency": 5, "batch_size": 0, "timeout": 600},
            "approval_enabled": True,
            "approval_nodes": [{"node_order": 1, "role_id": seed["roles"]["approver"]}],
        })
        assert resp.json()["code"] == 0
        resp = await client.put(f"/api/v1/cmdb/apps/{env['app_id']}", headers=env["ops_h"],
                                json={"name": "订单服务", "deploy_type": "docker", "host_ids": []})
        assert resp.json()["code"] == 0

        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert detail["title"] == "重启 Nginx"  # 标题=模板名快照
        assert detail["template_version"] == 1  # 升版前的版本号
        assert detail["params"] == {"svc": "nginx"}  # 缺省补默认值
        assert detail["steps"][0]["content_snap"].startswith("#!/bin/bash")
        assert [h["ip"] for h in detail["hosts"]] == ["10.9.0.1"]
        assert detail["flow_snap"] == [{"node": 1, "role_id": seed["roles"]["approver"],
                                        "role_name": "审批人", "approve_mode": "any"}]

        # 待办：approver 可见；admin 无 approver 角色不可见；ops 无 ticket:approve 40301
        assert (await client.get("/api/v1/tickets/todo", headers=appr_h)).json()["data"]["total"] == 1
        assert (await client.get("/api/v1/tickets/todo", headers=env["admin_h"])).json()["data"]["total"] == 0
        assert (await client.get("/api/v1/tickets/todo", headers=env["ops_h"])).json()["code"] == 40301

        # 末节点通过 → queued + Execution(queued, auto_approve) + XADD 队列（M5：worker 认领后才置 running）
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve", "comment": "同意"}, headers=appr_h)
        assert resp.json()["data"] == {"status": "queued", "current_node": 0}
        async with db_factory() as session:
            execution = (await session.execute(select(Execution))).scalar_one()
            assert (execution.status, execution.triggered_by) == ("queued", "auto_approve")
            assert (execution.total_steps, execution.total_hosts) == (1, 1)
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
        assert detail["steps"][0]["params"] == {"svc": "mysql"}
        assert detail["steps"][1]["params"] == {"port": "8080"}

    async def test_no_approval_direct_run(self, client, db_factory, fake_redis):
        """免审模板：提交直接 queued 入队，flow_snap=[]，无通知事件。"""
        env = await _base_env(client)
        tpl_id = await _create_template(client, env)  # nodes=None → 免审

        data = await _submit(client, env["ops_h"], tpl_id)
        assert data["status"] == "queued" and data["current_node"] == 0
        detail = (await client.get(f"/api/v1/tickets/{data['id']}",
                                   headers=env["ops_h"])).json()["data"]
        assert detail["flow_snap"] == [] and detail["total_nodes"] == 0
        assert detail["execution"]["triggered_by"] == "no_approval"
        assert await fake_redis.xlen(EXEC_QUEUE) == 1
        assert await _notify_rows(db_factory) == []

    async def test_submit_guard(self, client, seed):
        """禁用模板 40901；可见范围外 40302；应用无主机 40001。"""
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

        # 应用无关联主机 → 40001
        resp = await client.post("/api/v1/cmdb/apps", headers=env["ops_h"],
                                 json={"name": "空应用", "deploy_type": "shell", "host_ids": []})
        empty_app = resp.json()["data"]["id"]
        empty_tpl = await _create_template(client, env, name="空应用模板", app_id=empty_app)
        resp = await client.post("/api/v1/tickets", headers=env["ops_h"],
                                 json={"template_id": empty_tpl, "params": {}})
        body = resp.json()
        assert body["code"] == 40001 and "主机" in body["message"]


class TestApproveAndCancel:
    """节点审批推进 / 驳回 / 撤回。"""

    async def test_multi_node_flow(self, client, db_factory, seed):
        """两节点：逐节点推进；非当前节点角色审批 40302；时间线完整。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(client, env, nodes=[
            {"node_order": 1, "role_id": seed["roles"]["approver"]},
            {"node_order": 2, "role_id": seed["roles"]["admin"]},
        ])
        data = await _submit(client, env["ops_h"], tpl_id)

        # 第 1 节点需 approver：admin 越级审批 -> 40302
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve"}, headers=env["admin_h"])
        assert resp.json()["code"] == 40302

        # 第 1 节点通过 → approving / current_node=2
        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "approve"}, headers=appr_h)
        assert resp.json()["data"] == {"status": "approving", "current_node": 2}

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
        assert [(a["node_order"], a["action"], a["approver_name"]) for a in detail["approvals"]] == [
            (1, "approve", "appr1"), (2, "approve", "admin"),
        ]

    async def test_reject_requires_comment_and_closes(self, client, db_factory, seed):
        """驳回意见必填 40001；驳回后 rejected 终态 + 通知创建人。"""
        env = await _base_env(client)
        appr_h = await _approver_headers(client, db_factory, seed)
        tpl_id = await _create_template(
            client, env, nodes=[{"node_order": 1, "role_id": seed["roles"]["approver"]}])
        data = await _submit(client, env["ops_h"], tpl_id)

        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "reject"}, headers=appr_h)
        assert resp.json()["code"] == 40001

        resp = await client.post(f"/api/v1/tickets/{data['id']}/approve",
                                 json={"action": "reject", "comment": "窗口期不合适"}, headers=appr_h)
        assert resp.json()["data"] == {"status": "rejected", "current_node": 0}
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
            client, env, nodes=[{"node_order": 1, "role_id": seed["roles"]["approver"]}])
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
            nodes=[{"node_order": 1, "role_id": seed["roles"]["approver"]}])
        data = await _submit(client, env["ops_h"], locked)
        resp = await client.post(f"/api/v1/tickets/{data['id']}/cancel", headers=env["ops_h"])
        assert resp.json()["code"] == 42201

    async def test_list_filters(self, client, seed):
        """列表筛选：status/keyword 命中；标题=模板名。"""
        env = await _base_env(client)
        tpl_a = await _create_template(
            client, env, nodes=[{"node_order": 1, "role_id": seed["roles"]["approver"]}])
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
