"""跨模块只修改关联关系；拒绝越权、无效目标并保留部署主机关联。"""
import pytest
from sqlalchemy import select

from app.core.response import BizError
from app.core.security import encrypt_text
from app.models.cmdb import AppHost, Application, Host
from app.models.database_management import DatabaseInstance, DatabaseInstanceApplication, DatabaseInstanceHost, DatabaseRoleInstance
from app.models.job import Credential
from app.models.auth import Role
from app.schemas.cmdb import AppUpdateRequest
from app.services import cmdb_service, credential_service, rbac_service
from tests.conftest import auth_header, login_for_tokens


@pytest.fixture
async def associated(db_factory, seed):
    async with db_factory() as session:
        credential = Credential(name="database fixture", auth_type="username_password", login_user="readonly", secret_enc=encrypt_text("synthetic-only"))
        host = Host(hostname="db-host", ip="192.0.2.20", environment="prod")
        app = Application(name="order-service", deploy_type="shell")
        role = Role(code="database-fixture", name="Database fixture", is_builtin=False)
        session.add_all([credential, host, app, role])
        await session.flush()
        instance = DatabaseInstance(name="shared-db", engine="mysql", environment="prod", host="hidden-target", port=3306, credential_id=credential.id)
        session.add(instance)
        await session.flush()
        session.add_all([AppHost(app_id=app.id, host_id=host.id), DatabaseInstanceApplication(instance_id=instance.id, app_id=app.id), DatabaseInstanceHost(instance_id=instance.id, host_id=host.id), DatabaseRoleInstance(instance_id=instance.id, role_id=role.id)])
        await session.commit()
        return {"app": app.id, "host": host.id, "instance": instance.id, "credential": credential.id, "role": role.id}


@pytest.mark.parametrize("ids", [None, [1,1], [True], [0], [-1]])
def test_database_association_ids_are_strict(ids):
    with pytest.raises(ValueError):
        AppUpdateRequest(name="app", deploy_type="shell", database_instance_ids=ids)


async def test_cmdb_association_requires_database_write_and_preserves_hosts(client, associated):
    ops = auth_header(await login_for_tokens(client, "ops1"))
    admin = auth_header(await login_for_tokens(client, "admin"))
    url = f"/api/v1/cmdb/apps/{associated['app']}"
    payload = {"name":"order-service", "deploy_type":"shell", "host_ids":[associated["host"]]}
    response = await client.put(url, headers=ops, json={**payload, "database_instance_ids":[]})
    assert response.status_code == 403
    detail = (await client.get(url, headers=ops)).json()["data"]
    assert detail["database_instances"] == [{"id": associated["instance"], "name":"shared-db", "engine":"mysql", "enabled":True}]
    response = await client.put(url, headers=admin, json={**payload,"database_instance_ids":[99999]})
    assert response.status_code == 400
    assert (await client.put(url, headers=ops, json=payload)).json()["code"] == 0
    assert len((await client.get(url, headers=ops)).json()["data"]["database_instances"]) == 1
    assert (await client.put(url, headers=admin, json={**payload,"database_instance_ids":[]})).json()["code"] == 0
    detail = (await client.get(url, headers=ops)).json()["data"]
    assert detail["database_instances"] == []
    assert [h["id"] for h in detail["hosts"]] == [associated["host"]]


async def test_database_credential_reference_protected(db_factory, associated):
    async with db_factory() as session:
        with pytest.raises(BizError):
            await credential_service.delete_credential(session, associated["credential"])


async def test_cmdb_role_deletion_only_cleans_links(db_factory, associated):
    async with db_factory() as session:
        await rbac_service.delete_role(session, associated["role"])
        await cmdb_service.delete_app(session, associated["app"])
        await cmdb_service.delete_host(session, associated["host"])
        await session.commit()
        assert await session.get(DatabaseInstance, associated["instance"]) is not None
        for model in (DatabaseRoleInstance, DatabaseInstanceApplication, DatabaseInstanceHost):
            assert list((await session.execute(select(model))).scalars()) == []
