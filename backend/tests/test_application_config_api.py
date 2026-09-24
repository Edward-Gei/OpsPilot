"""应用配置实例与本地配置文件 API 测试。"""
import pytest
from sqlalchemy import delete, select

from app.config_providers.nacos import NacosAdapter
from app.models.application_config import ConfigTask
from app.models.auth import Permission, Role, RolePermission, UserRole

from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


async def _create_api_token_credential(client, headers: dict) -> int:
    response = await client.post("/api/v1/credentials", headers=headers, json={
        "name": "nacos-token",
        "auth_type": "api_token",
        "secret": "test-token",
    })
    assert response.json()["code"] == 0
    return response.json()["data"]["id"]


async def _create_instance(client, headers: dict, credential_id: int) -> int:
    response = await client.post("/api/v1/application-configs/platform-instances", headers=headers, json={
        "name": "nacos-prod",
        "provider": "nacos",
        "base_url": "https://nacos.example",
        "credential_id": credential_id,
    })
    assert response.json()["code"] == 0
    return response.json()["data"]["id"]


async def test_ops_can_create_file_but_cannot_manage_platform_instance(client, seed):
    """ops 默认有配置读写权限，但没有实例管理权限。"""
    admin_headers = auth_header(await login_for_tokens(client, "admin"))
    ops_headers = auth_header(await login_for_tokens(client, "ops1"))
    credential_id = await _create_api_token_credential(client, admin_headers)
    instance_id = await _create_instance(client, admin_headers, credential_id)

    denied = await client.post("/api/v1/application-configs/platform-instances", headers=ops_headers, json={
        "name": "not-allowed", "provider": "nacos", "base_url": "https://other.example",
        "credential_id": credential_id,
    })
    assert denied.json()["code"] == 40301

    response = await client.post("/api/v1/application-configs/files", headers=ops_headers, json={
        "platform_instance_id": instance_id,
        "selection": {
            "name": "payment-prod", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "prod", "group": "PAYMENT", "data_id": "payment.yaml"},
            "initial_content": "enabled: true\n",
        },
    })
    assert response.json()["code"] == 0


async def test_nacos_namespace_options_are_scoped_to_instance_and_config_writer(client, seed, monkeypatch):
    async def list_namespaces(self, connection):
        assert connection.provider.value == "nacos"
        return [{"id": "", "name": "public"}, {"id": "prod-id", "name": "生产"}]

    monkeypatch.setattr(NacosAdapter, "list_namespaces", list_namespaces, raising=False)
    admin_headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, admin_headers)
    instance_id = await _create_instance(client, admin_headers, credential_id)
    ops_headers = auth_header(await login_for_tokens(client, "ops1"))
    result = await client.get(f"/api/v1/application-configs/platform-instances/{instance_id}/namespaces",
                              headers=ops_headers)
    assert result.json()["code"] == 0
    assert result.json()["data"]["items"] == [{"id": "", "name": "public"}, {"id": "prod-id", "name": "生产"}]
    assert "test-token" not in result.text
    no_auth = await client.get(f"/api/v1/application-configs/platform-instances/{instance_id}/namespaces")
    assert no_auth.json()["code"] != 0


async def test_nacos_public_namespace_can_be_saved_as_exact_locator(client, seed):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    instance_id = await _create_instance(client, headers, credential_id)
    response = await client.post("/api/v1/application-configs/files", headers=headers, json={
        "platform_instance_id": instance_id,
        "selection": {
            "name": "public-config", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "", "group": "DEFAULT_GROUP", "data_id": "common.yaml"},
            "initial_content": "enabled: true\n",
        },
    })
    assert response.json()["code"] == 0
    detail = await client.get(f"/api/v1/application-configs/files/{response.json()['data']['id']}", headers=headers)
    assert detail.json()["data"]["locator"] == {
        "namespace": "", "group": "DEFAULT_GROUP", "data_id": "common.yaml",
    }


async def test_apollo_rejects_text_before_creating_unpublishable_file(client, seed):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    instance = await client.post("/api/v1/application-configs/platform-instances", headers=headers, json={
        "name": "apollo-prod", "provider": "apollo", "base_url": "https://apollo.example",
        "credential_id": credential_id,
    })
    assert instance.json()["code"] == 0
    response = await client.post("/api/v1/application-configs/files", headers=headers, json={
        "platform_instance_id": instance.json()["data"]["id"],
        "selection": {
            "name": "unsupported-text", "content_format": "text", "approval_role_id": seed["roles"]["ops"],
            "locator": {"app_id": "payment", "cluster": "default", "namespace": "notes.txt"},
            "initial_content": "some text",
        },
    })
    assert response.json()["code"] != 0
    files = await client.get("/api/v1/application-configs/files", headers=headers)
    assert files.json()["data"]["total"] == 0


async def test_file_task_history_is_readable_without_content(client, seed, monkeypatch):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    instance_id = await _create_instance(client, headers, credential_id)
    created = await client.post("/api/v1/application-configs/files", headers=headers, json={
        "platform_instance_id": instance_id,
        "selection": {
            "name": "history-prod", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "prod", "group": "HISTORY", "data_id": "history.yaml"},
            "initial_content": "password: private-value\n",
        },
    })
    file_id = created.json()["data"]["id"]
    monkeypatch.setattr(ConfigTask.__table__, "implicit_returning", False)
    task = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=headers)
    assert task.json()["code"] == 0
    assert task.json()["data"]["created_at"] is not None
    history = await client.get(f"/api/v1/application-configs/files/{file_id}/tasks", headers=headers)
    assert history.json()["code"] == 0
    assert history.json()["data"]["items"][0]["id"] == task.json()["data"]["id"]
    assert "private-value" not in history.text


async def test_config_reader_can_list_association_options_without_write(client, seed, db_factory):
    async with db_factory() as session:
        role = Role(code="config_reader", name="配置只读", is_builtin=False)
        session.add(role)
        await session.flush()
        permission_id = (await session.execute(select(Permission.id).where(Permission.code == "config:read"))).scalar_one()
        session.add(RolePermission(role_id=role.id, permission_id=permission_id))
        await session.execute(delete(UserRole).where(UserRole.user_id == seed["users"]["ops1"]))
        session.add(UserRole(user_id=seed["users"]["ops1"], role_id=role.id))
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "ops1"))
    response = await client.get("/api/v1/application-configs/cmdb-applications", headers=headers)
    assert response.json()["code"] == 0
    denied = await client.post("/api/v1/application-configs/discover", headers=headers,
                               json={"platform_instance_id": 1, "query": {}})
    assert denied.json()["code"] == 40301
    namespaces = await client.get("/api/v1/application-configs/platform-instances/1/namespaces", headers=headers)
    assert namespaces.json()["code"] == 40301


async def test_file_cannot_be_archived_while_sync_is_queued(client, seed):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    instance_id = await _create_instance(client, headers, credential_id)
    created = await client.post("/api/v1/application-configs/files", headers=headers, json={
        "platform_instance_id": instance_id,
        "selection": {
            "name": "active-task", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "prod", "group": "TASK", "data_id": "task.yaml"},
            "initial_content": "enabled: true\n",
        },
    })
    file_id = created.json()["data"]["id"]
    queued = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=headers)
    assert queued.json()["code"] == 0
    archived = await client.post(f"/api/v1/application-configs/files/{file_id}/archive", headers=headers)
    assert archived.json()["code"] != 0
    detail = await client.get(f"/api/v1/application-configs/files/{file_id}", headers=headers)
    assert detail.json()["data"]["status"] == "active"


async def test_import_task_results_can_be_reopened_after_page_reload(client, seed, monkeypatch):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    instance_id = await _create_instance(client, headers, credential_id)
    monkeypatch.setattr(ConfigTask.__table__, "implicit_returning", False)
    created = await client.post("/api/v1/application-configs/import-tasks", headers=headers, json={
        "platform_instance_id": instance_id,
        "selections": [{
            "name": "batch-item", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "prod", "group": "BATCH", "data_id": "item.yaml"},
        }],
    })
    assert created.json()["code"] == 0
    assert created.json()["data"]["created_at"] is not None
    listed = await client.get("/api/v1/application-configs/import-tasks", headers=headers)
    assert listed.json()["code"] == 0
    assert listed.json()["data"]["items"][0]["id"] == created.json()["data"]["id"]
    assert listed.json()["data"]["items"][0]["items"][0]["name"] == "batch-item"


async def test_platform_url_rejects_embedded_credentials_and_query(client, seed):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    for url in ("https://user:password@nacos.example/nacos", "https://nacos.example/nacos?accessToken=secret"):
        response = await client.post("/api/v1/application-configs/platform-instances", headers=headers, json={
            "name": "unsafe-url", "provider": "nacos", "base_url": url, "credential_id": credential_id,
        })
        assert response.json()["code"] != 0
        assert "password" not in response.text and "secret" not in response.text


async def test_first_binding_locks_instance_and_protects_references(client, seed):
    """首次配置绑定后不可替换平台/地址，凭据和审批角色不能被删除。"""
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_api_token_credential(client, headers)
    instance_id = await _create_instance(client, headers, credential_id)
    role = await client.post("/api/v1/roles", headers=headers, json={
        "code": "config_approver", "name": "配置审批", "permissions": [],
    })
    assert role.json()["code"] == 0
    role_id = role.json()["data"]["id"]
    created = await client.post("/api/v1/application-configs/files", headers=headers, json={
        "platform_instance_id": instance_id,
        "selection": {
            "name": "payment-prod", "content_format": "yaml", "approval_role_id": role_id,
            "locator": {"namespace": "prod", "group": "PAYMENT", "data_id": "payment.yaml"},
            "initial_content": "enabled: true\n",
        },
    })
    assert created.json()["code"] == 0
    file_id = created.json()["data"]["id"]

    locked = await client.put(f"/api/v1/application-configs/platform-instances/{instance_id}", headers=headers, json={
        "provider": "apollo", "base_url": "https://apollo.example",
    })
    assert locked.json()["code"] == 42201
    assert (await client.delete(f"/api/v1/credentials/{credential_id}", headers=headers)).json()["code"] == 42201
    assert (await client.delete(f"/api/v1/roles/{role_id}", headers=headers)).json()["code"] == 42201

    assert (await client.delete(f"/api/v1/application-configs/files/{file_id}", headers=headers)).json()["code"] == 42201
    assert (await client.post(f"/api/v1/application-configs/files/{file_id}/archive", headers=headers)).json()["code"] == 0
    assert (await client.delete(f"/api/v1/application-configs/files/{file_id}", headers=headers)).json()["code"] == 0
