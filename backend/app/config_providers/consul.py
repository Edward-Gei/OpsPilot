"""Consul ACL KV 前缀的 HTTP 适配。"""
from __future__ import annotations

import base64
import json

from app.config_providers.base import (
    ConfigPlatformAdapter,
    DiscoverQuery,
    HttpConfigAdapter,
    PlatformConnection,
    ProviderProbe,
    ProviderRejectedError,
    ProviderWriteUncertainError,
    RemoteConfigResource,
    RemoteContent,
    RemoteExpectation,
    RemoteLocator,
    RemoteWriteResult,
)
from app.services.config_content_service import NormalizedContent, validate_and_normalize_content


_MAX_TXN_KV_OPERATIONS = 64


class ConsulAdapter(HttpConfigAdapter, ConfigPlatformAdapter):
    """Consul 前缀读写使用 ACL 与单次事务，拒绝可能形成半发布的大批量。"""

    async def probe(self, connection: PlatformConnection) -> ProviderProbe:
        response = await self._request(
            connection, "GET", "/v1/status/leader", headers=self._headers(connection), operation="probe",
        )
        self._ensure_success(response, "probe")
        return ProviderProbe(reachable=True, detail="leader_available")

    async def discover(
        self, connection: PlatformConnection, query: DiscoverQuery,
    ) -> list[RemoteConfigResource]:
        datacenter = query.values.get("datacenter", "").strip()
        prefix = query.values.get("kv_prefix", "").strip().strip("/")
        if not datacenter or not prefix:
            raise ProviderRejectedError("discover", "missing_scope")
        response = await self._request(
            connection,
            "GET",
            f"/v1/kv/{prefix}",
            headers=self._headers(connection),
            params={"dc": datacenter, "keys": "true"},
            operation="discover",
        )
        if response.status_code == 404:
            return []
        self._ensure_success(response, "discover")
        try:
            keys = response.json()
        except ValueError as exc:
            raise ProviderRejectedError("discover", "invalid_response") from exc
        if not isinstance(keys, list) or not keys:
            return []
        return [RemoteConfigResource(
            locator={"datacenter": datacenter, "kv_prefix": prefix},
            display_name=prefix,
        )]

    async def read(self, connection: PlatformConnection, locator: RemoteLocator) -> RemoteContent | None:
        datacenter, prefix = self._locator(locator)
        response = await self._request(
            connection,
            "GET",
            f"/v1/kv/{prefix}",
            headers=self._headers(connection),
            params={"dc": datacenter, "recurse": "true"},
            operation="read",
        )
        if response.status_code == 404:
            return None
        self._ensure_success(response, "read")
        try:
            items = response.json()
            values = self._decode_items(prefix, items)
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderRejectedError("read", "invalid_kv_value") from exc
        normalized = validate_and_normalize_content("consul_kv", json.dumps(values, ensure_ascii=False))
        revisions = [str(item.get("ModifyIndex", "")) for item in items if item.get("ModifyIndex") is not None]
        return RemoteContent(
            locator={"datacenter": datacenter, "kv_prefix": prefix},
            content=normalized.canonical,
            revision=max(revisions, key=lambda value: int(value or 0), default=None),
        )

    async def write(
        self,
        connection: PlatformConnection,
        locator: RemoteLocator,
        content: NormalizedContent,
        *,
        expect: RemoteExpectation,
    ) -> RemoteWriteResult:
        if content.format != "consul_kv" or not isinstance(content.structured, dict):
            raise ProviderRejectedError("write", "format")
        datacenter, prefix = self._locator(locator)
        response = await self._request(
            connection, "GET", f"/v1/kv/{prefix}", headers=self._headers(connection),
            params={"dc": datacenter, "recurse": "true"}, operation="write_check",
        )
        if response.status_code == 404:
            items = []
        else:
            self._ensure_success(response, "write_check")
            try:
                items = response.json()
            except ValueError as exc:
                raise ProviderRejectedError("write_check", "invalid_response") from exc
        try:
            existing = self._decode_items(prefix, items)
            indexes = {item["Key"][len(prefix) + 1:]: int(item["ModifyIndex"]) for item in items}
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderRejectedError("write_check", "invalid_response") from exc
        if expect.resource_exists != bool(items) or (expect.resource_exists and
            (expect.content is None or
             validate_and_normalize_content("consul_kv", json.dumps(existing, ensure_ascii=False)).canonical
                != validate_and_normalize_content("consul_kv", expect.content).canonical)):
            raise ProviderRejectedError("write", "remote_changed")
        operations = []
        for key, value in sorted(content.structured.items()):
            operation = {"Verb": "cas", "Key": f"{prefix}/{key}",
                         "Value": base64.b64encode(value.encode("utf-8")).decode("ascii"),
                         "Index": indexes.get(key, 0)}
            if existing.get(key) == value:
                operation = {"Verb": "check-index", "Key": f"{prefix}/{key}", "Index": indexes[key]}
            operations.append({"KV": operation})
        for key in sorted(existing.keys() - content.structured.keys()):
            operations.append({"KV": {"Verb": "delete-cas", "Key": f"{prefix}/{key}", "Index": indexes[key]}})
        if len(operations) > _MAX_TXN_KV_OPERATIONS:
            raise ProviderRejectedError("write", "max_64_kv_operations")
        if not operations:
            return RemoteWriteResult()
        response = await self._request(
            connection,
            "PUT",
            "/v1/txn",
            headers=self._headers(connection),
            params={"dc": datacenter},
            json=operations,
            operation="write",
            is_write=True,
        )
        self._ensure_success(response, "write")
        try:
            result = response.json()
        except ValueError as exc:
            raise ProviderWriteUncertainError("write", "invalid_response") from exc
        if not isinstance(result, dict) or result.get("Errors"):
            raise ProviderWriteUncertainError("write", "transaction_failed")
        return RemoteWriteResult()

    @staticmethod
    def _headers(connection: PlatformConnection) -> dict[str, str]:
        if connection.auth_type != "api_token":
            raise ProviderRejectedError("auth", "credential_type")
        return {"X-Consul-Token": connection.secret}

    def _locator(self, locator: RemoteLocator) -> tuple[str, str]:
        datacenter, prefix = self._required(locator, "datacenter", "kv_prefix")
        prefix = prefix.strip("/")
        if not prefix or any(part in {"", ".", ".."} for part in prefix.split("/")) or any(
            char in prefix for char in "?#%"
        ):
            raise ProviderRejectedError("locator", "invalid_field")
        return datacenter, prefix

    @staticmethod
    def _decode_items(prefix: str, items: object) -> dict[str, str]:
        if not isinstance(items, list):
            raise ValueError("items")
        normalized_prefix = f"{prefix}/"
        values: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("item")
            key = str(item["Key"])
            if not key.startswith(normalized_prefix):
                raise ValueError("key")
            relative_key = key[len(normalized_prefix):]
            if not relative_key or relative_key in values:
                raise ValueError("relative_key")
            encoded = item.get("Value")
            if not isinstance(encoded, str):
                raise ValueError("value")
            values[relative_key] = base64.b64decode(encoded, validate=True).decode("utf-8")
        return values
