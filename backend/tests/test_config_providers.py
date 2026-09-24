"""应用配置平台 HTTP 适配器的离线契约测试。"""
import base64
import json

import httpx
import pytest

from app.config_providers.apollo import ApolloAdapter
from app.config_providers.base import (
    DiscoverQuery,
    PlatformConnection,
    ProviderRejectedError,
    ProviderWriteUncertainError,
    RemoteContent,
    RemoteExpectation,
)
from app.config_providers.consul import ConsulAdapter
from app.config_providers.nacos import NacosAdapter
from app.core.constants import ConfigProvider
from app.services.config_content_service import validate_and_normalize_content


pytestmark = pytest.mark.asyncio


async def test_nacos_reads_and_writes_exact_namespace_group_data_id():
    """Nacos 的读写必须携带精确租户、分组和 DataId。"""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.method == "GET":
            assert request.url.params["tenant"] == "prod"
            assert request.url.params["group"] == "PAYMENT"
            assert request.url.params["dataId"] == "payment.yaml"
            return httpx.Response(200, text="enabled: true\n", headers={"Last-Modified": "11"})
        assert request.method == "POST"
        assert b"dataId=payment.yaml" in request.content
        assert b"group=PAYMENT" in request.content
        assert b"tenant=prod" in request.content
        return httpx.Response(200, text="true")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://nacos.example")
    adapter = NacosAdapter(requester=client)
    connection = PlatformConnection(
        provider=ConfigProvider.NACOS,
        base_url="https://nacos.example",
        credential_id=1,
        auth_type="api_token",
        secret="test-token",
    )
    try:
        content = await adapter.read(connection, {"namespace": "prod", "group": "PAYMENT", "data_id": "payment.yaml"})
        assert content is not None
        assert content.content == "enabled: true\n"
        await adapter.write(
            connection,
            content.locator,
            validate_and_normalize_content("yaml", "enabled: false\n"),
            expect=RemoteExpectation.exists(content),
        )
    finally:
        await client.aclose()

    assert len(captured) == 3


async def test_nacos_write_timeout_is_uncertain_and_is_not_retried():
    """写请求网络中断不能被适配器自动重试。"""
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        if request.method == "GET":
            return httpx.Response(404)
        count += 1
        raise httpx.ReadTimeout("timeout", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://nacos.example")
    adapter = NacosAdapter(requester=client)
    connection = PlatformConnection(
        provider=ConfigProvider.NACOS,
        base_url="https://nacos.example",
        credential_id=1,
        auth_type="api_token",
        secret="test-token",
    )
    try:
        with pytest.raises(ProviderWriteUncertainError):
            await adapter.write(
                connection,
                {"namespace": "prod", "group": "DEFAULT_GROUP", "data_id": "payment.yaml"},
                validate_and_normalize_content("yaml", "enabled: false\n"),
                expect=RemoteExpectation.missing(),
            )
    finally:
        await client.aclose()

    assert count == 1


async def test_consul_reads_base64_kv_and_rejects_oversized_transaction():
    """Consul 读取精确保留 UTF-8 值，超过事务上限时不发起写请求。"""
    methods = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        assert request.headers["X-Consul-Token"] == "acl-token"
        return httpx.Response(200, json=[{
            "Key": "prod/payment/enabled",
            "Value": base64.b64encode(b"true").decode(),
            "ModifyIndex": 23,
        }])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://consul.example")
    adapter = ConsulAdapter(requester=client)
    connection = PlatformConnection(
        provider=ConfigProvider.CONSUL,
        base_url="https://consul.example",
        credential_id=1,
        auth_type="api_token",
        secret="acl-token",
    )
    locator = {"datacenter": "dc1", "kv_prefix": "prod/payment"}
    try:
        content = await adapter.read(connection, locator)
        assert content is not None
        assert content.content == '{\n  "enabled": "true"\n}'
        oversized = {f"key-{index}": "value" for index in range(65)}
        with pytest.raises(ProviderRejectedError, match="64"):
            await adapter.write(
                connection,
                locator,
                validate_and_normalize_content("consul_kv", __import__("json").dumps(oversized)),
                expect=RemoteExpectation.exists(content),
            )
    finally:
        await client.aclose()

    assert methods and all(method == "GET" for method in methods)


async def test_apollo_reads_exact_app_cluster_namespace_with_open_api_token():
    """Apollo Open API 读取必须使用 AppId、Cluster 与 Namespace。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "test-open-api-token"
        assert request.url.path.startswith("/openapi/v1/envs/PRO/apps/payment/clusters/default/namespaces/application.yaml")
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"configurations": {"content": "enabled: true\n"}})
        return httpx.Response(200, json={"format": "yaml", "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://apollo.example")
    adapter = ApolloAdapter(requester=client)
    connection = PlatformConnection(
        provider=ConfigProvider.APOLLO,
        base_url="https://apollo.example",
        credential_id=1,
        auth_type="api_token",
        secret="test-open-api-token",
    )
    try:
        content = await adapter.read(connection, {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"})
    finally:
        await client.aclose()

    assert content is not None
    assert content.content == "enabled: true\n"


async def test_apollo_discovers_production_namespaces():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/openapi/v1/envs/PRO/apps/payment/clusters/default/namespaces"
        return httpx.Response(200, json=[{"namespaceName": "application.yaml"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ApolloAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
        result = await adapter.discover(connection, DiscoverQuery({"app_id": "payment", "cluster": "default"}))
    assert result[0].locator == {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"}


async def test_apollo_first_release_in_existing_empty_namespace():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, json={"format": "yaml", "items": []})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ApolloAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
        locator = {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"}
        await adapter.write(connection, locator, validate_and_normalize_content("yaml", "enabled: true"),
                            expect=RemoteExpectation.missing())
    writes = [request for request in requests if request.method == "POST"]
    assert [request.url.path.rsplit("/", 1)[-1] for request in writes] == ["items", "releases"]
    assert json.loads(writes[0].content) == {
        "key": "content", "value": "enabled: true\n", "dataChangeCreatedBy": "OpsPilot",
    }


async def test_apollo_first_release_creates_exact_private_namespace_after_parent_check():
    requests = []
    created = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal created
        requests.append(request)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(404)
        if request.url.path.endswith("/namespaces/application.yaml") and request.method == "GET":
            return httpx.Response(200, json={"format": "yaml", "items": []}) if created else httpx.Response(404)
        if request.url.path.endswith("/appnamespaces"):
            created = True
            return httpx.Response(200, json={"name": "application.yaml"})
        if request.method == "GET":
            return httpx.Response(200, json={})
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ApolloAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
        await adapter.write(connection, {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"},
                            validate_and_normalize_content("yaml", "enabled: true"),
                            expect=RemoteExpectation.missing())
    creation = next(request for request in requests if request.url.path.endswith("/appnamespaces"))
    assert json.loads(creation.content) == {
        "appId": "payment", "name": "application", "format": "yaml", "isPublic": False,
        "appendNamespacePrefix": False, "dataChangeCreatedBy": "OpsPilot",
    }
    assert any(request.url.path.endswith("/clusters/default") and request.method == "GET"
               for request in requests[:requests.index(creation)])
    assert requests[-1].url.path.endswith("/releases")


async def test_apollo_missing_parent_never_creates_namespace():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = ApolloAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
        with pytest.raises(ProviderRejectedError):
            await adapter.write(connection, {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"},
                                validate_and_normalize_content("yaml", "enabled: true"),
                                expect=RemoteExpectation.missing())
    assert all(request.method == "GET" for request in requests)


async def test_nacos_discovery_reads_all_pages():
    page_numbers = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["pageNo"])
        page_numbers.append(page)
        items = [{"tenant": "prod", "group": "G", "dataId": f"config-{index}.yaml"}
                 for index in range((page - 1) * 200, min(page * 200, 201))]
        return httpx.Response(200, json={"totalCount": 201, "pageItems": items})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = NacosAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "token")
        resources = await adapter.discover(connection, DiscoverQuery({"tenant": "prod"}))
    assert page_numbers == [1, 2]
    assert len(resources) == 201
    assert resources[-1].locator["data_id"] == "config-200.yaml"


async def test_nacos_discovery_sends_empty_filters_for_unfiltered_listing():
    """Nacos 2.2.3 列表接口要求空的 DataId 和 Group 也显式传入。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if "dataId" not in request.url.params or "group" not in request.url.params:
            return httpx.Response(500)
        if request.url.params["dataId"] != "" or request.url.params["group"] != "":
            return httpx.Response(400)
        return httpx.Response(200, json={
            "totalCount": 1,
            "pageItems": [{"tenant": "prod", "group": "PAY", "dataId": "payment.yaml"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = NacosAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "token")
        resources = await adapter.discover(connection, DiscoverQuery({"tenant": "prod"}))
    assert [resource.locator for resource in resources] == [
        {"namespace": "prod", "group": "PAY", "data_id": "payment.yaml"},
    ]


async def test_nacos_lists_namespaces_with_public_mapped_to_empty_tenant():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/nacos/v1/console/namespaces"
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(200, json={"code": 200, "message": None, "data": [
            {"namespace": "public", "namespaceShowName": "public", "namespaceDesc": "", "configCount": 2, "type": 0},
            {"namespace": "prod-id", "namespaceShowName": "生产", "namespaceDesc": "", "configCount": 3, "type": 2},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = NacosAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "token")
        namespaces = await adapter.list_namespaces(connection)
    assert namespaces == [{"id": "", "name": "public"}, {"id": "prod-id", "name": "生产"}]


async def test_nacos_public_discovery_and_read_use_empty_tenant():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["tenant"] == ""
        if "pageNo" in request.url.params:
            return httpx.Response(200, json={"totalCount": 1, "pageItems": [
                {"tenant": "", "group": "DEFAULT_GROUP", "dataId": "common.yaml"},
            ]})
        return httpx.Response(200, text="enabled: true\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = NacosAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "token")
        resources = await adapter.discover(connection, DiscoverQuery({"tenant": ""}))
        content = await adapter.read(connection, resources[0].locator)
    assert resources[0].locator == {"namespace": "", "group": "DEFAULT_GROUP", "data_id": "common.yaml"}
    assert content is not None and content.content == "enabled: true\n"


async def test_consul_transaction_cas_updates_and_deletes_exact_prefix():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=[
                {"Key": "prod/payment/keep", "Value": base64.b64encode(b"old").decode(), "ModifyIndex": 7},
                {"Key": "prod/payment/remove", "Value": base64.b64encode(b"gone").decode(), "ModifyIndex": 8},
            ])
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"Results": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ConsulAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.CONSUL, "https://consul.example", 1, "api_token", "token")
    locator = {"datacenter": "dc1", "kv_prefix": "prod/payment"}
    try:
        remote = await adapter.read(connection, locator)
        await adapter.write(connection, locator,
                            validate_and_normalize_content("consul_kv", '{"keep":"new"}'),
                            expect=RemoteExpectation.exists(remote))
    finally:
        await client.aclose()
    assert seen == [[
        {"KV": {"Verb": "cas", "Key": "prod/payment/keep", "Value": "bmV3", "Index": 7}},
        {"KV": {"Verb": "delete-cas", "Key": "prod/payment/remove", "Index": 8}},
    ]]


async def test_apollo_write_releases_changes_after_updating_item():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            if request.url.path.endswith("/releases/latest"):
                return httpx.Response(200, json={"configurations": {"content": "old"}})
            return httpx.Response(200, json={"format": "yaml", "items": [{"key": "content", "value": "old"}]})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ApolloAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
    locator = {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"}
    try:
        await adapter.write(connection, locator, validate_and_normalize_content("yaml", "enabled: true"),
                            expect=RemoteExpectation.exists(RemoteContent(locator, "old")))
    finally:
        await client.aclose()
    assert requests[-1] == (
        "POST", "/openapi/v1/envs/PRO/apps/payment/clusters/default/namespaces/application.yaml/releases",
    )


async def test_apollo_rejects_first_write_without_existing_parent_cluster():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ApolloAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
    try:
        with pytest.raises(ProviderRejectedError, match="http_404"):
            await adapter.write(connection,
                                {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"},
                                validate_and_normalize_content("yaml", "enabled: true"),
                                expect=RemoteExpectation.missing())
    finally:
        await client.aclose()
    assert all(method == "GET" for method in requests)


async def test_nacos_write_does_not_overwrite_changed_remote_content():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text="changed: true\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = NacosAdapter(requester=client)
        connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "token")
        locator = {"namespace": "prod", "group": "G", "data_id": "payment.yaml"}
        with pytest.raises(ProviderRejectedError, match="remote_changed"):
            await adapter.write(connection, locator, validate_and_normalize_content("yaml", "enabled: false"),
                                expect=RemoteExpectation.exists(RemoteContent(locator, "enabled: true\n")))
    assert [request.method for request in requests] == ["GET"]


async def test_apollo_release_failure_after_item_update_is_uncertain():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"configurations": {"content": "old"}})
        if request.method == "GET":
            return httpx.Response(200, json={"format": "yaml", "items": [{"key": "content", "value": "old"}]})
        if request.url.path.endswith("/releases"):
            return httpx.Response(400)
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ApolloAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
    locator = {"app_id": "payment", "cluster": "default", "namespace": "application.yaml"}
    try:
        with pytest.raises(ProviderWriteUncertainError):
            await adapter.write(connection, locator, validate_and_normalize_content("yaml", "enabled: true"),
                                expect=RemoteExpectation.exists(RemoteContent(locator, "old")))
    finally:
        await client.aclose()


async def test_apollo_properties_replace_items_then_release():
    changes = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"configurations": {"keep": "old", "remove": "gone"}})
        if request.method == "GET":
            return httpx.Response(200, json={"items": [
                {"key": "keep", "value": "old"}, {"key": "remove", "value": "gone"},
            ], "format": "properties"})
        changes.append((request.method, request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ApolloAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
    locator = {"app_id": "payment", "cluster": "default", "namespace": "application"}
    try:
        await adapter.write(connection, locator,
                            validate_and_normalize_content("properties", "keep=new\nadd=hello\n"),
                            expect=RemoteExpectation.exists(RemoteContent(locator, "keep=old\nremove=gone\n")))
    finally:
        await client.aclose()
    assert changes[:3] == [
        ("PUT", "batch-update", [{"key": "keep", "value": "new"}]),
        ("POST", "batch-create", [{"key": "add", "value": "hello"}]),
        ("POST", "batch-delete", ["remove"]),
    ]
    assert changes[3][0:2] == ("POST", "releases")


async def test_apollo_properties_key_named_content_is_not_file_body():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"configurations": {"content": "ordinary-value"}})
        return httpx.Response(200, json={"format": "properties", "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = ApolloAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token")
    try:
        remote = await adapter.read(connection, {"app_id": "payment", "cluster": "default", "namespace": "application"})
        assert remote.content == "content=ordinary-value\n"
    finally:
        await client.aclose()


async def test_nacos_rejects_false_publish_response():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        return httpx.Response(200, text="false")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = NacosAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "token")
    try:
        with pytest.raises(ProviderRejectedError):
            await adapter.write(connection,
                                {"namespace": "prod", "group": "PAY", "data_id": "payment.yaml"},
                                validate_and_normalize_content("yaml", "enabled: true"),
                                expect=RemoteExpectation.missing())
    finally:
        await client.aclose()


async def test_nacos_token_stays_out_of_url_and_http_logs(caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-token"
        assert "private-token" not in str(request.url)
        return httpx.Response(200, text="enabled: true\n")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = NacosAdapter(requester=client)
    connection = PlatformConnection(ConfigProvider.NACOS, "https://nacos.example", 1, "api_token", "private-token")
    try:
        with caplog.at_level("INFO", logger="httpx"):
            result = await adapter.read(connection, {"namespace": "prod", "group": "PAY", "data_id": "payment.yaml"})
        assert result.content == "enabled: true\n"
        assert "private-token" not in caplog.text
    finally:
        await client.aclose()


async def test_provider_rejects_locator_path_delimiters_before_http():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        apollo = ApolloAdapter(requester=client)
        with pytest.raises(ProviderRejectedError, match="invalid_field"):
            await apollo.read(
                PlatformConnection(ConfigProvider.APOLLO, "https://apollo.example", 1, "api_token", "token"),
                {"app_id": "../admin", "cluster": "default", "namespace": "application.yaml"},
            )
        consul = ConsulAdapter(requester=client)
        with pytest.raises(ProviderRejectedError, match="invalid_field"):
            await consul.read(
                PlatformConnection(ConfigProvider.CONSUL, "https://consul.example", 1, "api_token", "token"),
                {"datacenter": "dc1", "kv_prefix": "prod/payment?dc=dc2"},
            )
    finally:
        await client.aclose()
    assert calls == []
