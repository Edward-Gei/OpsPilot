"""DNS 服务商适配器的离线回归测试。"""
import copy

import pytest

from app.core.constants import DomainProvider
from app.dns_providers.aws_route53 import AwsRoute53Adapter
from app.dns_providers.base import (
    ProviderCredential,
    ProviderRejectedError,
    ProviderUnavailableError,
    RecordSetDraft,
    ZoneRef,
)
from app.dns_providers.tencent_dnspod import TencentDnsPodAdapter


AWS_CREDENTIAL = ProviderCredential(DomainProvider.AWS_ROUTE53, 1, "AKIA_TEST", "secret")
AWS_ZONE = ZoneRef(DomainProvider.AWS_ROUTE53, "ZPUBLIC", "example.com")
TENCENT_CREDENTIAL = ProviderCredential(DomainProvider.TENCENT_DNSPOD, 2, "SECRET_ID", "secret")
TENCENT_ZONE = ZoneRef(DomainProvider.TENCENT_DNSPOD, "21", "example.com")


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


async def _remote_record(adapter: AwsRoute53Adapter, client: FakeRoute53Client, value: str):
    client.record_pages = [{
        "ResourceRecordSets": [
            {"Name": "api.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": value}]},
        ],
        "IsTruncated": False,
    }]
    return (await adapter.list_record_sets(AWS_ZONE, AWS_CREDENTIAL))[0]
