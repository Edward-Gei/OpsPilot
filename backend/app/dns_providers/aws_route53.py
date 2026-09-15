"""AWS Route 53 公网 Zone 与简单记录集适配器。"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from typing import Any

import boto3

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


_REJECTED_ERROR_MARKERS = (
    "accessdenied", "access denied", "auth", "unauthorized", "forbidden", "invalid", "validation", "nosuch",
)


def _route53_client(credential: ProviderCredential):
    return boto3.client(
        "route53",
        aws_access_key_id=credential.login_user,
        aws_secret_access_key=credential.secret,
    )


def _route53_error(exc: Exception) -> ProviderRejectedError | ProviderUnavailableError:
    response = getattr(exc, "response", None)
    code = ""
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping):
            code = str(error.get("Code", "")).lower()
    if any(marker in code for marker in _REJECTED_ERROR_MARKERS):
        return ProviderRejectedError("AWS Route 53 拒绝该请求")
    return ProviderUnavailableError("AWS Route 53 暂时不可用")


class AwsRoute53Adapter(DnsProviderAdapter):
    """使用 Route 53 Hosted Zone API 管理公网简单记录集。"""

    def __init__(
        self,
        client_factory: Callable[[ProviderCredential], Any] | None = None,
        *,
        poll_attempts: int = 20,
        poll_interval: float = 0.2,
    ):
        self._client_factory = client_factory or _route53_client
        self._poll_attempts = poll_attempts
        self._poll_interval = poll_interval

    async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
        try:
            pages = await asyncio.to_thread(self._list_hosted_zone_pages, credential)
            return [
                ZoneRef(
                    DomainProvider.AWS_ROUTE53,
                    str(zone["Id"]).rsplit("/", 1)[-1],
                    normalize_zone_name(str(zone["Name"])),
                )
                for page in pages
                for zone in page.get("HostedZones", [])
                if not zone.get("Config", {}).get("PrivateZone", False)
            ]
        except Exception as exc:
            raise _route53_error(exc) from None

    async def list_record_sets(self, zone: ZoneRef, credential: ProviderCredential) -> list[RemoteRecordSet]:
        try:
            pages = await asyncio.to_thread(self._list_record_set_pages, zone, credential)
            return [
                self._to_remote_record_set(record, zone)
                for page in pages
                for record in page.get("ResourceRecordSets", [])
            ]
        except Exception as exc:
            raise _route53_error(exc) from None

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
        await self._change_record_set(zone, "CREATE", self._to_route53_record_set(record), credential)

    async def replace_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, after: RecordSetDraft, credential: ProviderCredential,
    ) -> None:
        await self._change_record_set(zone, "UPSERT", self._to_route53_record_set(after), credential)

    async def delete_simple_record_set(
        self, zone: ZoneRef, before: RemoteRecordSet, credential: ProviderCredential,
    ) -> None:
        await self._change_record_set(zone, "DELETE", self._to_route53_record_set(before), credential)

    def _list_hosted_zone_pages(self, credential: ProviderCredential) -> list[dict[str, Any]]:
        client = self._client_factory(credential)
        pages: list[dict[str, Any]] = []
        marker: str | None = None
        while True:
            page = client.list_hosted_zones(**({"Marker": marker} if marker else {}))
            pages.append(page)
            if not page.get("IsTruncated", False):
                return pages
            marker = page.get("NextMarker")
            if not marker:
                raise RuntimeError("Route 53 Hosted Zone 分页游标缺失")

    def _list_record_set_pages(self, zone: ZoneRef, credential: ProviderCredential) -> list[dict[str, Any]]:
        client = self._client_factory(credential)
        pages: list[dict[str, Any]] = []
        params: dict[str, Any] = {"HostedZoneId": zone.remote_zone_id}
        while True:
            page = client.list_resource_record_sets(**params)
            pages.append(page)
            if not page.get("IsTruncated", False):
                return pages
            next_name = page.get("NextRecordName")
            next_type = page.get("NextRecordType")
            if not next_name or not next_type:
                raise RuntimeError("Route 53 记录分页游标缺失")
            params = {
                "HostedZoneId": zone.remote_zone_id,
                "StartRecordName": next_name,
                "StartRecordType": next_type,
            }
            if page.get("NextRecordIdentifier") is not None:
                params["StartRecordIdentifier"] = page["NextRecordIdentifier"]

    def _to_remote_record_set(self, resource: Mapping[str, Any], zone: ZoneRef) -> RemoteRecordSet:
        record_name = normalize_owner_name(str(resource["Name"]), zone.zone_name)
        record_type = str(resource["Type"]).upper()
        locator = self._record_locator(resource, record_name, record_type)
        provider_meta: dict[str, object] = {"locator": locator}
        if "AliasTarget" in resource:
            provider_meta["alias"] = True
        if any(key in resource for key in (
            "SetIdentifier", "Failover", "GeoLocation", "Region", "MultiValueAnswer", "CidrRoutingConfig",
        )):
            provider_meta["advanced_routing"] = True

        alias_target = resource.get("AliasTarget")
        if isinstance(alias_target, Mapping):
            values = (str(alias_target.get("DNSName", "")).rstrip("."),)
            ttl: int | None = None
        else:
            raw_values = tuple(str(item.get("Value", "")) for item in resource.get("ResourceRecords", []))
            ttl = int(resource["TTL"]) if resource.get("TTL") is not None else None
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
    def _record_locator(resource: Mapping[str, Any], record_name: str, record_type: str) -> dict[str, object]:
        locator: dict[str, object] = {"name": record_name, "type": record_type}
        selector_keys = {
            "SetIdentifier": "set_identifier",
            "Failover": "failover",
            "GeoLocation": "geo_location",
            "Region": "region",
            "MultiValueAnswer": "multi_value_answer",
            "CidrRoutingConfig": "cidr_routing_config",
        }
        for source, target in selector_keys.items():
            if source in resource:
                locator[target] = resource[source]
        return locator

    async def _change_record_set(
        self, zone: ZoneRef, action: str, record_set: dict[str, object], credential: ProviderCredential,
    ) -> None:
        submitted = False
        try:
            client = await asyncio.to_thread(self._client_factory, credential)
            response = await asyncio.to_thread(
                client.change_resource_record_sets,
                HostedZoneId=zone.remote_zone_id,
                ChangeBatch={"Changes": [{"Action": action, "ResourceRecordSet": record_set}]},
            )
            submitted = True
            change = response.get("ChangeInfo", {})
            change_id = change.get("Id")
            if not change_id:
                raise RuntimeError("Route 53 变更编号缺失")
            await asyncio.to_thread(self._wait_for_change, client, str(change_id))
        except Exception as exc:
            if submitted:
                raise ProviderUnavailableError("AWS Route 53 暂时不可用") from None
            raise _route53_error(exc) from None

    def _wait_for_change(self, client: Any, change_id: str) -> None:
        for attempt in range(self._poll_attempts):
            response = client.get_change(Id=change_id)
            if response.get("ChangeInfo", {}).get("Status") == "INSYNC":
                return
            if attempt < self._poll_attempts - 1 and self._poll_interval:
                time.sleep(self._poll_interval)
        raise TimeoutError("Route 53 变更未在限定时间内完成")

    @staticmethod
    def _to_route53_record_set(record: RecordSetDraft | RemoteRecordSet) -> dict[str, object]:
        if record.ttl is None:
            raise ValueError("Route 53 简单记录必须包含 TTL")
        return {
            "Name": f"{record.record_name.rstrip('.')}.",
            "Type": record.record_type,
            "TTL": record.ttl,
            "ResourceRecords": [
                {"Value": AwsRoute53Adapter._to_route53_value(record.record_type, value)}
                for value in record.values
            ],
        }

    @staticmethod
    def _to_route53_value(record_type: str, value: str) -> str:
        if record_type == "CNAME":
            return f"{value.rstrip('.')}."
        if record_type == "MX":
            priority, target = value.split(" ", 1)
            return f"{priority} {target.rstrip('.')}."
        if record_type == "SRV":
            priority, weight, port, target = value.split(" ", 3)
            return f"{priority} {weight} {port} {target.rstrip('.')}."
        return value
