"""DNS 服务商统一记录模型与规范化规则回归测试。"""
import subprocess
import sys

import pytest

from app.core.constants import DomainProvider
from app.dns_providers.base import (
    MAX_EDITABLE_TTL,
    MIN_EDITABLE_TTL,
    WRITABLE_RECORD_TYPES,
    ProviderCredential,
    RecordSetDraft,
    RemoteRecordSet,
    ZoneRef,
    decode_record_key,
    encode_record_key,
    normalize_owner_name,
    normalize_record_values,
    normalize_zone_name,
    read_only_reason,
)


def test_provider_neutral_value_objects_preserve_contract_fields():
    credential = ProviderCredential(DomainProvider.AWS_ROUTE53, 7, "AKIA", "secret")
    zone = ZoneRef(DomainProvider.AWS_ROUTE53, "Z123", "example.com")
    record = RemoteRecordSet("key", "www.example.com", "A", 300, ("192.0.2.1",), None, {})
    draft = RecordSetDraft("www.example.com", "A", 300, ("192.0.2.2",))

    assert credential.credential_id == 7
    assert zone.remote_zone_id == "Z123"
    assert record.values == ("192.0.2.1",)
    assert draft.ttl == 300


def test_record_key_codec_is_deterministic_and_rejects_invalid_payloads():
    locator = {"type": "A", "name": "www.example.com", "identifier": "blue"}

    encoded = encode_record_key(locator)

    assert encoded == encode_record_key({"identifier": "blue", "name": "www.example.com", "type": "A"})
    assert decode_record_key(encoded) == locator
    with pytest.raises(ValueError):
        decode_record_key("not-a-valid-key")
    with pytest.raises(ValueError):
        decode_record_key("W10")
    with pytest.raises(ValueError):
        encode_record_key(["not", "an", "object"])
    with pytest.raises(ValueError, match="定位键过长"):
        encode_record_key({"value": "x" * 500})


def test_zone_and_owner_normalization_accepts_at_relative_and_fqdn():
    assert normalize_zone_name("Example.COM.") == "example.com"
    assert normalize_owner_name("@", "example.com") == "example.com"
    assert normalize_owner_name("api", "example.com") == "api.example.com"
    assert normalize_owner_name("api.example.com.", "example.com") == "api.example.com"
    assert normalize_owner_name("API", "Example.COM.") == "api.example.com"
    assert normalize_owner_name("_sip._tcp", "example.com") == "_sip._tcp.example.com"


def test_owner_normalization_accepts_only_leftmost_wildcard_label():
    assert normalize_owner_name("*", "example.com") == "*.example.com"
    assert normalize_owner_name("*.api", "example.com") == "*.api.example.com"
    assert normalize_owner_name("*.example.com.", "example.com") == "*.example.com"


@pytest.mark.parametrize("owner", [
    "", ".", "bad..example.com", "api.other.example.", "example.net.", "api.*.example.com",
])
def test_owner_outside_zone_or_malformed_is_rejected(owner):
    with pytest.raises(ValueError):
        normalize_owner_name(owner, "example.com")


@pytest.mark.parametrize("zone", ["", ".", "bad..example.com", "-bad.example", "bad-.example"])
def test_malformed_zone_is_rejected(zone):
    with pytest.raises(ValueError):
        normalize_zone_name(zone)


def test_record_value_normalization_supports_all_editable_record_types():
    assert normalize_record_values("A", ("192.0.2.1",)) == ("192.0.2.1",)
    assert normalize_record_values("AAAA", ("2001:db8::1",)) == ("2001:db8::1",)
    assert normalize_record_values("CNAME", ("target.example.net.",)) == ("target.example.net",)
    assert normalize_record_values("MX", ("10 mail.example.net.",)) == ("10 mail.example.net",)
    assert normalize_record_values("SRV", ("10 5 443 target.example.net.",)) == ("10 5 443 target.example.net",)
    assert normalize_record_values("TXT", (" keep raw spaces ",)) == (" keep raw spaces ",)
    assert normalize_record_values("CAA", ("0 issue letsencrypt.org",)) == ("0 issue letsencrypt.org",)


@pytest.mark.parametrize("record_type,values", [
    ("A", ("999.0.0.1",)),
    ("AAAA", ("192.0.2.1",)),
    ("CNAME", ("a.example.com", "b.example.com")),
    ("CNAME", ("bad..example.com",)),
    ("MX", ("65536 mail.example.net",)),
    ("MX", ("10",)),
    ("SRV", ("10 5 70000 target.example.com",)),
    ("SRV", ("10 5 443",)),
    ("TXT", ("",)),
    ("TXT", ("has\x00nul",)),
    ("CAA", ("256 issue letsencrypt.org",)),
    ("CAA", ("0 invalid_tag! letsencrypt.org",)),
    ("PTR", ("target.example.com",)),
])
def test_invalid_record_values_are_rejected(record_type, values):
    with pytest.raises(ValueError):
        normalize_record_values(record_type, values)


@pytest.mark.parametrize("record_type,values", [
    ("A", ("192.0.2.1", "192.0.2.1")),
    ("MX", ("10 mail.example.net.", "10 mail.example.net")),
    ("TXT", ("same", "same")),
])
def test_duplicate_canonical_record_values_are_rejected(record_type, values):
    with pytest.raises(ValueError):
        normalize_record_values(record_type, values)


def test_read_only_rules_cover_system_advanced_and_ttl_boundaries():
    assert read_only_reason("SOA", 300, {}) == "SOA 记录不可编辑或删除"
    assert read_only_reason("NS", 300, {}) == "NS 记录不可编辑或删除"
    assert read_only_reason("A", None, {"alias": True}) == "Alias 记录不可编辑或删除"
    assert read_only_reason("A", 300, {"advanced_routing": True}) == "高级路由记录不可编辑或删除"
    assert read_only_reason("PTR", 300, {}) == "该记录类型暂不支持编辑或删除"
    assert read_only_reason("TXT", 120, {}) == "TTL 不在可编辑范围内"
    assert read_only_reason("TXT", None, {}) == "TTL 不在可编辑范围内"
    assert read_only_reason("CAA", 300, {}) is None
    assert read_only_reason("A", MIN_EDITABLE_TTL, {}) is None
    assert read_only_reason("A", MAX_EDITABLE_TTL, {}) is None


def test_contract_import_uses_no_provider_sdk_client():
    assert WRITABLE_RECORD_TYPES == frozenset({"A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV"})
    assert MIN_EDITABLE_TTL == 300
    assert MAX_EDITABLE_TTL == 86400
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import app.dns_providers.base; "
            "assert 'boto3' not in sys.modules; "
            "assert 'tencentcloud' not in sys.modules; "
            "assert 'google.cloud.dns' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
