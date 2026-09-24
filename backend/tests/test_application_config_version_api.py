"""草稿、候选版本和当前成员审批测试。"""
import pytest
from sqlalchemy import delete, select

from app.models.application_config import ConfigDraft, ConfigTask, ConfigVersion
from app.models.auth import Role, UserRole
from app.services import rbac_service

from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


async def _created_file(client, seed):
    admin = auth_header(await login_for_tokens(client, "admin"))
    ops = auth_header(await login_for_tokens(client, "ops1"))
    cred = await client.post("/api/v1/credentials", headers=admin, json={
        "name": "config-token", "auth_type": "api_token", "secret": "test-token",
    })
    instance = await client.post("/api/v1/application-configs/platform-instances", headers=admin, json={
        "name": "nacos-prod", "provider": "nacos", "base_url": "https://nacos.example",
        "credential_id": cred.json()["data"]["id"],
    })
    created = await client.post("/api/v1/application-configs/files", headers=ops, json={
        "platform_instance_id": instance.json()["data"]["id"],
        "selection": {
            "name": "payment", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "prod", "group": "PAY", "data_id": "payment.yaml"},
            "initial_content": "db:\n  password: p@ss\n  pool: 10\n",
        },
    })
    assert created.json()["code"] == 0
    return created.json()["data"]["id"], admin, ops


async def test_ops_member_self_approves(client, seed):
    """提交人允许按当前审批角色成员身份自审。"""
    file_id, admin, ops = await _created_file(client, seed)
    draft = await client.get(f"/api/v1/application-configs/files/{file_id}/draft", headers=ops)
    assert draft.json()["code"] == 0
    assert "content" in draft.json()["data"]
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=ops)
    assert candidate.json()["code"] == 0
    version_id = candidate.json()["data"]["id"]
    approved = await client.post(
        f"/api/v1/application-configs/files/{file_id}/candidates/{version_id}/approve", headers=ops,
    )
    assert approved.json()["data"]["status"] == "approved"



async def test_admin_can_directly_approve(client, seed):
    """admin 当前角色成员无需拥有目标文件的专门审批角色。"""
    file_id, admin, ops = await _created_file(client, seed)
    second = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=ops)
    second_id = second.json()["data"]["id"]
    by_admin = await client.post(
        f"/api/v1/application-configs/files/{file_id}/candidates/{second_id}/approve", headers=admin,
    )
    assert by_admin.json()["data"]["status"] == "approved"


async def test_rebinding_approval_role_invalidates_pending_candidate(client, seed):
    file_id, admin, ops = await _created_file(client, seed)
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=ops)
    version_id = candidate.json()["data"]["id"]
    changed = await client.put(f"/api/v1/application-configs/files/{file_id}", headers=admin, json={
        "approval_role_id": seed["roles"]["approver"],
    })
    assert changed.json()["code"] == 0
    versions = await client.get(f"/api/v1/application-configs/files/{file_id}/versions", headers=admin)
    status = next(item["status"] for item in versions.json()["data"]["items"] if item["id"] == version_id)
    assert status == "invalidated"


async def test_reading_draft_does_not_create_one(client, seed, db_factory):
    file_id, _admin, ops = await _created_file(client, seed)
    discarded = await client.delete(f"/api/v1/application-configs/files/{file_id}/draft", headers=ops)
    assert discarded.json()["code"] == 0
    preview = await client.get(f"/api/v1/application-configs/files/{file_id}/draft", headers=ops)
    assert preview.json()["code"] == 0
    assert preview.json()["data"]["id"] is None
    async with db_factory() as session:
        assert (await session.execute(select(ConfigDraft.id).where(ConfigDraft.config_file_id == file_id))).first() is None


async def test_candidate_approval_uses_current_role_membership(client, seed, db_factory):
    file_id, _admin, ops = await _created_file(client, seed)
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=ops)
    version_id = candidate.json()["data"]["id"]
    async with db_factory() as session:
        await session.execute(delete(UserRole).where(
            UserRole.user_id == seed["users"]["ops1"], UserRole.role_id == seed["roles"]["ops"],
        ))
        await session.commit()
    denied = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates/{version_id}/approve", headers=ops)
    assert denied.json()["code"] == 40301
    async with db_factory() as session:
        session.add(UserRole(user_id=seed["users"]["ops1"], role_id=seed["roles"]["ops"]))
        await session.commit()
    approved = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates/{version_id}/approve", headers=ops)
    assert approved.json()["data"]["status"] == "approved"


async def test_config_approval_todo_is_scoped_to_current_members_and_admin(client, seed, db_factory):
    """配置审批不借用工单权限，待办正文仍按 secret:read 脱敏。"""
    file_id, admin, ops = await _created_file(client, seed)
    async with db_factory() as session:
        role = Role(code="config_only_approver", name="配置专属审批", is_builtin=False)
        session.add(role)
        await session.flush()
        role_id = role.id
        await session.commit()
    changed = await client.put(f"/api/v1/application-configs/files/{file_id}", headers=admin,
                               json={"approval_role_id": role_id})
    assert changed.json()["code"] == 0
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=admin)
    version_id = candidate.json()["data"]["id"]

    base = "/api/v1/application-configs/approval-todo"
    invisible = await client.get(base, headers=ops)
    assert invisible.json()["data"]["total"] == 0
    denied = await client.get(f"{base}/{version_id}/content", headers=ops)
    assert denied.status_code == 404

    async with db_factory() as session:
        await session.execute(delete(UserRole).where(UserRole.user_id == seed["users"]["ops1"]))
        session.add(UserRole(user_id=seed["users"]["ops1"], role_id=role_id))
        await session.commit()
    await rbac_service.invalidate_user_perms(seed["users"]["ops1"])
    visible = await client.get(base, headers=ops)
    assert visible.json()["data"]["total"] == 1
    assert visible.json()["data"]["items"][0]["file_id"] == file_id
    summary = await client.get("/api/v1/dashboard/summary", headers=ops)
    assert summary.json()["data"]["todo_total"] == 1
    preview = await client.get(f"{base}/{version_id}/content", headers=ops)
    assert preview.json()["code"] == 0
    assert "p@ss" not in preview.json()["data"]["content"]
    assert "pool" in preview.json()["data"]["content"]
    admin_todo = await client.get(base, headers=admin)
    assert admin_todo.json()["data"]["total"] == 1

    approved = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates/{version_id}/approve", headers=ops)
    assert approved.json()["data"]["status"] == "approved"
    async with db_factory() as session:
        tasks = (await session.execute(select(ConfigTask).where(ConfigTask.config_version_id == version_id))).scalars().all()
        assert len(tasks) == 1
        assert tasks[0].status == "queued"
    assert (await client.get(base, headers=ops)).json()["data"]["total"] == 0
    assert (await client.get("/api/v1/dashboard/summary", headers=ops)).json()["data"]["todo_total"] == 0


async def test_candidate_response_loads_server_timestamp_without_returning(client, seed, monkeypatch):
    """MySQL 不支持 INSERT RETURNING 时提交响应也不得触发隐式异步查询。"""
    file_id, _admin, ops = await _created_file(client, seed)
    monkeypatch.setattr(ConfigVersion.__table__, "implicit_returning", False)
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=ops)
    assert candidate.json()["code"] == 0
    assert candidate.json()["data"]["created_at"] is not None
