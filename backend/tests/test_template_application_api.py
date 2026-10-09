"""模板适用应用：多对多持久化、双向详情及关联生命周期。"""
import pytest
from sqlalchemy import delete, select

from app.models.auth import Permission, RolePermission, User, UserRole
from app.services import rbac_service
from tests.conftest import TEST_PASSWORD_HASH, auth_header, login_for_tokens
from tests.test_process_template_api import _env, _process_payload


async def _setup(client):
    admin, ops, host_id = await _env(client)
    process = await client.post("/api/v1/process-templates", headers=admin, json=_process_payload())
    app_ids = []
    for name in ("订单应用", "支付应用"):
        response = await client.post("/api/v1/cmdb/apps", headers=admin, json={
            "name": name, "deploy_type": "docker", "host_ids": [],
        })
        assert response.json()["code"] == 0, response.json()
        app_ids.append(response.json()["data"]["id"])
    payload = {"name": "应用发布入口", "type": "release", "job_host_id": host_id,
               "process_template_id": process.json()["data"]["id"], "params_schema": []}
    return admin, ops, app_ids, payload


async def test_template_applications_many_to_many_and_reverse_detail(client):
    admin, ops, app_ids, payload = await _setup(client)
    ids = []
    for name in ("发布入口", "回滚入口"):
        response = await client.post("/api/v1/templates", headers=ops,
                                     json={**payload, "name": name, "app_ids": app_ids})
        assert response.json()["code"] == 0, response.json()
        ids.append(response.json()["data"]["id"])
    detail = (await client.get(f"/api/v1/templates/{ids[0]}", headers=ops)).json()["data"]
    assert detail["app_ids"] == app_ids
    assert detail["apps"] == [{"id": app_ids[0], "name": "订单应用"},
                              {"id": app_ids[1], "name": "支付应用"}]
    for app_id in app_ids:
        app = (await client.get(f"/api/v1/cmdb/apps/{app_id}", headers=admin)).json()["data"]
        assert app["ticket_templates"] == [{"id": ids[0], "name": "发布入口", "status": "enabled"},
                                            {"id": ids[1], "name": "回滚入口", "status": "enabled"}]


async def test_template_applications_replace_clear_and_legacy_update(client):
    admin, _, app_ids, payload = await _setup(client)
    created = await client.post("/api/v1/templates", headers=admin, json={**payload, "app_ids": app_ids})
    template_id = created.json()["data"]["id"]
    url = f"/api/v1/templates/{template_id}"
    # 旧客户端未发送应用字段时，不能悄悄清空已有绑定。
    assert (await client.put(url, headers=admin, json=payload)).json()["code"] == 0
    assert (await client.get(url, headers=admin)).json()["data"]["app_ids"] == app_ids
    for target in ([app_ids[1]], []):
        assert (await client.put(url, headers=admin, json={**payload, "app_ids": target})).json()["code"] == 0
        assert (await client.get(url, headers=admin)).json()["data"]["app_ids"] == target
        reverse = (await client.get(f"/api/v1/cmdb/apps/{app_ids[0]}", headers=admin)).json()["data"]
        assert reverse["ticket_templates"] == []


@pytest.mark.parametrize("target", [[999999], [1, 1], [0], [-1], ["1"], [True], None])
async def test_template_applications_reject_invalid_ids_without_partial_update(client, target):
    admin, _, app_ids, payload = await _setup(client)
    created = await client.post("/api/v1/templates", headers=admin, json={**payload, "app_ids": app_ids})
    url = f"/api/v1/templates/{created.json()['data']['id']}"
    result = await client.put(url, headers=admin, json={**payload, "name": "不应保存", "app_ids": target})
    assert result.json()["code"] != 0
    detail = (await client.get(url, headers=admin)).json()["data"]
    assert detail["name"] == payload["name"]
    assert detail["app_ids"] == app_ids


async def test_deleting_template_or_app_cleans_only_links(client):
    admin, _, app_ids, payload = await _setup(client)
    created = await client.post("/api/v1/templates", headers=admin, json={**payload, "app_ids": app_ids})
    template_id = created.json()["data"]["id"]
    assert (await client.delete(f"/api/v1/cmdb/apps/{app_ids[0]}", headers=admin)).json()["code"] == 0
    detail = (await client.get(f"/api/v1/templates/{template_id}", headers=admin)).json()["data"]
    assert detail["app_ids"] == [app_ids[1]]
    assert (await client.delete(f"/api/v1/templates/{template_id}", headers=admin)).json()["code"] == 0
    app = (await client.get(f"/api/v1/cmdb/apps/{app_ids[1]}", headers=admin)).json()["data"]
    assert app["ticket_templates"] == []


async def test_application_binding_permissions_and_safe_reverse_metadata(client, seed, db_factory):
    admin, ops, app_ids, payload = await _setup(client)
    created = await client.post("/api/v1/templates", headers=admin, json={**payload, "app_ids": app_ids})
    url = f"/api/v1/templates/{created.json()['data']['id']}"
    async with db_factory() as session:
        permission_id = (await session.execute(
            select(Permission.id).where(Permission.code == "cmdb:read")
        )).scalar_one()
        await session.execute(delete(RolePermission).where(
            RolePermission.role_id == seed["roles"]["ops"], RolePermission.permission_id == permission_id,
        ))
        await session.commit()
    await rbac_service.invalidate_user_perms(seed["users"]["ops1"])
    assert (await client.put(url, headers=ops, json={**payload, "app_ids": []})).status_code == 403
    assert (await client.post("/api/v1/templates", headers=ops,
                              json={**payload, "name": "越权绑定", "app_ids": app_ids})).status_code == 403
    assert (await client.put(url, headers=ops, json=payload)).json()["code"] == 0
    assert (await client.get(url, headers=ops)).json()["data"]["app_ids"] == app_ids
    async with db_factory() as session:
        reader = User(username="app_reader", display_name="应用只读", password_hash=TEST_PASSWORD_HASH,
                      must_change_password=False, status="active")
        session.add(reader)
        await session.flush()
        session.add(UserRole(user_id=reader.id, role_id=seed["roles"]["auditor"]))
        await session.commit()
    reader_headers = auth_header(await login_for_tokens(client, "app_reader"))
    app = (await client.get(f"/api/v1/cmdb/apps/{app_ids[0]}", headers=reader_headers)).json()["data"]
    assert set(app["ticket_templates"][0]) == {"id", "name", "status"}
    assert (await client.get(url, headers=reader_headers)).status_code == 403
