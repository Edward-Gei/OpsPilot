"""DNS 服务商适配器的离线回归测试。"""
import copy

import pytest
from google.auth import exceptions as google_auth_exceptions

from app.core.constants import DomainProvider
from app.dns_providers.aws_route53 import AwsRoute53Adapter
from app.dns_providers.base import (
    ProviderCredential,
    ProviderRejectedError,
    ProviderUnavailableError,
    RecordSetDraft,
    RemoteRecordSet,
    ZoneRef,
)
from app.dns_providers.google_cloud_dns import GoogleCloudDnsAdapter
from app.dns_providers.tencent_dnspod import TencentDnsPodAdapter


AWS_CREDENTIAL = ProviderCredential(DomainProvider.AWS_ROUTE53, 1, "AKIA_TEST", "secret")
AWS_ZONE = ZoneRef(DomainProvider.AWS_ROUTE53, "ZPUBLIC", "example.com")
TENCENT_CREDENTIAL = ProviderCredential(DomainProvider.TENCENT_DNSPOD, 2, "SECRET_ID", "secret")
TENCENT_ZONE = ZoneRef(DomainProvider.TENCENT_DNSPOD, "21", "example.com")
GOOGLE_CREDENTIAL = ProviderCredential(DomainProvider.GOOGLE_CLOUD_DNS, 3, None, '{"project_id":"dns-test"}')
GOOGLE_ZONE = ZoneRef(DomainProvider.GOOGLE_CLOUD_DNS, "public-zone", "example.com")


class FakeRoute53Client:
    def __init__(self, *, hosted_zone_pages=None, record_pages=None, change_statuses=None, error=None):
        self.hosted_zone_pages = list(hosted_zone_pages or [{"HostedZones": [], "IsTruncated": False}])
        self.record_pages = list(record_pages or [{"ResourceRecordSets": [], "IsTruncated": False}])
        self.change_statuses = list(change_statuses or ["INSYNC"])
        self.error = error
        self.hosted_zone_calls: list[dict] = []
        self.record_calls: list[dict] = []
        self.change_requests: list[dict] = []
        self.change_reads: list[str] = []

    def list_hosted_zones(self, **kwargs):
        self.hosted_zone_calls.append(kwargs)
        if self.error:
            raise self.error
        return copy.deepcopy(self.hosted_zone_pages.pop(0))

    def list_resource_record_sets(self, **kwargs):
        self.record_calls.append(kwargs)
        if self.error:
            raise self.error
        return copy.deepcopy(self.record_pages.pop(0))

    def change_resource_record_sets(self, **kwargs):
        self.change_requests.append(copy.deepcopy(kwargs))
        if self.error:
            raise self.error
        return {"ChangeInfo": {"Id": "/change/C1"}}

    def get_change(self, **kwargs):
        self.change_reads.append(kwargs["Id"])
        status = self.change_statuses.pop(0) if len(self.change_statuses) > 1 else self.change_statuses[0]
        return {"ChangeInfo": {"Status": status}}


class FakeClientError(Exception):
    def __init__(self, code: str):
        self.response = {"Error": {"Code": code}}


class FakeTencentError(Exception):
    def __init__(self, code: str):
        self._code = code

    def get_code(self) -> str:
        return self._code


class FakeDnsPodResponder:
    def __init__(self, responses: dict[str, list[dict | Exception]]):
        self.responses = {action: list(items) for action, items in responses.items()}
        self.actions: list[str] = []
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, action: str, payload: dict, credential: ProviderCredential) -> dict:
        self.actions.append(action)
        self.calls.append((action, copy.deepcopy(payload)))
        queue = self.responses.get(action)
        if not queue:
            return {}
        result = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


class FakeGoogleRecord:
    def __init__(self, name: str, record_type: str, ttl: int, rrdatas: list[str], **attributes):
        self.name = name
        self.record_type = record_type
        self.ttl = ttl
        self.rrdatas = rrdatas
        for key, value in attributes.items():
            setattr(self, key, value)


class FakeGooglePage:
    def __init__(self, raw_page: dict):
        self.raw_page = raw_page


class FakeGoogleRecordIterator:
    def __init__(self, records: list[FakeGoogleRecord], raw_pages: list[dict]):
        self._records = records
        self.pages = [FakeGooglePage(raw_page) for raw_page in raw_pages]

    def __iter__(self):
        return iter(self._records)


class FakeGoogleChange:
    def __init__(self, statuses: list[str]):
        self._statuses = list(statuses)
        self.status = self._statuses.pop(0) if self._statuses else "done"
        self.added: list[FakeGoogleRecord] = []
        self.deleted: list[FakeGoogleRecord] = []
        self.create_calls = 0
        self.reload_calls = 0

    def add_record_set(self, record: FakeGoogleRecord):
        self.added.append(record)

    def delete_record_set(self, record: FakeGoogleRecord):
        self.deleted.append(record)

    def create(self):
        self.create_calls += 1

    def reload(self):
        self.reload_calls += 1
        if self._statuses:
            self.status = self._statuses.pop(0)


class FakeGoogleZone:
    def __init__(
        self,
        name: str,
        dns_name: str,
        visibility: str,
        *,
        records: list[FakeGoogleRecord] | None = None,
        raw_pages: list[dict] | None = None,
        change_statuses: list[str] | None = None,
    ):
        self.name = name
        self.dns_name = dns_name
        self.visibility = visibility
        self.records = list(records or [])
        self.raw_pages = raw_pages
        self.change_statuses = list(change_statuses or ["done"])
        self.changes_created: list[FakeGoogleChange] = []

    def list_resource_record_sets(self):
        if self.raw_pages is not None:
            return FakeGoogleRecordIterator(self.records, self.raw_pages)
        return list(self.records)

    def resource_record_set(self, name: str, record_type: str, ttl: int, rrdatas: list[str]):
        return FakeGoogleRecord(name, record_type, ttl, rrdatas)

    def changes(self):
        change = FakeGoogleChange(self.change_statuses)
        self.changes_created.append(change)
        return change


class FakeGoogleDnsClient:
    def __init__(self, managed_zones: list[FakeGoogleZone]):
        self.managed_zones = managed_zones
        self.zones = {zone.name: zone for zone in managed_zones}
        self.zone_calls: list[str] = []

    def list_zones(self):
        return list(self.managed_zones)

    def zone(self, name: str):
        self.zone_calls.append(name)
        return self.zones[name]


def _dnspod_record(record_id: int, value: str, *, name: str = "@", record_type: str = "A", ttl: int = 300, **extra):
    return {
        "RecordId": record_id,
        "Name": name,
        "Type": record_type,
        "TTL": ttl,
        "Value": value,
        "Status": "ENABLE",
        "Line": "默认",
        **extra,
    }


@pytest.mark.asyncio
async def test_dnspod_groups_simple_records_as_one_record_set():
    responder = FakeDnsPodResponder({
        "DescribeRecordList": [{"RecordList": [
            _dnspod_record(10, "192.0.2.1"),
            _dnspod_record(11, "192.0.2.2"),
        ]}],
    })
    adapter = TencentDnsPodAdapter(requester=responder)

    records = await adapter.list_record_sets(TENCENT_ZONE, TENCENT_CREDENTIAL)

    assert records[0].record_name == "example.com"
    assert records[0].values == ("192.0.2.1", "192.0.2.2")
    assert records[0].provider_meta["record_ids"] == [10, 11]


@pytest.mark.asyncio
async def test_dnspod_lists_existing_wildcard_record():
    responder = FakeDnsPodResponder({
        "DescribeRecordList": [{"RecordList": [_dnspod_record(10, "192.0.2.1", name="*")]}],
    })
    adapter = TencentDnsPodAdapter(requester=responder)

    records = await adapter.list_record_sets(TENCENT_ZONE, TENCENT_CREDENTIAL)

    assert records[0].record_name == "*.example.com"
    assert records[0].read_only_reason is None


@pytest.mark.asyncio
async def test_dnspod_uses_only_public_domain_api():
    responder = FakeDnsPodResponder({
        "DescribeDomainList": [{"DomainList": [{"DomainId": 21, "Name": "example.com"}]}],
    })
    adapter = TencentDnsPodAdapter(requester=responder)

    zones = await adapter.discover_public_zones(TENCENT_CREDENTIAL)

    assert zones == [ZoneRef(DomainProvider.TENCENT_DNSPOD, "21", "example.com")]
    assert responder.actions == ["DescribeDomainList"]


@pytest.mark.asyncio
async def test_dnspod_marks_disabled_line_and_mixed_ttl_groups_read_only():
    responder = FakeDnsPodResponder({
        "DescribeRecordList": [{"RecordList": [
            _dnspod_record(10, "192.0.2.1", name="blue", Status="DISABLE"),
            _dnspod_record(11, "192.0.2.2", name="line", Line="境外"),
            _dnspod_record(12, "192.0.2.3", name="mixed", ttl=300),
            _dnspod_record(13, "192.0.2.4", name="mixed", ttl=600),
        ]}],
    })
    adapter = TencentDnsPodAdapter(requester=responder)

    records = await adapter.list_record_sets(TENCENT_ZONE, TENCENT_CREDENTIAL)

    assert [record.read_only_reason for record in records] == [
        "高级路由记录不可编辑或删除",
        "高级路由记录不可编辑或删除",
        "高级路由记录不可编辑或删除",
    ]


@pytest.mark.asyncio
async def test_dnspod_create_replace_delete_use_public_record_api_and_converge():
    before_rows = {"RecordList": [_dnspod_record(10, "192.0.2.1"), _dnspod_record(11, "192.0.2.2")]}
    created_rows = {"RecordList": [_dnspod_record(12, "192.0.2.3"), _dnspod_record(13, "192.0.2.4")]}
    replaced_rows = {"RecordList": [_dnspod_record(14, "192.0.2.5")]}
    responder = FakeDnsPodResponder({
        "DescribeRecordList": [before_rows, created_rows, replaced_rows, replaced_rows, {"RecordList": []}],
        "CreateRecord": [{}, {}, {}, {}, {}],
        "DeleteRecordBatch": [{}, {}],
    })
    adapter = TencentDnsPodAdapter(requester=responder, poll_interval=0)
    before = (await adapter.list_record_sets(TENCENT_ZONE, TENCENT_CREDENTIAL))[0]

    await adapter.create_simple_record_set(
        TENCENT_ZONE, RecordSetDraft("example.com", "A", 300, ("192.0.2.3", "192.0.2.4")), TENCENT_CREDENTIAL,
    )
    await adapter.replace_simple_record_set(
        TENCENT_ZONE, before, RecordSetDraft("example.com", "A", 300, ("192.0.2.5",)), TENCENT_CREDENTIAL,
    )
    replaced = (await adapter.list_record_sets(TENCENT_ZONE, TENCENT_CREDENTIAL))[0]
    await adapter.delete_simple_record_set(TENCENT_ZONE, replaced, TENCENT_CREDENTIAL)

    assert responder.actions == [
        "DescribeRecordList",
        "CreateRecord", "CreateRecord", "DescribeRecordList",
        "DeleteRecordBatch", "CreateRecord", "DescribeRecordList",
        "DescribeRecordList",
        "DeleteRecordBatch", "DescribeRecordList",
    ]
    create_payloads = [payload for action, payload in responder.calls if action == "CreateRecord"]
    assert [payload["Value"] for payload in create_payloads] == ["192.0.2.3", "192.0.2.4", "192.0.2.5"]
    assert all(payload["RecordLine"] == "默认" for payload in create_payloads)
    assert responder.calls[4] == ("DeleteRecordBatch", {"RecordIdList": [10, 11]})
    assert responder.calls[8] == ("DeleteRecordBatch", {"RecordIdList": [14]})


@pytest.mark.asyncio
async def test_dnspod_creates_mx_with_separate_priority():
    responder = FakeDnsPodResponder({
        "CreateRecord": [{}],
        "DescribeRecordList": [{"RecordList": [
            _dnspod_record(12, "mail.example.net", record_type="MX", MX=10),
        ]}],
    })
    adapter = TencentDnsPodAdapter(requester=responder, poll_interval=0)

    await adapter.create_simple_record_set(
        TENCENT_ZONE, RecordSetDraft("example.com", "MX", 300, ("10 mail.example.net",)), TENCENT_CREDENTIAL,
    )

    assert responder.calls[0] == ("CreateRecord", {
        "DomainId": 21,
        "SubDomain": "@",
        "RecordType": "MX",
        "RecordLine": "默认",
        "TTL": 300,
        "MX": 10,
        "Value": "mail.example.net",
    })


@pytest.mark.asyncio
async def test_dnspod_convergence_timeout_does_not_retry_write():
    responder = FakeDnsPodResponder({
        "CreateRecord": [{}],
        "DescribeRecordList": [{"RecordList": []}],
    })
    adapter = TencentDnsPodAdapter(requester=responder, poll_attempts=1, poll_interval=0)

    with pytest.raises(ProviderUnavailableError, match="腾讯云 DNSPod 暂时不可用"):
        await adapter.create_simple_record_set(
            TENCENT_ZONE, RecordSetDraft("example.com", "A", 300, ("192.0.2.1",)), TENCENT_CREDENTIAL,
        )

    assert responder.actions == ["CreateRecord", "DescribeRecordList"]


@pytest.mark.asyncio
async def test_dnspod_partial_known_write_failure_is_sanitized_and_not_retried():
    responder = FakeDnsPodResponder({
        "CreateRecord": [{}, FakeTencentError("AuthFailure")],
    })
    adapter = TencentDnsPodAdapter(requester=responder, poll_interval=0)

    with pytest.raises(ProviderUnavailableError, match="腾讯云 DNSPod 暂时不可用"):
        await adapter.create_simple_record_set(
            TENCENT_ZONE, RecordSetDraft("example.com", "A", 300, ("192.0.2.1", "192.0.2.2")), TENCENT_CREDENTIAL,
        )

    assert responder.actions == ["CreateRecord", "CreateRecord"]


@pytest.mark.asyncio
async def test_dnspod_sanitizes_initial_provider_rejection():
    responder = FakeDnsPodResponder({
        "DescribeDomainList": [FakeTencentError("AuthFailure")],
    })
    adapter = TencentDnsPodAdapter(requester=responder)

    with pytest.raises(ProviderRejectedError, match="腾讯云 DNSPod 拒绝该请求"):
        await adapter.discover_public_zones(TENCENT_CREDENTIAL)


@pytest.mark.asyncio
async def test_google_discovers_public_managed_zones_only():
    public_zone = FakeGoogleZone("public-zone", "example.com.", "public")
    private_zone = FakeGoogleZone("private-zone", "corp.example.", "private")
    client = FakeGoogleDnsClient([public_zone, private_zone])
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: client)

    zones = await adapter.discover_public_zones(GOOGLE_CREDENTIAL)

    assert zones == [GOOGLE_ZONE]


@pytest.mark.asyncio
async def test_google_normalizes_records_and_marks_system_ttl_and_routing_records_read_only():
    zone = FakeGoogleZone("public-zone", "example.com.", "public", records=[
        FakeGoogleRecord("api.example.com.", "A", 300, ["192.0.2.1"]),
        FakeGoogleRecord("example.com.", "NS", 300, ["ns1.example.net."]),
        FakeGoogleRecord("slow.example.com.", "A", 60, ["192.0.2.2"]),
        FakeGoogleRecord(
            "weighted.example.com.", "A", 300, ["192.0.2.3"], routing_policy={"wrr": {"weight": 10}},
        ),
    ])
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: FakeGoogleDnsClient([zone]))

    records = await adapter.list_record_sets(GOOGLE_ZONE, GOOGLE_CREDENTIAL)

    assert [record.record_name for record in records] == [
        "api.example.com", "example.com", "slow.example.com", "weighted.example.com",
    ]
    assert [record.read_only_reason for record in records] == [
        None,
        "NS 记录不可编辑或删除",
        "TTL 不在可编辑范围内",
        "高级路由记录不可编辑或删除",
    ]


@pytest.mark.asyncio
async def test_google_uses_raw_page_routing_policy_metadata_to_protect_existing_record():
    raw_record = {
        "name": "weighted.example.com.",
        "type": "A",
        "ttl": 300,
        "routingPolicy": {
            "wrr": [
                {"weight": 10, "rrdatas": ["192.0.2.3"]},
                {"weight": 20, "rrdatas": ["192.0.2.4"]},
            ],
        },
    }
    zone = FakeGoogleZone(
        "public-zone",
        "example.com.",
        "public",
        records=[FakeGoogleRecord("weighted.example.com.", "A", 300, ["192.0.2.3"])],
        raw_pages=[{"rrsets": [raw_record]}],
    )
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: FakeGoogleDnsClient([zone]))

    records = await adapter.list_record_sets(GOOGLE_ZONE, GOOGLE_CREDENTIAL)

    assert records[0].read_only_reason == "高级路由记录不可编辑或删除"
    assert records[0].provider_meta["advanced_routing"] is True
    assert records[0].provider_meta["locator"] == {"name": "weighted.example.com", "type": "A"}
    assert records[0].values == ("192.0.2.3", "192.0.2.4")


@pytest.mark.asyncio
async def test_google_transport_error_is_sanitized_as_unavailable():
    secret_marker = "service-account-json-should-not-leak"

    def raise_transport_error(credential: ProviderCredential):
        raise google_auth_exceptions.TransportError(secret_marker)

    adapter = GoogleCloudDnsAdapter(client_factory=raise_transport_error)

    with pytest.raises(ProviderUnavailableError, match="Google Cloud DNS 暂时不可用") as exc:
        await adapter.discover_public_zones(GOOGLE_CREDENTIAL)

    assert secret_marker not in str(exc.value)


@pytest.mark.asyncio
async def test_google_create_replace_delete_submit_one_change_and_wait_for_done():
    zone = FakeGoogleZone(
        "public-zone",
        "example.com.",
        "public",
        records=[FakeGoogleRecord("api.example.com.", "A", 300, ["192.0.2.1"])],
        change_statuses=["pending", "done"],
    )
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: FakeGoogleDnsClient([zone]))
    before = (await adapter.list_record_sets(GOOGLE_ZONE, GOOGLE_CREDENTIAL))[0]
    after = RecordSetDraft("api.example.com", "A", 300, ("192.0.2.2",))

    await adapter.create_simple_record_set(
        GOOGLE_ZONE, RecordSetDraft("new.example.com", "A", 300, ("192.0.2.3",)), GOOGLE_CREDENTIAL,
    )
    await adapter.replace_simple_record_set(GOOGLE_ZONE, before, after, GOOGLE_CREDENTIAL)
    await adapter.delete_simple_record_set(
        GOOGLE_ZONE,
        RemoteRecordSet("api-key", "api.example.com", "A", 300, ("192.0.2.2",), None, {}),
        GOOGLE_CREDENTIAL,
    )

    changes = zone.changes_created
    assert [_google_record_fields(record) for record in changes[0].added] == [
        ("new.example.com.", "A", 300, ["192.0.2.3"]),
    ]
    assert [_google_record_fields(record) for record in changes[1].deleted] == [
        ("api.example.com.", "A", 300, ["192.0.2.1"]),
    ]
    assert [_google_record_fields(record) for record in changes[1].added] == [
        ("api.example.com.", "A", 300, ["192.0.2.2"]),
    ]
    assert [_google_record_fields(record) for record in changes[2].deleted] == [
        ("api.example.com.", "A", 300, ["192.0.2.2"]),
    ]
    assert all(change.create_calls == 1 and change.reload_calls == 1 for change in changes)


@pytest.mark.asyncio
async def test_google_writes_absolute_cname_mx_and_srv_targets():
    zone = FakeGoogleZone("public-zone", "example.com.", "public", change_statuses=["pending", "done"])
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: FakeGoogleDnsClient([zone]))

    await adapter.create_simple_record_set(
        GOOGLE_ZONE, RecordSetDraft("www.example.com", "CNAME", 300, ("target.example.net",)), GOOGLE_CREDENTIAL,
    )
    await adapter.create_simple_record_set(
        GOOGLE_ZONE, RecordSetDraft("example.com", "MX", 300, ("10 mail.example.net",)), GOOGLE_CREDENTIAL,
    )
    await adapter.create_simple_record_set(
        GOOGLE_ZONE,
        RecordSetDraft("_sip._tcp.example.com", "SRV", 300, ("10 5 443 target.example.net",)),
        GOOGLE_CREDENTIAL,
    )

    values = [change.added[0].rrdatas[0] for change in zone.changes_created]
    assert values == ["target.example.net.", "10 mail.example.net.", "10 5 443 target.example.net."]


@pytest.mark.asyncio
async def test_google_invalid_service_account_is_rejected_without_a_cloud_call():
    adapter = GoogleCloudDnsAdapter()
    invalid_credential = ProviderCredential(DomainProvider.GOOGLE_CLOUD_DNS, 3, None, "not-json")

    with pytest.raises(ProviderRejectedError, match="Google Cloud DNS 拒绝该请求"):
        await adapter.discover_public_zones(invalid_credential)


@pytest.mark.asyncio
async def test_google_change_poll_timeout_does_not_submit_a_second_change():
    zone = FakeGoogleZone("public-zone", "example.com.", "public", change_statuses=["pending"])
    adapter = GoogleCloudDnsAdapter(
        client_factory=lambda credential: FakeGoogleDnsClient([zone]), poll_attempts=1, poll_interval=0,
    )

    with pytest.raises(ProviderUnavailableError, match="Google Cloud DNS 暂时不可用"):
        await adapter.create_simple_record_set(
            GOOGLE_ZONE, RecordSetDraft("api.example.com", "A", 300, ("192.0.2.1",)), GOOGLE_CREDENTIAL,
        )

    assert zone.changes_created[0].create_calls == 1


@pytest.mark.asyncio
async def test_route53_discovers_only_public_zones_and_normalizes_name():
    client = FakeRoute53Client(hosted_zone_pages=[{
        "HostedZones": [
            {"Id": "/hostedzone/ZPUBLIC", "Name": "Example.COM.", "Config": {"PrivateZone": False}},
            {"Id": "/hostedzone/ZPRIVATE", "Name": "internal.example.", "Config": {"PrivateZone": True}},
        ],
        "IsTruncated": False,
    }])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client)

    zones = await adapter.discover_public_zones(AWS_CREDENTIAL)

    assert zones == [ZoneRef(DomainProvider.AWS_ROUTE53, "ZPUBLIC", "example.com")]


@pytest.mark.asyncio
async def test_route53_pages_hosted_zones_with_next_marker():
    client = FakeRoute53Client(hosted_zone_pages=[
        {
            "HostedZones": [{"Id": "/hostedzone/Z1", "Name": "one.example.", "Config": {"PrivateZone": False}}],
            "IsTruncated": True,
            "NextMarker": "Z1",
        },
        {
            "HostedZones": [{"Id": "/hostedzone/Z2", "Name": "two.example.", "Config": {"PrivateZone": False}}],
            "IsTruncated": False,
        },
    ])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client)

    zones = await adapter.discover_public_zones(AWS_CREDENTIAL)

    assert [zone.remote_zone_id for zone in zones] == ["Z1", "Z2"]
    assert client.hosted_zone_calls == [{}, {"Marker": "Z1"}]


@pytest.mark.asyncio
async def test_route53_lists_existing_wildcard_record():
    client = FakeRoute53Client(record_pages=[{
        "ResourceRecordSets": [
            {"Name": "*.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.1"}]},
        ],
        "IsTruncated": False,
    }])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client)

    records = await adapter.list_record_sets(AWS_ZONE, AWS_CREDENTIAL)

    assert records[0].record_name == "*.example.com"
    assert records[0].read_only_reason is None


@pytest.mark.asyncio
async def test_route53_collects_pages_and_marks_alias_and_advanced_records_read_only():
    client = FakeRoute53Client(record_pages=[
        {
            "ResourceRecordSets": [
                {"Name": "www.example.com.", "Type": "A", "AliasTarget": {"DNSName": "target.example.net."}},
                {
                    "Name": "blue.example.com.", "Type": "A", "TTL": 300,
                    "ResourceRecords": [{"Value": "192.0.2.1"}], "SetIdentifier": "blue", "Weight": 10,
                },
            ],
            "IsTruncated": True,
            "NextRecordName": "next.example.com.",
            "NextRecordType": "A",
        },
        {
            "ResourceRecordSets": [
                {"Name": "next.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.2"}]},
            ],
            "IsTruncated": False,
        },
    ])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client)

    records = await adapter.list_record_sets(AWS_ZONE, AWS_CREDENTIAL)

    assert [record.read_only_reason for record in records] == [
        "Alias 记录不可编辑或删除", "高级路由记录不可编辑或删除", None,
    ]
    assert records[2].record_name == "next.example.com"
    assert client.record_calls[1] == {
        "HostedZoneId": "ZPUBLIC", "StartRecordName": "next.example.com.", "StartRecordType": "A",
    }


@pytest.mark.asyncio
async def test_route53_get_record_set_uses_stable_locator():
    page = {
        "ResourceRecordSets": [
            {"Name": "api.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.1"}]},
            {"Name": "www.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.2"}]},
        ],
        "IsTruncated": False,
    }
    adapter = AwsRoute53Adapter(client_factory=lambda credential: FakeRoute53Client(record_pages=[page, page]))
    records = await adapter.list_record_sets(AWS_ZONE, AWS_CREDENTIAL)

    found = await adapter.get_record_set(AWS_ZONE, records[1].record_key, AWS_CREDENTIAL)

    assert found == records[1]


@pytest.mark.asyncio
async def test_route53_create_replace_delete_submit_one_change_and_wait_for_insync():
    client = FakeRoute53Client(change_statuses=["PENDING", "INSYNC"])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client, poll_interval=0)
    before = await _remote_record(adapter, client, "192.0.2.1")
    after = RecordSetDraft("api.example.com", "A", 300, ("192.0.2.2",))

    await adapter.create_simple_record_set(AWS_ZONE, after, AWS_CREDENTIAL)
    await adapter.replace_simple_record_set(AWS_ZONE, before, after, AWS_CREDENTIAL)
    await adapter.delete_simple_record_set(AWS_ZONE, before, AWS_CREDENTIAL)

    changes = [request["ChangeBatch"]["Changes"][0] for request in client.change_requests]
    assert [change["Action"] for change in changes] == ["CREATE", "UPSERT", "DELETE"]
    assert changes[0]["ResourceRecordSet"] == {
        "Name": "api.example.com.", "Type": "A", "TTL": 300,
        "ResourceRecords": [{"Value": "192.0.2.2"}],
    }
    assert changes[1]["ResourceRecordSet"]["ResourceRecords"] == [{"Value": "192.0.2.2"}]
    assert changes[2]["ResourceRecordSet"]["ResourceRecords"] == [{"Value": "192.0.2.1"}]
    assert client.change_reads == ["/change/C1", "/change/C1", "/change/C1", "/change/C1"]


@pytest.mark.asyncio
async def test_route53_writes_absolute_cname_mx_and_srv_targets():
    client = FakeRoute53Client()
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client, poll_interval=0)

    await adapter.create_simple_record_set(
        AWS_ZONE, RecordSetDraft("www.example.com", "CNAME", 300, ("target.example.net",)), AWS_CREDENTIAL,
    )
    await adapter.create_simple_record_set(
        AWS_ZONE, RecordSetDraft("example.com", "MX", 300, ("10 mail.example.net",)), AWS_CREDENTIAL,
    )
    await adapter.create_simple_record_set(
        AWS_ZONE, RecordSetDraft("_sip._tcp.example.com", "SRV", 300, ("10 5 443 target.example.net",)), AWS_CREDENTIAL,
    )

    values = [
        request["ChangeBatch"]["Changes"][0]["ResourceRecordSet"]["ResourceRecords"][0]["Value"]
        for request in client.change_requests
    ]
    assert values == ["target.example.net.", "10 mail.example.net.", "10 5 443 target.example.net."]


@pytest.mark.asyncio
async def test_route53_sanitizes_provider_rejection_and_unavailability():
    rejected = AwsRoute53Adapter(client_factory=lambda credential: FakeRoute53Client(error=FakeClientError("AccessDenied")))
    unavailable = AwsRoute53Adapter(client_factory=lambda credential: FakeRoute53Client(error=TimeoutError()))

    with pytest.raises(ProviderRejectedError, match="AWS Route 53 拒绝该请求"):
        await rejected.discover_public_zones(AWS_CREDENTIAL)
    with pytest.raises(ProviderUnavailableError, match="AWS Route 53 暂时不可用"):
        await unavailable.discover_public_zones(AWS_CREDENTIAL)


@pytest.mark.asyncio
async def test_route53_change_poll_timeout_returns_sanitized_unavailable_without_retry():
    client = FakeRoute53Client(change_statuses=["PENDING"])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client, poll_attempts=1, poll_interval=0)

    with pytest.raises(ProviderUnavailableError, match="AWS Route 53 暂时不可用"):
        await adapter.create_simple_record_set(
            AWS_ZONE, RecordSetDraft("api.example.com", "A", 300, ("192.0.2.1",)), AWS_CREDENTIAL,
        )

    assert len(client.change_requests) == 1
    assert client.change_reads == ["/change/C1"]


@pytest.mark.asyncio
async def test_route53_waits_for_slow_change_to_reach_insync():
    client = FakeRoute53Client(change_statuses=["PENDING"] * 20 + ["INSYNC"])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client, poll_interval=0)

    await adapter.create_simple_record_set(
        AWS_ZONE, RecordSetDraft("api.example.com", "A", 300, ("192.0.2.1",)), AWS_CREDENTIAL,
    )

    assert client.change_reads == ["/change/C1"] * 21


async def _remote_record(adapter: AwsRoute53Adapter, client: FakeRoute53Client, value: str):
    client.record_pages = [{
        "ResourceRecordSets": [
            {"Name": "api.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": value}]},
        ],
        "IsTruncated": False,
    }]
    return (await adapter.list_record_sets(AWS_ZONE, AWS_CREDENTIAL))[0]


def _google_record_fields(record: FakeGoogleRecord) -> tuple[str, str, int, list[str]]:
    return record.name, record.record_type, record.ttl, record.rrdatas
