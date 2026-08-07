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
        "name": "可复用发布流程", "description": "测试流程", "params_schema": [
            {"name": "env", "label": "环境", "source": "user", "input_type": "enum",
             "options": ["test", "prod"], "default": "test", "required": True},
        ], "exec_strategy": {"timeout": 600, "fail_fast": True, "kill_on_stop": False},
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


async def test_ticket_template_has_no_credential_or_embedded_rules(client, seed):
    admin, _, host_id = await _env(client)
    process = await client.post("/api/v1/process-templates", headers=admin,
                                json=_process_payload())
    process_id = process.json()["data"]["id"]
    tpl = await client.post("/api/v1/templates", headers=admin, json={
        "name": "无凭据引用入口", "type": "daily_ops", "job_host_id": host_id,
        "process_template_id": process_id, "notify_rules": [], "visible_role_ids": [],
    })
    assert tpl.json()["code"] == 0
    detail = (await client.get(f"/api/v1/templates/{tpl.json()['data']['id']}", headers=admin)).json()["data"]
    assert detail["process_template_id"] == process_id
    assert "credential_refs" not in detail and "steps" not in detail and "approval_nodes" not in detail


def test_generated_parameter_output_supports_value_and_options():
    values, options = _parse_output('{"release": ["blue", "green"], "version": "2026.08"}', ["release", "version"])
    assert values == {"version": "2026.08"}
    assert options == {"release": ["blue", "green"]}
