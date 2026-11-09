"""集群精确筛选及候选列表的完整性、分页与授权边界。"""
from datetime import datetime

import pytest
from sqlalchemy import delete, select

from conftest import auth_header, login_for_tokens
from app.core.security import encrypt_text
from app.models.auth import Permission, RolePermission
from app.models.cmdb import Application, Host
from app.models.database_management import (DatabaseInstance, DatabaseInstanceApplication,
    DatabaseInstanceHost, DatabaseRoleInstance)
from app.models.job import Credential


@pytest.fixture
async def inventory(db_factory, seed):
    async with db_factory() as session:
        credential = Credential(name="cluster fixture", auth_type="username_password", login_user="readonly",
                                secret_enc=encrypt_text("synthetic-only"))
        app = Application(name="cluster app", deploy_type="shell")
        host = Host(hostname="cluster host", ip="192.0.2.31", environment="demo")
        session.add_all([credential, app, host])
        await session.flush()
        specifications = [
            ("needle", "alpha", "mysql", "mitrade", True, "succeeded", True),
            ("other-alpha", "alpha", "postgresql", "tradingkey", False, "failed", False),
            ("prefix", "alpha-extra", "mysql", "mitrade", True, "succeeded", False),
            ("beta", "beta", "mysql", "mitrade", True, "failed", True),
            ("gamma", "gamma", "redis", None, True, "untested", False),
            ("duplicate-beta", "beta", "clickhouse", None, True, "untested", False),
            ("null-cluster", None, "mysql", None, True, "untested", False),
            ("empty-cluster", "", "mysql", None, True, "untested", False),
            ("restricted", "secret", "mongodb", None, True, "untested", False),
        ]
        ids = {}
        for name, cluster, engine, business, enabled, probe, granted in specifications:
            instance = DatabaseInstance(name=name, cluster_name=cluster, engine=engine, business_line=business,
                enabled=enabled, last_probe_status=probe, environment="demo", host="fixture", port=3306,
                credential_id=credential.id, connection_options={})
            session.add(instance)
            await session.flush()
            ids[name] = instance.id
            if granted:
                session.add(DatabaseRoleInstance(instance_id=instance.id, role_id=seed["roles"]["admin"]))
        session.add_all([DatabaseInstanceApplication(instance_id=ids["needle"], app_id=app.id),
                         DatabaseInstanceHost(instance_id=ids["needle"], host_id=host.id)])
        await session.commit()
        return {"ids": ids, "app_id": app.id, "host_id": host.id,
                "clusters": ["alpha", "alpha-extra", "beta", "gamma", "secret"]}


async def get_inventory(client, **params):
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.get("/api/v1/database/instances", headers=headers, params=params)
    assert response.status_code == 200, response.json()
    return response.json()["data"]


async def test_cluster_filter_matches_exact_name_instead_of_prefix(client, inventory):
    data = await get_inventory(client, cluster_name="alpha")
    assert data["total"] == 2
    assert {row["id"] for row in data["items"]} == {inventory["ids"]["needle"], inventory["ids"]["other-alpha"]}
    assert {row["cluster_name"] for row in data["items"]} == {"alpha"}


async def test_cluster_candidates_cover_all_pages_and_deduplicate_nonempty_names(client, inventory):
    first = await get_inventory(client, page=1, page_size=1)
    second = await get_inventory(client, page=2, page_size=1)
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert first["cluster_names"] == second["cluster_names"] == inventory["clusters"]


async def test_cluster_filter_combines_with_all_existing_filters_without_shrinking_candidates(client, inventory):
    data = await get_inventory(client, keyword="needle", cluster_name="alpha", engine="mysql",
        business_line="mitrade", enabled="true", probe_status="succeeded", instance_id=inventory["ids"]["needle"],
        app_id=inventory["app_id"], host_id=inventory["host_id"], page=1, page_size=1)
    assert data["total"] == 1 and data["items"][0]["id"] == inventory["ids"]["needle"]
    mismatch = await get_inventory(client, cluster_name="beta", instance_id=inventory["ids"]["needle"])
    assert mismatch["total"] == 0 and mismatch["items"] == []
    assert data["cluster_names"] == inventory["clusters"]
    assert mismatch["cluster_names"] == inventory["clusters"]


async def test_query_candidates_stay_within_explicit_grants_and_ignore_filters(client, inventory):
    data = await get_inventory(client, scope="query", keyword="needle", engine="mysql", cluster_name="alpha", page_size=1)
    assert data["total"] == 1 and data["items"][0]["id"] == inventory["ids"]["needle"]
    assert data["cluster_names"] == ["alpha", "beta"]
    hidden = await get_inventory(client, scope="query", cluster_name="secret")
    assert hidden["total"] == 0 and hidden["items"] == []
    assert hidden["cluster_names"] == ["alpha", "beta"]


async def test_query_without_instance_grants_has_no_cluster_candidates(client, db_factory, inventory):
    async with db_factory() as session:
        await session.execute(delete(DatabaseRoleInstance))
        await session.commit()
    data = await get_inventory(client, scope="query")
    assert data["total"] == 0 and data["items"] == [] and data["cluster_names"] == []


@pytest.mark.parametrize("scope", ["management", "query"])
async def test_cluster_candidates_require_scope_permission(client, db_factory, seed, inventory, scope):
    async with db_factory() as session:
        await session.execute(delete(RolePermission).where(RolePermission.role_id == seed["roles"]["ops"]))
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "ops1"))
    response = await client.get("/api/v1/database/instances", headers=headers, params={"scope": scope, "cluster_name": "alpha"})
    assert response.status_code == 403
    assert response.json()["data"] is None


@pytest.mark.parametrize("length", [128, 129])
async def test_cluster_query_accepts_128_characters_and_rejects_129(client, db_factory, inventory, length):
    cluster = "c" * 128
    async with db_factory() as session:
        (await session.get(DatabaseInstance, inventory["ids"]["needle"])).cluster_name = cluster
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.get("/api/v1/database/instances", headers=headers, params={"cluster_name": "c" * length})
    if length == 129:
        assert response.status_code == 422
    else:
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["total"] == 1 and data["items"][0]["cluster_name"] == cluster


@pytest.mark.parametrize("address,port,keyword", [
    ("db.example.test", 15432, "db.example.test"),
    ("db.example.test", 15432, "15432"),
    ("db.example.test", 15432, "db.example.test:15432"),
    ("2001:db8::2", 15432, "2001:db8::2"),
    ("2001:db8::2", 15432, "[2001:db8::2]:15432"),
    ("[2001:db8::2]", 15432, "[2001:db8::2]:15432"),
])
async def test_visible_connection_address_is_searchable(client, db_factory, inventory, address, port, keyword):
    async with db_factory() as session:
        row = await session.get(DatabaseInstance, inventory["ids"]["needle"])
        row.host, row.port = address, port
        await session.commit()
    data = await get_inventory(client, keyword=keyword)
    assert data["total"] == 1
    row = data["items"][0]
    assert row["id"] == inventory["ids"]["needle"]
    assert row["host"] == address and row["port"] == port
    assert "credential" not in row and "connection_options" not in row


async def test_connection_address_search_cannot_reveal_ungranted_instances(client, db_factory, inventory):
    async with db_factory() as session:
        row = await session.get(DatabaseInstance, inventory["ids"]["restricted"])
        row.host, row.port = "restricted.example.test", 19999
        await session.commit()
    hidden = await get_inventory(client, scope="query", keyword="restricted.example.test:19999")
    assert hidden["total"] == 0 and hidden["items"] == []
    visible = await get_inventory(client, scope="query", keyword="needle")
    assert visible["items"][0]["host"] == "fixture"
    assert visible["items"][0]["port"] == 3306


@pytest.mark.parametrize("keyword,names", [
    ("成功", {"needle", "prefix"}),
    ("失败", {"other-alpha", "beta"}),
    ("未检测", {"gamma", "duplicate-beta", "null-cluster", "empty-cluster", "restricted"}),
    ("连接正常", {"needle", "prefix"}),
    ("启用/成功", {"needle", "prefix"}),
    ("停用/失败", {"other-alpha"}),
])
async def test_visible_probe_status_labels_remain_searchable(client, inventory, keyword, names):
    data = await get_inventory(client, keyword=keyword)
    assert {row["name"] for row in data["items"]} == names
    assert data["total"] == len(names)


async def test_probe_status_search_stays_within_query_grants(client, inventory):
    data = await get_inventory(client, scope="query", keyword="成功")
    assert [row["name"] for row in data["items"]] == ["needle"]


async def test_sorting_happens_before_pagination_with_stable_ties(client, inventory):
    first = await get_inventory(client, sort_by="cluster_name", sort_order="asc", page_size=3)
    second = await get_inventory(client, sort_by="cluster_name", sort_order="asc", page=2, page_size=3)
    sorted_ids = [row["id"] for row in first["items"] + second["items"]]
    expected = [inventory["ids"][name] for name in (
        "other-alpha", "needle", "prefix", "duplicate-beta", "beta", "gamma")]
    assert sorted_ids == expected
    descending = await get_inventory(client, sort_by="cluster_name", sort_order="desc")
    assert [row["name"] for row in descending["items"]][-2:] == ["empty-cluster", "null-cluster"]
    assert [row["name"] for row in descending["items"]][:3] == ["restricted", "gamma", "duplicate-beta"]
    default = await get_inventory(client)
    assert [row["id"] for row in default["items"]] == sorted(inventory["ids"].values(), reverse=True)


async def test_sorting_address_uses_numeric_port_and_preserves_query_scope(client, db_factory, inventory):
    async with db_factory() as session:
        (await session.get(DatabaseInstance, inventory["ids"]["needle"])).port = 100
        (await session.get(DatabaseInstance, inventory["ids"]["other-alpha"])).port = 9
        await session.commit()
    rows = await get_inventory(client, cluster_name="alpha", sort_by="address", sort_order="asc")
    assert [row["name"] for row in rows["items"]] == ["other-alpha", "needle"]
    reverse = await get_inventory(client, cluster_name="alpha", sort_by="address", sort_order="desc")
    assert [row["name"] for row in reverse["items"]] == ["needle", "other-alpha"]
    granted = await get_inventory(client, scope="query", sort_by="name", sort_order="asc")
    assert [row["name"] for row in granted["items"]] == ["beta", "needle"]
    assert "restricted" not in [row["name"] for row in granted["items"]]


@pytest.mark.parametrize("sort_by,ascending", [
    ("name", ["needle", "other-alpha"]),
    ("description", ["needle", "other-alpha"]),
    ("engine", ["needle", "other-alpha"]),
    ("business_line", ["needle", "other-alpha"]),
    ("cluster_role", ["needle", "other-alpha"]),
    ("version", ["needle", "other-alpha"]),
    ("created_at", ["needle", "other-alpha"]),
    ("status", ["other-alpha", "needle"]),
])
async def test_visible_scalar_columns_sort_in_both_directions(client, db_factory, inventory, sort_by, ascending):
    async with db_factory() as session:
        needle = await session.get(DatabaseInstance, inventory["ids"]["needle"])
        other = await session.get(DatabaseInstance, inventory["ids"]["other-alpha"])
        needle.description, other.description = "a", "z"
        needle.cluster_role, other.cluster_role = "primary", "replica"
        needle.version, other.version = "1", "2"
        needle.created_at, other.created_at = datetime(2025, 1, 1), datetime(2026, 1, 1)
        await session.commit()
    asc = await get_inventory(client, cluster_name="alpha", sort_by=sort_by, sort_order="asc")
    desc = await get_inventory(client, cluster_name="alpha", sort_by=sort_by, sort_order="desc")
    assert [row["name"] for row in asc["items"]] == ascending
    assert [row["name"] for row in desc["items"]] == list(reversed(ascending))


async def test_sorting_related_names_keeps_one_row_per_instance_and_nulls_last(client, db_factory, inventory):
    async with db_factory() as session:
        alpha = Application(name="alpha app", deploy_type="docker")
        zulu = Application(name="zulu app", deploy_type="shell")
        first_host = Host(hostname="alpha host", ip="192.0.2.32", environment="stage")
        last_host = Host(hostname="zulu host", ip="192.0.2.33", environment="prod")
        session.add_all([alpha, zulu, first_host, last_host])
        await session.flush()
        session.add_all([
            DatabaseInstanceApplication(instance_id=inventory["ids"]["needle"], app_id=zulu.id),
            DatabaseInstanceApplication(instance_id=inventory["ids"]["beta"], app_id=alpha.id),
            DatabaseInstanceHost(instance_id=inventory["ids"]["needle"], host_id=last_host.id),
            DatabaseInstanceHost(instance_id=inventory["ids"]["beta"], host_id=first_host.id),
        ])
        await session.commit()
    apps = await get_inventory(client, sort_by="apps", sort_order="asc")
    hosts = await get_inventory(client, sort_by="hosts", sort_order="desc")
    assert apps["total"] == hosts["total"] == len(inventory["ids"])
    assert [row["name"] for row in apps["items"]][:2] == ["beta", "needle"]
    assert [row["name"] for row in hosts["items"]][:2] == ["needle", "beta"]
    assert len({row["id"] for row in apps["items"]}) == apps["total"]
    assert len({row["id"] for row in hosts["items"]}) == hosts["total"]


async def test_detail_relation_columns_require_cmdb_read(client, db_factory, seed, inventory):
    async with db_factory() as session:
        app = await session.get(Application, inventory["app_id"])
        app.language = "Python"
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "admin"))
    url = f"/api/v1/database/instances/{inventory['ids']['needle']}"
    detail = (await client.get(url, headers=headers)).json()["data"]
    assert detail["apps"] == [{"id": inventory["app_id"], "name": "cluster app",
                               "language": "Python", "deploy_type": "shell"}]
    assert detail["hosts"] == [{"id": inventory["host_id"], "hostname": "cluster host",
                                "ip": "192.0.2.31", "environment": "demo"}]

    async with db_factory() as session:
        permission_id = (await session.execute(select(Permission.id).where(Permission.code == "cmdb:read"))).scalar_one()
        await session.execute(delete(RolePermission).where(
            RolePermission.role_id == seed["roles"]["admin"], RolePermission.permission_id == permission_id))
        await session.commit()
    restricted = (await client.get(url, headers=headers)).json()["data"]
    assert restricted["apps"] == [{"id": inventory["app_id"], "name": "cluster app"}]
    assert restricted["hosts"] == [{"id": inventory["host_id"], "hostname": "cluster host",
                                    "ip": "192.0.2.31"}]


@pytest.mark.parametrize("params", [{"sort_by": "credential_id"}, {"sort_by": "name", "sort_order": "random"}])
async def test_sorting_rejects_unknown_columns_and_direction(client, inventory, params):
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.get("/api/v1/database/instances", headers=headers, params=params)
    assert response.status_code == 422
