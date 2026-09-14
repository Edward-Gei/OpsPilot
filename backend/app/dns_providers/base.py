"""DNS 服务商适配器的统一契约与记录规范化。"""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.constants import DomainProvider


WRITABLE_RECORD_TYPES = frozenset({"A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV"})
MIN_EDITABLE_TTL = 300
MAX_EDITABLE_TTL = 86400

_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)
_OWNER_LABEL_RE = re.compile(r"[_a-z0-9](?:[_a-z0-9-]{0,61}[_a-z0-9])?", re.ASCII)
_CAA_TAG_RE = re.compile(r"[a-z0-9-]{1,15}", re.ASCII)


class ProviderRejectedError(Exception):
    """服务商已明确拒绝认证、权限或参数请求。"""


class ProviderUnavailableError(Exception):
    """服务商暂时不可用，调用方可按操作语义处理。"""


@dataclass(frozen=True)
class ProviderCredential:
    provider: DomainProvider
    credential_id: int
    login_user: str | None
    secret: str


@dataclass(frozen=True)
class ZoneRef:
    provider: DomainProvider
    remote_zone_id: str
    zone_name: str


@dataclass(frozen=True)
class RemoteRecordSet:
    record_key: str
    record_name: str
    record_type: str
    ttl: int | None
    values: Sequence[str]
    read_only_reason: str | None
    provider_meta: dict[str, object]


@dataclass(frozen=True)
class RecordSetDraft:
    record_name: str
    record_type: str
    ttl: int
    values: Sequence[str]


class DnsProviderAdapter(Protocol):
    async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]: ...

    async def list_record_sets(self, zone: ZoneRef, credential: ProviderCredential) -> list[RemoteRecordSet]: ...

    async def get_record_set(
        self, zone: ZoneRef, record_key: str, credential: ProviderCredential,
    ) -> RemoteRecordSet | None: ...

    async def create_simple_record_set(
        self, zone: ZoneRef, record: RecordSetDraft, credential: ProviderCredential,
    ) -> None: ...

    async def replace_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, after: RecordSetDraft, credential: ProviderCredential,
    ) -> None: ...

    async def delete_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, credential: ProviderCredential,
    ) -> None: ...


def encode_record_key(locator: dict[str, object]) -> str:
    """将服务商记录定位信息编码成数据库可保存的稳定不透明键。"""
    if not isinstance(locator, dict):
        raise ValueError("服务商记录定位键无效")
    try:
        raw = json.dumps(locator, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError("服务商记录定位键无效") from exc
    key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if len(key) > 512:
        raise ValueError("服务商记录定位键过长")
    return key


def decode_record_key(record_key: str) -> dict[str, object]:
    """还原适配器生成的记录定位键，拒绝非对象或损坏输入。"""
    if not isinstance(record_key, str) or not record_key or len(record_key) > 512:
        raise ValueError("服务商记录定位键无效")
    try:
        padding = "=" * (-len(record_key) % 4)
        raw = base64.b64decode(record_key + padding, altchars=b"-_", validate=True)
        locator = json.loads(raw.decode("ascii"))
    except (ValueError, UnicodeDecodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("服务商记录定位键无效") from exc
    if not isinstance(locator, dict):
        raise ValueError("服务商记录定位键无效")
    return locator


def _normalize_dns_name(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field}无效")
    name = value.strip().rstrip(".").lower()
    if not name or len(name) > 253:
        raise ValueError(f"{field}无效")
    labels = name.split(".")
    if any(not _DNS_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError(f"{field}无效")
    return name


def normalize_zone_name(zone_name: str) -> str:
    return _normalize_dns_name(zone_name, "Zone 名称")


def _normalize_owner_dns_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("记录所有权名称无效")
    name = value.strip().rstrip(".").lower()
    if not name or len(name) > 253:
        raise ValueError("记录所有权名称无效")
    if any(not _OWNER_LABEL_RE.fullmatch(label) for label in name.split(".")):
        raise ValueError("记录所有权名称无效")
    return name


def normalize_owner_name(owner: str, zone_name: str) -> str:
    """将 `@`、相对名称与 Zone 内完整名称统一为完整所有权名称。"""
    if not isinstance(owner, str):
        raise ValueError("记录所有权名称无效")
    raw_owner = owner.strip()
    is_fqdn = raw_owner.endswith(".")
    name = raw_owner.rstrip(".").lower()
    zone = normalize_zone_name(zone_name)
    if name == "@":
        return zone
    if not name:
        raise ValueError("记录所有权名称无效")
    if name == zone or name.endswith(f".{zone}"):
        full_name = name
    elif is_fqdn:
        raise ValueError("记录所有权名称必须位于当前 Zone 内")
    else:
        full_name = f"{name}.{zone}"
    return _normalize_owner_dns_name(full_name)


def _normalize_target(value: str) -> str:
    return _normalize_dns_name(value, "记录目标")


def _bounded_number(value: str, maximum: int, field: str) -> int:
    if not value.isdecimal():
        raise ValueError(f"{field}无效")
    number = int(value)
    if number > maximum:
        raise ValueError(f"{field}无效")
    return number


def _check_values(values: Sequence[str]) -> None:
    if isinstance(values, (str, bytes)) or not values:
        raise ValueError("记录值不能为空")
    if any(not isinstance(value, str) for value in values):
        raise ValueError("记录值无效")


def normalize_record_values(record_type: str, values: Sequence[str]) -> tuple[str, ...]:
    """按 DNS 类型校验并规范化记录值，保留 TXT 的原始非空行。"""
    if not isinstance(record_type, str):
        raise ValueError("记录类型暂不支持")
    normalized_type = record_type.upper()
    if normalized_type not in WRITABLE_RECORD_TYPES:
        raise ValueError("记录类型暂不支持")
    _check_values(values)
    if normalized_type == "CNAME" and len(values) != 1:
        raise ValueError("CNAME 记录只能填写一个值")

    normalized: list[str] = []
    for value in values:
        if normalized_type == "A":
            try:
                canonical = str(ipaddress.IPv4Address(value.strip()))
            except ipaddress.AddressValueError as exc:
                raise ValueError("A 记录值必须是 IPv4 地址") from exc
        elif normalized_type == "AAAA":
            try:
                canonical = str(ipaddress.IPv6Address(value.strip()))
            except ipaddress.AddressValueError as exc:
                raise ValueError("AAAA 记录值必须是 IPv6 地址") from exc
        elif normalized_type == "CNAME":
            canonical = _normalize_target(value)
        elif normalized_type == "MX":
            parts = value.strip().split()
            if len(parts) != 2:
                raise ValueError("MX 记录值格式无效")
            priority = _bounded_number(parts[0], 65535, "MX 优先级")
            canonical = f"{priority} {_normalize_target(parts[1])}"
        elif normalized_type == "SRV":
            parts = value.strip().split()
            if len(parts) != 4:
                raise ValueError("SRV 记录值格式无效")
            priority = _bounded_number(parts[0], 65535, "SRV 优先级")
            weight = _bounded_number(parts[1], 65535, "SRV 权重")
            port = _bounded_number(parts[2], 65535, "SRV 端口")
            canonical = f"{priority} {weight} {port} {_normalize_target(parts[3])}"
        elif normalized_type == "TXT":
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError("TXT 记录值无效") from exc
            if not value or "\x00" in value:
                raise ValueError("TXT 记录值无效")
            canonical = value
        else:
            parts = value.strip().split(maxsplit=2)
            if len(parts) != 3:
                raise ValueError("CAA 记录值格式无效")
            flags = _bounded_number(parts[0], 255, "CAA flags")
            tag = parts[1].lower()
            try:
                caa_value = parts[2].encode("utf-8").decode("utf-8")
            except UnicodeError as exc:
                raise ValueError("CAA 记录值无效") from exc
            if not _CAA_TAG_RE.fullmatch(tag) or not caa_value or "\x00" in caa_value:
                raise ValueError("CAA 记录值无效")
            canonical = f"{flags} {tag} {caa_value}"
        normalized.append(canonical)

    if len(normalized) != len(set(normalized)):
        raise ValueError("记录值不能重复")
    return tuple(normalized)


def read_only_reason(record_type: str, ttl: int | None, provider_meta: Mapping[str, object] | None) -> str | None:
    """统一界定系统、高级路由和不可编辑 TTL 的快照展示语义。"""
    normalized_type = record_type.upper()
    meta = provider_meta or {}
    if normalized_type == "SOA":
        return "SOA 记录不可编辑或删除"
    if normalized_type == "NS":
        return "NS 记录不可编辑或删除"
    if any(meta.get(key) for key in ("alias", "is_alias", "alias_target")):
        return "Alias 记录不可编辑或删除"
    if any(meta.get(key) for key in (
        "advanced_routing", "is_advanced", "set_identifier", "failover", "geo_location", "region",
        "multi_value_answer", "cidr_routing", "line", "monitor", "weight",
    )):
        return "高级路由记录不可编辑或删除"
    if normalized_type not in WRITABLE_RECORD_TYPES:
        return "该记录类型暂不支持编辑或删除"
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not MIN_EDITABLE_TTL <= ttl <= MAX_EDITABLE_TTL:
        return "TTL 不在可编辑范围内"
    return None
