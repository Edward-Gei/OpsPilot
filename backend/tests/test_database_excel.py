"""实例 Excel 的安全字段、逐行导入和无凭据草稿回归。"""
from io import BytesIO
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
import pytest
from sqlalchemy import delete, select

from conftest import auth_header, login_for_tokens
from app.core.response import Errors
from app.services import database_excel
from app.models.auth import Permission, RolePermission
from app.models.database_management import (DatabaseAuditEvent, DatabaseInstance, DatabaseInstanceApplication,
    DatabaseInstanceHost, DatabaseRoleInstance, DatabaseTask)
from app.models.cmdb import Application, Host
from app.models.job import Credential
from app.services.database_authorization_service import current_permissions
from app.services.database_quota_service import bucket, claim_next_task
from test_database_core import target

HEADERS = ["实例名称*", "引擎*", "连接地址(host)*", "端口*", "环境*", "描述", "业务线", "集群名称", "集群角色", "版本"]


def workbook(*rows, headers=HEADERS):
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def upload(client, headers, content, *, upsert=False):
    return await client.post(f"/api/v1/database/instances/import?upsert={str(upsert).lower()}",
        headers=headers, files={"file": ("instances.xlsx", content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


async def test_import_permission_added_to_admin_only(db_factory, seed):
    async with db_factory() as session:
        assert "database:import" in await current_permissions(session, seed["users"]["admin"])
        assert "database:import" not in await current_permissions(session, seed["users"]["ops1"])


async def test_template_import_partial_rows_create_disabled_drafts(client, db_factory, seed, monkeypatch):
    headers = auth_header(await login_for_tokens(client, "admin"))
    template = await client.get("/api/v1/database/instances/import-template", headers=headers)
    assert template.status_code == 200
    book = load_workbook(BytesIO(template.content))
    assert [c.value for c in book.active[1]] == HEADERS
    assert book.active.data_validations.count >= 2
    book.close()
    captured = []
    monkeypatch.setattr("app.api.v1.database_management.audit.log", lambda **kw: captured.append(kw))
    response = await upload(client, headers, workbook(
        ["first", "mysql", "db.internal", 3306, "prod", "a", "mitrade", "mysql-a", "primary", "8.0"],
        ["wrong", "mysql", "db.internal", 65536, "prod"],
        ["last", "postgresql", "pg.internal", "5432", "stage"],
        ["first", "mysql", "db.internal", 3306, "demo"],
    ))
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert (result["success_count"], result["created_count"], result["updated_count"]) == (2, 2, 0)
    assert [r["row"] for r in result["failed_rows"]] == [3, 5]
    async with db_factory() as session:
        rows = list((await session.execute(select(DatabaseInstance).order_by(DatabaseInstance.name))).scalars())
        assert [r.name for r in rows] == ["first", "last"]
        assert all(r.credential_id is None and not r.enabled and r.last_probe_status == "untested" for r in rows)
        assert all(r.connection_options == {} and r.concurrency_limit is None for r in rows)
        for model in (DatabaseInstanceApplication, DatabaseInstanceHost, DatabaseRoleInstance):
            assert not list((await session.execute(select(model))).scalars())
    detail = (await client.get(f"/api/v1/database/instances/{rows[0].id}", headers=headers)).json()["data"]
    assert detail["needs_configuration"] is True and detail["credential_id"] is None and detail["credential"] is None
    listed = (await client.get("/api/v1/database/instances", headers=headers)).json()["data"]["items"]
    assert all(row["needs_configuration"] for row in listed)
    audit = captured[-1]
    assert audit["action"] == "instance.import"
    assert audit["detail"] == {"upsert": False, "success_count": 2, "created_count": 2, "updated_count": 0, "failed_count": 2}
    assert "target_name" not in audit and "failed_rows" not in audit["detail"]


async def test_upsert_only_updates_excel_columns_and_preserves_connections_and_relations(client, db_factory, seed):
    instance_id, credential_id = await target(db_factory, seed)
    async with db_factory() as session:
        row = await session.get(DatabaseInstance, instance_id)
        row.description, row.cluster_role, row.version = "old", "old", "old"
        row.concurrency_limit, row.last_probe_status, row.last_probe_message = 4, "succeeded", "healthy"
        app = Application(name="app", language="java", deploy_type="jar")
        host = Host(hostname="host", ip="10.1.2.3", environment="demo", status="online")
        session.add_all([app, host])
        await session.flush()
        session.add_all([DatabaseInstanceApplication(instance_id=instance_id, app_id=app.id),
                        DatabaseInstanceHost(instance_id=instance_id, host_id=host.id)])
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    content = workbook(["db-demo", "mysql", "mysql-new", 3307, "prod", None, "tradingkey", "new"])
    duplicate = (await upload(client, headers, content)).json()["data"]
    assert duplicate["success_count"] == 0 and len(duplicate["failed_rows"]) == 1
    response = await upload(client, headers, content, upsert=True)
    assert response.status_code == 200
    assert response.json()["data"] == {"success_count": 1, "created_count": 0, "updated_count": 1, "failed_rows": []}
    async with db_factory() as session:
        row = await session.get(DatabaseInstance, instance_id)
        assert (row.host, row.port, row.environment, row.business_line) == ("mysql-new", 3307, "prod", "tradingkey")
        assert row.description is None and row.cluster_role is None and row.version is None
        assert row.credential_id == credential_id and row.enabled and row.config_revision == 2
        assert row.connection_options == {"default_database": "demo"} and row.concurrency_limit == 4
        assert (row.last_probe_status, row.last_probe_message) == ("succeeded", "healthy")
        for model in (DatabaseInstanceApplication, DatabaseInstanceHost, DatabaseRoleInstance):
            assert len(list((await session.execute(select(model).where(model.instance_id == instance_id))).scalars())) == 1
    unchanged_address = await upload(client, headers, content, upsert=True)
    assert unchanged_address.json()["data"]["updated_count"] == 1
    wrong_engine = await upload(client, headers,
        workbook(["db-demo", "redis", "mysql-new", 6379, "prod"]), upsert=True)
    assert wrong_engine.json()["data"]["success_count"] == 0
    async with db_factory() as session:
        row = await session.get(DatabaseInstance, instance_id)
        assert row.engine == "mysql" and row.config_revision == 2


@pytest.mark.parametrize("remove", ["database:write", "database:import"])
async def test_import_and_template_require_both_permissions(client, db_factory, seed, remove):
    async with db_factory() as session:
        ids = select(Permission.id).where(Permission.code == remove)
        await session.execute(delete(RolePermission).where(RolePermission.role_id == seed["roles"]["admin"],
                                                          RolePermission.permission_id.in_(ids)))
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    assert (await client.get("/api/v1/database/instances/import-template", headers=headers)).status_code == 403
    assert (await upload(client, headers, workbook(["x", "mysql", "x", 3306, "prod"]))).status_code == 403
    async with db_factory() as session:
        assert not list((await session.execute(select(DatabaseInstance))).scalars())


async def test_export_uses_full_list_filters_sort_and_literal_text(client, db_factory, seed, monkeypatch):
    instance_id, _ = await target(db_factory, seed)
    async with db_factory() as session:
        base = await session.get(DatabaseInstance, instance_id)
        base.cluster_name, base.business_line = "export-group", "mitrade"
        base.description = "=HYPERLINK(\"https://invalid.test\")"
        base.connection_options = {"default_database": "hidden-connection-option"}
        extra = DatabaseInstance(name="=1+1", engine="mysql", environment="prod", host="other", port=3307,
            credential_id=base.credential_id, cluster_name="export-group", business_line="mitrade", enabled=True,
            version="@SUM(A1)", description="+cmd", cluster_role="-role")
        session.add(extra)
        session.add(DatabaseInstance(name="outside", engine="redis", environment="demo", host="redis", port=6379,
            credential_id=base.credential_id, cluster_name="else", enabled=False))
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    filters = {"cluster_name": "export-group", "engine": "mysql", "business_line": "mitrade", "enabled": True,
               "probe_status": "untested", "keyword": "export-group", "sort_by": "name", "sort_order": "desc"}
    listing = (await client.get("/api/v1/database/instances", params={**filters, "page_size": 1}, headers=headers)).json()["data"]
    assert listing["total"] == 2
    captured = []
    monkeypatch.setattr("app.api.v1.database_management.audit.log", lambda **kw: captured.append(kw))
    exported = await client.get("/api/v1/database/instances/export", params=filters, headers=headers)
    assert exported.status_code == 200
    book = load_workbook(BytesIO(exported.content), data_only=False)
    rows = list(book.active.iter_rows())
    assert [c.value for c in rows[0]] == HEADERS
    assert len(rows) == 3 and rows[1][0].value == listing["items"][0]["name"]
    assert rows[2][0].value == "=1+1" and rows[2][0].data_type == "s"
    assert all(c.data_type != "f" for row in rows for c in row)
    assert "hidden-connection-option" not in str([[c.value for c in row] for row in rows])
    assert "synthetic-password" not in str([[c.value for c in row] for row in rows])
    assert captured[-1]["detail"] == {"count": 2}
    book.close()
    async with db_factory() as session:
        ids = select(Permission.id).where(Permission.code == "database:read")
        await session.execute(delete(RolePermission).where(RolePermission.role_id == seed["roles"]["admin"],
                                                          RolePermission.permission_id.in_(ids)))
        await session.commit()
    assert (await client.get("/api/v1/database/instances/export", headers=headers)).status_code == 403


async def test_unconfigured_draft_save_enable_rejection_and_rejected_task_evidence(client, db_factory, seed):
    _, credential_id = await target(db_factory, seed, grant=False)
    headers = auth_header(await login_for_tokens(client, "admin"))
    draft = {"name": "draft", "engine": "postgresql", "host": "postgres", "port": 5432, "enabled": False}
    created = await client.post("/api/v1/database/instances", json=draft, headers=headers)
    assert created.status_code == 200, created.text
    instance_id = created.json()["data"]["id"]
    assert (await client.put(f"/api/v1/database/instances/{instance_id}", json={**draft, "enabled": True}, headers=headers)).status_code in (400, 422)
    async with db_factory() as session:
        session.add(DatabaseRoleInstance(instance_id=instance_id, role_id=seed["roles"]["admin"]))
        await session.commit()
    for payload in ({"kind": "probe", "instance_id": instance_id},
                    {"kind": "sql", "instance_id": instance_id, "sql": "SELECT 1"}):
        response = await client.post("/api/v1/database/tasks", json=payload, headers=headers)
        assert response.status_code == 422
        task_id = response.json()["data"]["task_id"]
        async with db_factory() as session:
            task = await session.get(DatabaseTask, task_id)
            assert task.status == "rejected" and task.credential_id is None and task.quota_released
            assert task.target_attempted_at is None and task.credential_snapshot == {}
            assert "凭据" in task.error_message
            event = (await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == task_id))).scalar_one()
            assert event.event_type == "rejected" and event.validation["executed"] is False and event.terminal_at
            assert (await bucket(session, "global", "all")).queued_count == 0
    assert await claim_next_task(db_factory, "test-executor") is None
    response = await client.put(f"/api/v1/database/instances/{instance_id}",
        json={**draft, "credential_id": credential_id, "enabled": True, "connection_options": {"default_database": "acceptance"}}, headers=headers)
    assert response.status_code == 200, response.text
    detail = (await client.get(f"/api/v1/database/instances/{instance_id}", headers=headers)).json()["data"]
    assert detail["needs_configuration"] is False and detail["credential_id"] == credential_id


async def test_invalid_workbook_rejected_before_any_write(client, db_factory):
    headers = auth_header(await login_for_tokens(client, "admin"))
    for content in (b"not an xlsx", workbook(["bad", "mysql", "x", 3306, "demo"], headers=["bad"]),
                    workbook(*[[f"db-{i}", "mysql", "x", 3306, "demo"] for i in range(5001)])):
        response = await upload(client, headers, content)
        assert response.status_code == 400, response.text
    async with db_factory() as session:
        assert not list((await session.execute(select(DatabaseInstance))).scalars())


async def test_formula_import_is_a_failed_row_and_does_not_evaluate(client, db_factory):
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await upload(client, headers, workbook(
        ["=CONCAT(\"unsafe\")", "mysql", "host", 3306, "demo"],
        ["safe", "mysql", "host", 3306, "demo"]))
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["created_count"] == 1 and [r["row"] for r in result["failed_rows"]] == [2]


async def test_late_worksheet_xml_error_is_400_and_writes_no_rows(client, db_factory):
    content = workbook(*[[f"valid-{i}", "mysql", "host", 3306, "demo", "有效描述"*50] for i in range(100)])
    damaged = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(damaged, "w") as archive:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                payload = payload.replace(b"</worksheet>", b"</worksheet")
            archive.writestr(entry, payload)
    # 损坏只出现在流式读取尾部，文件加载与合法表头均能够先成功读取。
    book = load_workbook(BytesIO(damaged.getvalue()), read_only=True)
    assert list(next(book.active.iter_rows(max_row=1, values_only=True))) == HEADERS
    book.close()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await upload(client, headers, damaged.getvalue())
    assert response.status_code == 400, response.text
    assert response.json()["code"] == 40001
    async with db_factory() as session:
        assert not list((await session.execute(select(DatabaseInstance))).scalars())


@pytest.mark.parametrize("cell_type,value", [("n", "broken-number"), ("s", "99999")])
async def test_late_invalid_cell_is_400_and_writes_no_rows(client, db_factory, cell_type, value):
    content = workbook(*[[f"valid-{i}", "mysql", "host", 3306, "demo", "有效描述"*50] for i in range(100)])
    damaged = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(damaged, "w") as archive:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                original = b'<c r="D101" t="n"><v>3306</v></c>'
                assert original in payload
                payload = payload.replace(original, f'<c r="D101" t="{cell_type}"><v>{value}</v></c>'.encode())
            archive.writestr(entry, payload)
    book = load_workbook(BytesIO(damaged.getvalue()), read_only=True)
    assert list(next(book.active.iter_rows(max_row=1, values_only=True))) == HEADERS
    book.close()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await upload(client, headers, damaged.getvalue())
    assert response.status_code == 400, response.text
    assert response.json()["code"] == 40001
    async with db_factory() as session:
        assert not list((await session.execute(select(DatabaseInstance))).scalars())


@pytest.mark.parametrize("error", [RuntimeError("service failure"), Errors.rejected("business rejection")])
def test_row_processing_errors_remain_outside_file_parser_boundary(monkeypatch, error):
    def fail_validation(**_):
        raise error
    monkeypatch.setattr(database_excel, "InstanceWrite", fail_validation)
    with pytest.raises(type(error)) as caught:
        database_excel.parse_import_rows(workbook(["valid", "mysql", "host", 3306, "demo"]))
    assert caught.value is error
