"""Google Cloud DNS 公网 DNS 适配器。"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

from google.auth import exceptions as auth_exceptions
from google.cloud import dns
from google.oauth2 import service_account

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


_REJECTED_STATUS_CODES = frozenset({400, 401, 403, 404})


def _google_client(credential: ProviderCredential):
    """仅在内存中解析 Service Account JSON，禁止落地临时凭据文件。"""
    info = json.loads(credential.secret)
    if not isinstance(info, dict) or not isinstance(info.get("project_id"), str) or not info["project_id"]:
        raise ValueError("Google Cloud DNS 凭据无效")
    credentials = service_account.Credentials.from_service_account_info(info)
    return dns.Client(project=info["project_id"], credentials=credentials)


def _google_error(exc: Exception) -> ProviderRejectedError | ProviderUnavailableError:
    if isinstance(exc, (ProviderRejectedError, ProviderUnavailableError)):
        return exc
    if isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError, KeyError, auth_exceptions.GoogleAuthError)):
        return ProviderRejectedError("Google Cloud DNS 拒绝该请求")
    status = getattr(exc, "code", None)
    status = status() if callable(status) else status
    status = getattr(status, "value", status)
    try:
        status_code = int(status)
    except (TypeError, ValueError):
        status_code = None
    if status_code in _REJECTED_STATUS_CODES:
        return ProviderRejectedError("Google Cloud DNS 拒绝该请求")
    return ProviderUnavailableError("Google Cloud DNS 暂时不可用")


class GoogleCloudDnsAdapter(DnsProviderAdapter):
    """使用 Google Cloud DNS Managed Zone API 管理简单记录集。"""

    def __init__(
        self,
        client_factory: Callable[[ProviderCredential], Any] | None = None,
        *,
        poll_attempts: int = 20,
        poll_interval: float = 0.2,
    ):
        self._client_factory = client_factory or _google_client
        self._poll_attempts = max(1, poll_attempts)
        self._poll_interval = max(0, poll_interval)

    async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
        try:
            client = await asyncio.to_thread(self._client_factory, credential)
            zones = await asyncio.to_thread(lambda: list(client.list_zones()))
            return [
                ZoneRef(DomainProvider.GOOGLE_CLOUD_DNS, str(zone.name), normalize_zone_name(str(zone.dns_name)))
                for zone in zones
                if str(getattr(zone, "visibility", "")).lower() == "public"
            ]
        except Exception as exc:
            raise _google_error(exc) from None

    async def list_record_sets(self, zone: ZoneRef, credential: ProviderCredential) -> list[RemoteRecordSet]:
        try:
            managed_zone = await self._managed_zone(zone, credential)
            records = await asyncio.to_thread(self._list_record_resources, managed_zone)
            return [self._to_remote_record_set(record, zone) for record in records]
        except Exception as exc:
            raise _google_error(exc) from None

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
        await self._submit_change(zone, credential, additions=(record,))

    async def replace_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, after: RecordSetDraft, credential: ProviderCredential,
    ) -> None:
        await self._submit_change(zone, credential, deletions=(before,), additions=(after,))

    async def delete_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, credential: ProviderCredential,
    ) -> None:
        await self._submit_change(zone, credential, deletions=(before,))

    async def _managed_zone(self, zone: ZoneRef, credential: ProviderCredential):
        client = await asyncio.to_thread(self._client_factory, credential)
        return await asyncio.to_thread(client.zone, zone.remote_zone_id)

    def _to_remote_record_set(self, resource: Any, zone: ZoneRef) -> RemoteRecordSet:
        if isinstance(resource, Mapping):
            record_name = normalize_owner_name(str(resource["name"]), zone.zone_name)
            record_type = str(resource["type"]).upper()
            ttl = int(resource["ttl"]) if resource.get("ttl") is not None else None
            rrdatas = resource.get("rrdatas", ())
            if isinstance(rrdatas, (str, bytes)) or not isinstance(rrdatas, (list, tuple)):
                raise TypeError("Google Cloud DNS 记录格式无效")
            raw_values = tuple(str(value) for value in rrdatas)
        else:
            record_name = normalize_owner_name(str(resource.name), zone.zone_name)
            record_type = str(resource.record_type).upper()
            ttl = int(resource.ttl) if resource.ttl is not None else None
            raw_values = tuple(str(value) for value in resource.rrdatas)
        routing_policy = self._routing_policy_locator(resource)
        advanced = bool(routing_policy)
        locator: dict[str, object] = {"name": record_name, "type": record_type}
        if routing_policy:
            locator["routing_policy"] = routing_policy
        provider_meta: dict[str, object] = {"locator": locator}
        if advanced:
            provider_meta["advanced_routing"] = True
        try:
            values = normalize_record_values(record_type, raw_values)
        except ValueError:
            values = raw_values
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

    @staticmethod
    def _list_record_resources(managed_zone: Any) -> list[Any]:
        iterator = managed_zone.list_resource_record_sets()
        pages = getattr(iterator, "pages", None)
        if pages is None:
            return list(iterator)
        records: list[Any] = []
        for page in pages:
            raw_page = getattr(page, "raw_page", None)
            raw_records = raw_page.get("rrsets") if isinstance(raw_page, Mapping) else None
            if isinstance(raw_records, list):
                records.extend(raw_records)
            else:
                records.extend(page)
        return records

    async def _submit_change(
        self,
        zone: ZoneRef,
        credential: ProviderCredential,
        *,
        deletions: tuple[RemoteRecordSet, ...] = (),
        additions: tuple[RecordSetDraft, ...] = (),
    ) -> None:
        submitted = False
        try:
            managed_zone = await self._managed_zone(zone, credential)
            change = await asyncio.to_thread(managed_zone.changes)
            for record in deletions:
                remote_record = await asyncio.to_thread(self._to_google_record_set, managed_zone, record)
                await asyncio.to_thread(change.delete_record_set, remote_record)
            for record in additions:
                remote_record = await asyncio.to_thread(self._to_google_record_set, managed_zone, record)
                await asyncio.to_thread(change.add_record_set, remote_record)
            await asyncio.to_thread(change.create)
            submitted = True
            await self._wait_for_done(change)
        except Exception as exc:
            if submitted:
                raise ProviderUnavailableError("Google Cloud DNS 暂时不可用") from None
            raise _google_error(exc) from None

    async def _wait_for_done(self, change: Any) -> None:
        for attempt in range(self._poll_attempts):
            await asyncio.to_thread(change.reload)
            if str(getattr(change, "status", "")).lower() == "done":
                return
            if attempt < self._poll_attempts - 1 and self._poll_interval:
                await asyncio.sleep(self._poll_interval)
        raise TimeoutError("Google Cloud DNS 变更未在限定时间内完成")

    @staticmethod
    def _routing_policy_locator(resource: Any) -> dict[str, object]:
        fields = ("routingPolicy", "routing_policy", "rrsetRoutingPolicy", "geo_routing_policy", "weighted_routing_policy")
        if isinstance(resource, Mapping):
            return {field: resource[field] for field in fields if field in resource}
        return {
            field: value
            for field in fields
            if (value := getattr(resource, field, None)) is not None
        }

    @staticmethod
    def _to_google_record_set(managed_zone: Any, record: RecordSetDraft | RemoteRecordSet):
        if record.ttl is None:
            raise ValueError("Google Cloud DNS 简单记录必须包含 TTL")
        return managed_zone.resource_record_set(
            f"{record.record_name.rstrip('.')}.",
            record.record_type,
            record.ttl,
            [GoogleCloudDnsAdapter._to_google_value(record.record_type, value) for value in record.values],
        )

    @staticmethod
    def _to_google_value(record_type: str, value: str) -> str:
        if record_type == "CNAME":
            return f"{value.rstrip('.')}."
        if record_type == "MX":
            priority, target = value.split(" ", 1)
            return f"{priority} {target.rstrip('.') + '.'}"
        if record_type == "SRV":
            priority, weight, port, target = value.split(" ", 3)
            return f"{priority} {weight} {port} {target.rstrip('.') + '.'}"
        return value
