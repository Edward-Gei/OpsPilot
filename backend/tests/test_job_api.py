"""M3 凭据与 V2 工单模板接口测试。

覆盖验收点：
- 凭据任何接口不回显明文/密文；secret 传空不变更；
- 模板全量配置 CRUD：步骤/审批节点/通知规则/可见范围一体提交；
- 规则任一变更自动升版且旧版本快照可查；仅改名/说明不升版；
- 启用/禁用：禁用后列表可筛选，删除保护 42201；
- 引用校验：应用/凭据/角色不存在拒绝；
- RBAC：ops 无 credential:write（CRED-02 仅管理员可管理凭据）。
"""
from app.core.security import decrypt_text
from app.models.job import Credential
from app.models.ticket import Ticket, TicketStep
from tests.conftest import auth_header, login_for_tokens


CRED_PAYLOAD = {
    "name": "prod-root",
    "login_user": "root",
    "auth_type": "password",
    "secret": "S3cret!pass",
    "description": "生产 root 密码",
}

HOST_PAYLOAD = {
    "hostname": "tpl-web-01", "ip": "10.8.0.1", "environment": "prod",
    "status": "online", "ssh_port": 22,
}


async def _create_credential(client, headers, **override) -> int:
    """测试辅助：创建凭据并返回 id。"""
    resp = await client.post("/api/v1/credentials", json={**CRED_PAYLOAD, **override}, headers=headers)
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


async def _base_env(client) -> dict:
    """公共前置：admin/ops 登录 + 1 主机 + 1 应用 + 1 凭据（模板依赖引用）。"""
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
    cred_id = await _create_credential(client, admin_h)
    return {"admin_h": admin_h, "ops_h": ops_h, "host_id": host_id,
            "app_id": app_id, "cred_id": cred_id}


def _tpl_payload(env: dict, **override) -> dict:
    """标准模板全量配置体（1 步骤 + 免审），override 覆盖顶层键。"""
    return {
        "name": "重启 Nginx",
        "type": "ops",
        "description": "滚动重启",
        "app_id": env["app_id"],
        "steps": [{
            "name": "重启服务",
            "script_type": "shell",
            "content": "#!/bin/bash\nsystemctl restart {{ svc }}",
            "params_schema": [
                {"name": "svc", "label": "服务名", "default": "nginx", "required": True}
            ],
            "credential_id": env["cred_id"],
            "timeout": 300,
        }],
        "exec_strategy": {"concurrency": 5, "batch_size": 0, "timeout": 600},
        "approval_enabled": False,
        **override,
    }


async def _create_template(client, headers, env, **override) -> int:
    """测试辅助：创建模板并返回 id。"""
    resp = await client.post("/api/v1/templates", json=_tpl_payload(env, **override), headers=headers)
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


async def _make_active_ticket(db_factory, *, template_id: int, credential_id: int) -> None:
    """测试辅助：手工造一条进行中工单 + 步骤快照，用于引用保护验证。"""
    async with db_factory() as session:
        ticket = Ticket(
            ticket_no="T20260727-0001", template_id=template_id, template_version_snap=1,
            title="重启 Nginx", type="ops", app_id=1, app_name_snap="订单服务",
            status="approving", creator_id=1,
        )
        session.add(ticket)
        await session.flush()
        session.add(TicketStep(
            ticket_id=ticket.id, step_order=1, step_name_snap="重启服务",
            script_type_snap="shell", content_snap="echo x",
            credential_id=credential_id, timeout=300,
        ))
        await session.commit()


class TestCredentialApi:
    """凭据 CRUD / 密文安全 / RBAC。"""

    async def test_crud_and_secret_never_echo(self, client, db_factory):
        """创建→列表：响应无任何密文字段；库内为密文且可逆解回原文。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        cred_id = await _create_credential(client, headers)

        resp = await client.get("/api/v1/credentials", headers=headers)
        body = resp.json()
        assert body["data"]["total"] == 1
        item = body["data"]["items"][0]
        assert item["name"] == "prod-root"
        assert item["auth_type"] == "password"
        assert item["has_passphrase"] is False
        # 密文红线：响应不得出现 secret / secret_enc / passphrase 任何形态
        assert "secret" not in item and "secret_enc" not in item and "passphrase" not in item

        # 库内为 AES-GCM 密文（非明文），且可解密还原
        async with db_factory() as session:
            cred = await session.get(Credential, cred_id)
            assert cred.secret_enc != CRED_PAYLOAD["secret"]
            assert decrypt_text(cred.secret_enc) == CRED_PAYLOAD["secret"]

    async def test_update_empty_secret_keeps_cipher(self, client, db_factory):
        """secret 传空 = 不变更密文；传新值则密文更新。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        cred_id = await _create_credential(client, headers)
        async with db_factory() as session:
            old_enc = (await session.get(Credential, cred_id)).secret_enc

        # 传空 secret：改说明不改密文
        resp = await client.put(f"/api/v1/credentials/{cred_id}", headers=headers,
                                json={**CRED_PAYLOAD, "secret": "", "description": "改说明"})
        assert resp.json()["code"] == 0
        async with db_factory() as session:
            cred = await session.get(Credential, cred_id)
            assert cred.secret_enc == old_enc
            assert cred.description == "改说明"

        # 传新 secret：密文更新且可解出新明文
        resp = await client.put(f"/api/v1/credentials/{cred_id}", headers=headers,
                                json={**CRED_PAYLOAD, "secret": "NewPass#2026"})
        assert resp.json()["code"] == 0
        async with db_factory() as session:
            cred = await session.get(Credential, cred_id)
            assert cred.secret_enc != old_enc
            assert decrypt_text(cred.secret_enc) == "NewPass#2026"

    async def test_change_auth_type_requires_secret(self, client):
        """认证方式变更但未提供新密文 → 40001。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        cred_id = await _create_credential(client, headers)
        resp = await client.put(f"/api/v1/credentials/{cred_id}", headers=headers,
                                json={**CRED_PAYLOAD, "auth_type": "private_key", "secret": ""})
        assert resp.json()["code"] == 40001

    async def test_name_conflict(self, client):
        """凭据名重复 → 40901。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        await _create_credential(client, headers)
        resp = await client.post("/api/v1/credentials", json=CRED_PAYLOAD, headers=headers)
        assert resp.json()["code"] == 40901

    async def test_ops_cannot_write_but_can_read(self, client):
        """CRED-02：ops 仅可读（模板配置选择用），写操作 40301。"""
        admin_headers = auth_header(await login_for_tokens(client, "admin"))
        await _create_credential(client, admin_headers)

        ops_headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/credentials", headers=ops_headers)
        assert resp.json()["code"] == 0
        resp = await client.post("/api/v1/credentials", json={**CRED_PAYLOAD, "name": "x"},
                                 headers=ops_headers)
        assert resp.json()["code"] == 40301

    async def test_delete_protected_by_template_and_ticket(self, client, db_factory):
        """被模板步骤/进行中工单步骤引用 → 42201；未引用可删（04-API §5）。"""
        env = await _base_env(client)
        headers = env["admin_h"]
        busy_by_ticket = await _create_credential(client, headers, name="busy-ticket")
        free_id = await _create_credential(client, headers, name="idle-cred")
        await _make_active_ticket(db_factory, template_id=999, credential_id=busy_by_ticket)

        # env["cred_id"] 被模板步骤引用 → 42201
        await _create_template(client, env["ops_h"], env)
        resp = await client.delete(f"/api/v1/credentials/{env['cred_id']}", headers=headers)
        assert resp.json()["code"] == 42201
        # 被进行中工单步骤引用 → 42201
        resp = await client.delete(f"/api/v1/credentials/{busy_by_ticket}", headers=headers)
        assert resp.json()["code"] == 42201
        # 无引用可删
        resp = await client.delete(f"/api/v1/credentials/{free_id}", headers=headers)
        assert resp.json()["code"] == 0


class TestTemplateApi:
    """V2 模板：全量配置 CRUD / 版本化 / 启停 / 引用校验 / 删除保护。"""

    async def test_create_v1_and_detail(self, client, seed):
        """新建生成 v1；详情返回全部规则 + 步骤 + 审批节点。"""
        env = await _base_env(client)
        tpl_id = await _create_template(client, env["ops_h"], env, **{
            "approval_enabled": True,
            "approval_nodes": [{"node_order": 1, "role_id": seed["roles"]["approver"],
                                "approve_mode": "any"}],
            "notify_rules": [{"event": "ticket.pending_approval",
                              "receivers": ["approver_role"], "channels": ["email"]}],
            "visible_role_ids": [seed["roles"]["ops"]],
        })

        resp = await client.get(f"/api/v1/templates/{tpl_id}", headers=env["ops_h"])
        data = resp.json()["data"]
        assert (data["current_version"], data["status"]) == (1, "enabled")
        assert data["type"] == "ops" and data["app_id"] == env["app_id"]
        step = data["steps"][0]
        assert step["script_type"] == "shell"
        assert step["content"].startswith("#!/bin/bash")
        assert step["params_schema"][0]["name"] == "svc"
        assert data["approval_nodes"] == [
            {"node_order": 1, "role_id": seed["roles"]["approver"], "approve_mode": "any"}]
        assert data["notify_rules"][0]["event"] == "ticket.pending_approval"
        assert data["visible_role_ids"] == [seed["roles"]["ops"]]

    async def test_rule_change_bumps_version(self, client, seed):
        """改步骤内容 → 升 v2；旧版本快照可回看（TPL-06 验收点）。"""
        env = await _base_env(client)
        headers = env["ops_h"]
        tpl_id = await _create_template(client, headers, env)

        payload = _tpl_payload(env, changelog="改为 v2")
        payload["steps"][0]["content"] = "echo v2"
        resp = await client.put(f"/api/v1/templates/{tpl_id}", json=payload, headers=headers)
        body = resp.json()
        assert body["data"]["version_bumped"] is True
        assert body["data"]["current_version"] == 2

        # 版本历史：v2 在前，changelog 落到新版本行
        resp = await client.get(f"/api/v1/templates/{tpl_id}/versions", headers=headers)
        items = resp.json()["data"]["items"]
        assert [v["version"] for v in items] == [2, 1]
        assert items[0]["changelog"] == "改为 v2"

        # 旧版本快照内容可回看；当前详情为新内容
        resp = await client.get(f"/api/v1/templates/{tpl_id}/versions/1", headers=headers)
        snap = resp.json()["data"]["snapshot"]
        assert snap["steps"][0]["content"].startswith("#!/bin/bash")
        resp = await client.get(f"/api/v1/templates/{tpl_id}", headers=headers)
        assert resp.json()["data"]["steps"][0]["content"] == "echo v2"

        # 审批规则变更同样升版（审批规则属于模板内部规则）
        resp = await client.put(f"/api/v1/templates/{tpl_id}", headers=headers,
                                json=_tpl_payload(env, **{
                                    "steps": payload["steps"],
                                    "approval_enabled": True,
                                    "approval_nodes": [{"node_order": 1,
                                                        "role_id": seed["roles"]["approver"],
                                                        "approve_mode": "any"}],
                                }))
        assert resp.json()["data"]["current_version"] == 3

    async def test_rename_only_no_bump(self, client):
        """仅改名/说明 → 不升版（TPL-06）。"""
        env = await _base_env(client)
        headers = env["ops_h"]
        tpl_id = await _create_template(client, headers, env)
        resp = await client.put(f"/api/v1/templates/{tpl_id}", headers=headers,
                                json=_tpl_payload(env, name="重启 Nginx v2", description="改说明"))
        body = resp.json()
        assert body["data"]["version_bumped"] is False
        assert body["data"]["current_version"] == 1
        resp = await client.get(f"/api/v1/templates/{tpl_id}/versions", headers=headers)
        assert len(resp.json()["data"]["items"]) == 1

    async def test_validation_errors(self, client, seed):
        """名称冲突 40901；非法参数名/审批开启无节点/固定值无默认/引用不存在 → 拒绝。"""
        env = await _base_env(client)
        headers = env["ops_h"]
        await _create_template(client, headers, env)
        resp = await client.post("/api/v1/templates", json=_tpl_payload(env), headers=headers)
        assert resp.json()["code"] == 40901

        # 非法参数名
        bad = _tpl_payload(env, name="参数名非法")
        bad["steps"][0]["params_schema"] = [{"name": "1bad-name", "required": False}]
        resp = await client.post("/api/v1/templates", json=bad, headers=headers)
        assert resp.json()["code"] == 40001

        # 审批开启但无节点
        resp = await client.post("/api/v1/templates", headers=headers,
                                 json=_tpl_payload(env, name="无节点", approval_enabled=True,
                                                   approval_nodes=[]))
        assert resp.json()["code"] == 40001

        # 固定值参数未设默认值
        bad = _tpl_payload(env, name="固定值无默认")
        bad["steps"][0]["params_schema"] = [{"name": "env_name", "fixed": True}]
        resp = await client.post("/api/v1/templates", json=bad, headers=headers)
        assert resp.json()["code"] == 40001

        # 引用不存在：应用 40401 / 凭据 40001 / 审批角色 40001
        resp = await client.post("/api/v1/templates", headers=headers,
                                 json=_tpl_payload(env, name="坏应用", app_id=99999))
        assert resp.json()["code"] == 40401
        bad = _tpl_payload(env, name="坏凭据")
        bad["steps"][0]["credential_id"] = 99999
        resp = await client.post("/api/v1/templates", json=bad, headers=headers)
        assert resp.json()["code"] == 40001
        resp = await client.post("/api/v1/templates", headers=headers,
                                 json=_tpl_payload(env, name="坏角色", approval_enabled=True,
                                                   approval_nodes=[{"node_order": 1,
                                                                    "role_id": 99999}]))
        assert resp.json()["code"] == 40001

    async def test_status_toggle_and_filter(self, client):
        """启用/禁用切换；列表 status 筛选；启停不升版。"""
        env = await _base_env(client)
        headers = env["ops_h"]
        tpl_id = await _create_template(client, headers, env)

        resp = await client.put(f"/api/v1/templates/{tpl_id}/status",
                                json={"status": "disabled"}, headers=headers)
        assert resp.json()["data"]["status"] == "disabled"

        resp = await client.get("/api/v1/templates", params={"status": "disabled"}, headers=headers)
        assert resp.json()["data"]["total"] == 1
        resp = await client.get("/api/v1/templates", params={"status": "enabled"}, headers=headers)
        assert resp.json()["data"]["total"] == 0

        detail = (await client.get(f"/api/v1/templates/{tpl_id}", headers=headers)).json()["data"]
        assert detail["current_version"] == 1  # 启停不升版

    async def test_delete_protected_by_active_ticket(self, client, db_factory):
        """被进行中工单引用 → 42201；未引用删除后版本行一并清理。"""
        env = await _base_env(client)
        headers = env["ops_h"]
        busy_id = await _create_template(client, headers, env)
        free_id = await _create_template(client, headers, env, name="空闲模板")
        await _make_active_ticket(db_factory, template_id=busy_id, credential_id=999)

        resp = await client.delete(f"/api/v1/templates/{busy_id}", headers=headers)
        assert resp.json()["code"] == 42201
        resp = await client.delete(f"/api/v1/templates/{free_id}", headers=headers)
        assert resp.json()["code"] == 0
        # 删除后版本历史随主表消失（404）
        resp = await client.get(f"/api/v1/templates/{free_id}/versions", headers=headers)
        assert resp.json()["code"] == 40401

    async def test_unauthenticated_rejected(self, client):
        """未认证访问凭据/模板接口 → 401。"""
        resp = await client.get("/api/v1/credentials")
        assert resp.status_code == 401
        resp = await client.get("/api/v1/templates")
        assert resp.status_code == 401
