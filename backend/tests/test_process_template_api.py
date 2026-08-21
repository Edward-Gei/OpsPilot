"""流程模板重构专项测试：只覆盖新契约，不依赖旧版本字段。"""

from tests.conftest import auth_header, login_for_tokens
from app.services.parameter_prepare_service import _parse_output


async def _env(client):
    admin = auth_header(await login_for_tokens(client, "admin"))
    ops = auth_header(await login_for_tokens(client, "ops1"))
    cred = await client.post("/api/v1/credentials", headers=admin, json={
        "name": "process-test", "login_user": "root", "auth_type": "password", "secret": "x",
    })
    host = await client.post("/api/v1/job-hosts", headers=admin, json={
        "name": "process-host", "ip": "10.0.0.10", "credential_id": cred.json()["data"]["id"],
    })
    return admin, ops, host.json()["data"]["id"]


def _process_payload(role_id: int | None = None):
    return {
        "name": "可复用发布流程", "description": "测试流程",
        "exec_strategy": {"timeout": 600, "fail_fast": True, "kill_on_stop": False},
        "steps": [{"name": "发布", "script_type": "shell", "content": "echo {{ env }}",
                   "timeout": 60, "approval_role_id": role_id}],
    }


async def test_process_template_lifecycle_and_reference_guard(client, seed):
    admin, ops, host_id = await _env(client)
    created = await client.post("/api/v1/process-templates", headers=admin,
                                json=_process_payload(seed["roles"]["approver"]))
    assert created.json()["code"] == 0, created.json()
    process_id = created.json()["data"]["id"]
    detail = await client.get(f"/api/v1/process-templates/{process_id}", headers=ops)
    assert detail.json()["data"]["steps"][0]["approval_role_id"] == seed["roles"]["approver"]

    tpl = await client.post("/api/v1/templates", headers=admin, json={
        "name": "发布入口", "type": "release", "job_host_id": host_id,
        "process_template_id": process_id, "allow_withdraw": True,
        "params_schema": [{"name": "env", "label": "环境", "source": "user", "input_type": "enum",
                            "options": ["test", "prod"], "default": "test", "required": True}],
        "notify_rules": [], "visible_role_ids": [],
    })
    assert tpl.json()["code"] == 0, tpl.json()
    blocked = await client.delete(f"/api/v1/process-templates/{process_id}", headers=admin)
    assert blocked.json()["code"] == 42201

    assert (await client.put(f"/api/v1/process-templates/{process_id}/status",
                             headers=admin, json={"status": "disabled"})).json()["code"] == 0
    updated = _process_payload(seed["roles"]["approver"])
    updated["name"] = "可复用发布流程（停用可编辑）"
    assert (await client.put(f"/api/v1/process-templates/{process_id}", headers=admin,
                             json=updated)).json()["code"] == 0


async def test_ticket_template_keeps_script_secret_refs_out_of_process_definition(client, seed):
    admin, _, host_id = await _env(client)
    process = await client.post("/api/v1/process-templates", headers=admin,
                                json=_process_payload())
    process_id = process.json()["data"]["id"]
    tpl = await client.post("/api/v1/templates", headers=admin, json={
        "name": "无凭据引用入口", "type": "daily_ops", "job_host_id": host_id,
        "process_template_id": process_id, "params_schema": [], "notify_rules": [], "visible_role_ids": [],
    })
    assert tpl.json()["code"] == 0
    detail = (await client.get(f"/api/v1/templates/{tpl.json()['data']['id']}", headers=admin)).json()["data"]
    assert detail["process_template_id"] == process_id
    assert detail["credential_refs"] == [] and "steps" not in detail and "approval_nodes" not in detail


async def test_ticket_template_binds_script_secrets_with_unique_aliases(client, seed):
    """脚本密钥归属工单模板，拒绝 SSH 凭据和非法/重复别名。"""
    admin, ops, host_id = await _env(client)
    script_cred = await client.post("/api/v1/credentials", headers=admin, json={
        "name": "发布令牌", "auth_type": "api_token", "secret": "deploy-token",
    })
    process = await client.post("/api/v1/process-templates", headers=admin, json=_process_payload())
    process_id = process.json()["data"]["id"]
    payload = {
        "name": "带密钥发布入口", "type": "release", "job_host_id": host_id,
        "process_template_id": process_id, "params_schema": [], "notify_rules": [],
        "visible_role_ids": [],
        "credential_refs": [{"alias": "DEPLOY_TOKEN", "credential_id": script_cred.json()["data"]["id"]}],
    }
    created = await client.post("/api/v1/templates", headers=ops, json=payload)
    assert created.json()["code"] == 0, created.json()
    detail = (await client.get(f"/api/v1/templates/{created.json()['data']['id']}", headers=ops)).json()["data"]
    assert detail["credential_refs"] == [{"alias": "DEPLOY_TOKEN", "credential_id": script_cred.json()["data"]["id"], "credential_name": "发布令牌"}]

    ssh_cred = await client.post("/api/v1/credentials", headers=admin, json={
        "name": "错误 SSH 引用", "login_user": "root", "auth_type": "password", "secret": "x",
    })
    payload["name"] = "SSH 引用入口"
    payload["credential_refs"] = [{"alias": "SSH_LOGIN", "credential_id": ssh_cred.json()["data"]["id"]}]
    assert (await client.post("/api/v1/templates", headers=ops, json=payload)).json()["code"] == 40001

    payload["name"] = "重复别名入口"
    payload["credential_refs"] = [
        {"alias": "DEPLOY_TOKEN", "credential_id": script_cred.json()["data"]["id"]},
        {"alias": "DEPLOY_TOKEN", "credential_id": script_cred.json()["data"]["id"]},
    ]
    assert (await client.post("/api/v1/templates", headers=ops, json=payload)).json()["code"] == 40001

    payload["name"] = "重复凭据入口"
    payload["credential_refs"] = [
        {"alias": "DEPLOY_TOKEN", "credential_id": script_cred.json()["data"]["id"]},
        {"alias": "BACKUP_TOKEN", "credential_id": script_cred.json()["data"]["id"]},
    ]
    assert (await client.post("/api/v1/templates", headers=ops, json=payload)).json()["code"] == 40001

    payload["name"] = "非法别名入口"
    payload["credential_refs"] = [{"alias": "deploy-token", "credential_id": script_cred.json()["data"]["id"]}]
    assert (await client.post("/api/v1/templates", headers=ops, json=payload)).json()["code"] == 40001


async def test_ops_can_load_template_reference_options(client):
    """模板编辑器依赖的作业主机和角色选项对运维角色可读。"""
    _, ops, _ = await _env(client)
    hosts = await client.get("/api/v1/job-hosts", headers=ops)
    roles = await client.get("/api/v1/roles/options", headers=ops)
    assert hosts.json()["code"] == 0
    assert roles.json()["code"] == 0


def test_generated_parameter_output_supports_value_and_options():
    values, options = _parse_output('{"release": ["blue", "green"], "version": "2026.08"}', ["release", "version"])
    assert values == {"version": "2026.08"}
    assert options == {"release": ["blue", "green"]}


async def test_process_template_is_parameter_agnostic_and_ticket_templates_are_independent(client):
    admin, _, host_id = await _env(client)
    process = await client.post("/api/v1/process-templates", headers=admin, json={
        "name": "共享参数无关流程", "description": "测试流程", "exec_strategy": {},
        "steps": [{"name": "执行", "script_type": "shell", "content": "echo {{ value }}", "timeout": 60}],
    })
    assert process.json()["code"] == 0
    process_id = process.json()["data"]["id"]
    first = await client.post("/api/v1/templates", headers=admin, json={
        "name": "入口一", "type": "daily_ops", "job_host_id": host_id, "process_template_id": process_id,
        "params_schema": [{"name": "value", "source": "fixed", "default": "one"}],
    })
    second = await client.post("/api/v1/templates", headers=admin, json={
        "name": "入口二", "type": "daily_ops", "job_host_id": host_id, "process_template_id": process_id,
        "params_schema": [{"name": "value", "source": "fixed", "default": "two"}],
    })
    assert first.json()["code"] == second.json()["code"] == 0
    first_detail = (await client.get(f"/api/v1/templates/{first.json()['data']['id']}", headers=admin)).json()["data"]
    second_detail = (await client.get(f"/api/v1/templates/{second.json()['data']['id']}", headers=admin)).json()["data"]
    assert first_detail["params_schema"][0]["default"] == "one"
    assert second_detail["params_schema"][0]["default"] == "two"
