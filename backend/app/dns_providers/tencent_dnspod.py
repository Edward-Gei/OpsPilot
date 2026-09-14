"""腾讯云 DNSPod 公网 DNS 适配器。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.core.constants import DomainProvider
from app.dns_providers.base import (
    DnsProviderAdapter,
    ProviderCredential,
    ProviderRejectedError,
    ProviderUnavailableError,
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


_PAGE_SIZE = 100
_REJECTED_ERROR_MARKERS = (
    "auth", "unauthorized", "forbidden", "permission", "invalid", "notauthorized",
)
_DEFAULT_RECORD_LINES = frozenset({"", "0", "default", "默认"})
_ADVANCED_RECORD_FIELDS = (
    "Monitor", "MonitorStatus", "Weight", "GroupId", "UseAlias", "AliasTarget", "Remark",
)


def _dnspod_request(
    action: str, payload: dict[str, object], credential: ProviderCredential,
) -> dict[str, object]:
    """用官方 SDK 请求 DNSPod，凭据只在当前调用栈内使用。"""
    from tencentcloud.common import credential as tencent_credential
    from tencentcloud.dnspod.v20210323 import dnspod_client, models

    if not credential.login_user or not credential.secret:
        raise ProviderRejectedError("腾讯云 DNSPod 拒绝该请求")
    client = dnspod_client.DnspodClient(
        tencent_credential.Credential(credential.login_user, credential.secret), "",
    )
    request = getattr(models, f"{action}Request")()
    request.from_json_string(json.dumps(payload))
    response = getattr(client, action)(request)
    body = json.loads(response.to_json_string())
    if not isinstance(body, dict):
        raise TypeError("DNSPod 响应格式无效")
    return body


def _dnspod_error(exc: Exception) -> ProviderRejectedError | ProviderUnavailableError:
    if isinstance(exc, (ProviderRejectedError, ProviderUnavailableError)):
        return exc
    get_code = getattr(exc, "get_code", None)
    code = str(get_code()).lower() if callable(get_code) else ""
    if any(marker in code for marker in _REJECTED_ERROR_MARKERS):
        return ProviderRejectedError("腾讯云 DNSPod 拒绝该请求")
    return ProviderUnavailableError("腾讯云 DNSPod 暂时不可用")


class TencentDnsPodAdapter(DnsProviderAdapter):
    """使用 DNSPod 公网 API 管理简单路由记录集。"""

    def __init__(
        self,
        requester: Callable[[str, dict[str, object], ProviderCredential], dict[str, object]] | None = None,
        *,
        poll_attempts: int = 20,
        poll_interval: float = 0.2,
    ):
        self._requester = requester or _dnspod_request
        self._poll_attempts = max(1, poll_attempts)
        self._poll_interval = max(0, poll_interval)

    async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
        try:
            offset = 0
            zones: list[ZoneRef] = []
            while True:
                body = await self._request(
                    "DescribeDomainList", {"Offset": offset, "Limit": _PAGE_SIZE}, credential,
                )
                rows = self._rows(body, "DomainList")
                zones.extend(
                    ZoneRef(
                        DomainProvider.TENCENT_DNSPOD,
                        str(row["DomainId"]),
                        normalize_zone_name(str(row["Name"])),
                    )
                    for row in rows
                )
                if len(rows) < _PAGE_SIZE:
                    return zones
                offset += len(rows)
        except Exception as exc:
            raise _dnspod_error(exc) from None

    async def list_record_sets(self, zone: ZoneRef, credential: ProviderCredential) -> list[RemoteRecordSet]:
        try:
            rows = await self._list_record_rows(zone, credential)
            grouped: dict[tuple[str, str], list[Mapping[str, object]]] = {}
            for row in rows:
                record_name = normalize_owner_name(str(row["Name"]), zone.zone_name)
                record_type = str(row["Type"]).upper()
                grouped.setdefault((record_name, record_type), []).append(row)
            return [self._to_remote_record_set(name, record_type, records) for (name, record_type), records in grouped.items()]
        except Exception as exc:
            raise _dnspod_error(exc) from None

    async def get_record_set(
        self, zone: ZoneRef, record_key: str, credential: ProviderCredential,
    ) -> RemoteRecordSet | None:
        locator = decode_record_key(record_key)
        expected_key = encode_record_key(locator)
        records = await self.list_record_sets(zone, credential)
        return next((record for record in records if record.record_key == expected_key), None)

    async def create_simple_record_set(
        self, zone: ZoneRef, record: RecordSetDraft, credential: ProviderCredential,
    ) -> None:
        writes = 0
        try:
            for value in record.values:
                await self._request("CreateRecord", self._create_payload(zone, record, value), credential)
                writes += 1
            await self._wait_for_draft(zone, record, credential)
        except Exception as exc:
            raise self._write_error(exc, writes) from None

    async def replace_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, after: RecordSetDraft, credential: ProviderCredential,
    ) -> None:
        writes = 0
        try:
            await self._delete_record_ids(self._record_ids(before), credential)
            writes += 1
            for value in after.values:
                await self._request("CreateRecord", self._create_payload(zone, after, value), credential)
                writes += 1
            await self._wait_for_draft(zone, after, credential)
        except Exception as exc:
            raise self._write_error(exc, writes) from None

    async def delete_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, credential: ProviderCredential,
    ) -> None:
        writes = 0
        try:
            await self._delete_record_ids(self._record_ids(before), credential)
            writes += 1
            await self._wait_for_deletion(zone, before, credential)
        except Exception as exc:
            raise self._write_error(exc, writes) from None

    async def _request(
        self, action: str, payload: dict[str, object], credential: ProviderCredential,
    ) -> Mapping[str, object]:
        body = await asyncio.to_thread(self._requester, action, payload, credential)
        if not isinstance(body, Mapping):
            raise TypeError("DNSPod 响应格式无效")
        return body

    async def _list_record_rows(
        self, zone: ZoneRef, credential: ProviderCredential,
    ) -> list[Mapping[str, object]]:
        offset = 0
        records: list[Mapping[str, object]] = []
        while True:
            body = await self._request(
                "DescribeRecordList",
                {"DomainId": self._domain_id(zone), "Offset": offset, "Limit": _PAGE_SIZE},
                credential,
            )
            rows = self._rows(body, "RecordList")
            records.extend(rows)
            if len(rows) < _PAGE_SIZE:
                return records
            offset += len(rows)

    def _to_remote_record_set(
        self, record_name: str, record_type: str, rows: Sequence[Mapping[str, object]],
    ) -> RemoteRecordSet:
        record_ids = sorted(self._record_id(row) for row in rows)
        ttls = {self._ttl(row) for row in rows}
        ttl = next(iter(ttls)) if len(ttls) == 1 else None
        raw_values = tuple(self._record_value(row, record_type) for row in rows)
        advanced = len(ttls) != 1 or any(self._is_advanced_row(row) for row in rows)
        try:
            values = normalize_record_values(record_type, raw_values)
        except ValueError:
            values = raw_values
            advanced = True

        locator: dict[str, object] = {
            "name": record_name,
            "record_ids": record_ids,
            "type": record_type,
        }
        provider_meta: dict[str, object] = {"locator": locator, "record_ids": record_ids}
        if advanced:
            provider_meta["advanced_routing"] = True
        return RemoteRecordSet(
            record_key=encode_record_key(locator),
            record_name=record_name,
            record_type=record_type,
            ttl=ttl,
            values=values,
            read_only_reason=read_only_reason(record_type, ttl, provider_meta),
            provider_meta=provider_meta,
        )

    async def _delete_record_ids(self, record_ids: list[int], credential: ProviderCredential) -> None:
        await self._request("DeleteRecordBatch", {"RecordIdList": record_ids}, credential)

    async def _wait_for_draft(
        self, zone: ZoneRef, draft: RecordSetDraft, credential: ProviderCredential,
    ) -> None:
        for attempt in range(self._poll_attempts):
            records = await self.list_record_sets(zone, credential)
            if any(self._matches_draft(record, draft) for record in records):
                return
            if attempt < self._poll_attempts - 1 and self._poll_interval:
                await asyncio.sleep(self._poll_interval)
        raise TimeoutError("DNSPod 记录变更未在限定时间内完成")

    async def _wait_for_deletion(
        self, zone: ZoneRef, before: RemoteRecordSet, credential: ProviderCredential,
    ) -> None:
        for attempt in range(self._poll_attempts):
            records = await self.list_record_sets(zone, credential)
            if not any(
                record.record_name == before.record_name and record.record_type == before.record_type
                for record in records
            ):
                return
            if attempt < self._poll_attempts - 1 and self._poll_interval:
                await asyncio.sleep(self._poll_interval)
        raise TimeoutError("DNSPod 记录删除未在限定时间内完成")

    @staticmethod
    def _rows(body: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
        rows = body.get(field, [])
        if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
            raise TypeError("DNSPod 响应格式无效")
        return rows

    @staticmethod
    def _domain_id(zone: ZoneRef) -> int:
        domain_id = int(zone.remote_zone_id)
        if domain_id <= 0:
            raise ValueError("DNSPod 域名标识无效")
        return domain_id

    @staticmethod
    def _record_id(row: Mapping[str, object]) -> int:
        record_id = int(str(row["RecordId"]))
        if record_id <= 0:
            raise ValueError("DNSPod 记录标识无效")
        return record_id

    @staticmethod
    def _ttl(row: Mapping[str, object]) -> int | None:
        value = row.get("TTL")
        if isinstance(value, bool) or value is None:
            return None
        try:
            return int(str(value))
        except ValueError:
            return None

    @staticmethod
    def _record_value(row: Mapping[str, object], record_type: str) -> str:
        value = str(row.get("Value", ""))
        if record_type != "MX":
            return value
        priority = row.get("MX")
        return f"{priority} {value}" if priority is not None else value

    @staticmethod
    def _is_advanced_row(row: Mapping[str, object]) -> bool:
        if str(row.get("Status", "")).upper() != "ENABLE":
            return True
        line = row.get("Line", row.get("RecordLine"))
        line_id = row.get("LineId", row.get("RecordLineId"))
        if str(line).strip().lower() not in _DEFAULT_RECORD_LINES or str(line_id or "").strip() not in {"", "0"}:
            return True
        return any(TencentDnsPodAdapter._is_non_default(row.get(field)) for field in _ADVANCED_RECORD_FIELDS)

    @staticmethod
    def _is_non_default(value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "none", "null", "default", "默认"}
        return bool(value)

    @staticmethod
    def _record_ids(record: RemoteRecordSet) -> list[int]:
        record_ids = record.provider_meta.get("record_ids")
        if isinstance(record_ids, (str, bytes)) or not isinstance(record_ids, Sequence):
            raise ValueError("DNSPod 记录定位信息无效")
        ids = sorted(int(record_id) for record_id in record_ids)
        if not ids or any(record_id <= 0 for record_id in ids):
            raise ValueError("DNSPod 记录定位信息无效")
        return ids

    @staticmethod
    def _create_payload(zone: ZoneRef, record: RecordSetDraft, value: str) -> dict[str, object]:
        record_name = normalize_owner_name(record.record_name, zone.zone_name)
        zone_name = normalize_zone_name(zone.zone_name)
        subdomain = "@" if record_name == zone_name else record_name[:-(len(zone_name) + 1)]
        payload: dict[str, object] = {
            "DomainId": TencentDnsPodAdapter._domain_id(zone),
            "SubDomain": subdomain,
            "RecordType": record.record_type.upper(),
            "RecordLine": "默认",
            "TTL": record.ttl,
            "Value": value,
        }
        if record.record_type.upper() == "MX":
            priority, target = value.split(" ", 1)
            payload["MX"] = int(priority)
            payload["Value"] = target
        return payload

    @staticmethod
    def _matches_draft(record: RemoteRecordSet, draft: RecordSetDraft) -> bool:
        return (
            record.record_name == draft.record_name
            and record.record_type == draft.record_type
            and record.ttl == draft.ttl
            and set(record.values) == set(draft.values)
        )

    @staticmethod
    def _write_error(exc: Exception, writes: int) -> ProviderRejectedError | ProviderUnavailableError:
        if writes:
            return ProviderUnavailableError("腾讯云 DNSPod 暂时不可用")
        return _dnspod_error(exc)
