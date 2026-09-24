"""Nacos 2.2.3-2.5.0 的 HTTP 配置接口适配。"""
from __future__ import annotations

from app.config_providers.base import (
    ConfigPlatformAdapter,
    DiscoverQuery,
    HttpConfigAdapter,
    PlatformConnection,
    ProviderProbe,
    ProviderRejectedError,
    RemoteConfigResource,
    RemoteContent,
    RemoteExpectation,
    RemoteLocator,
    RemoteWriteResult,
)
from app.services.config_content_service import NormalizedContent


class NacosAdapter(HttpConfigAdapter, ConfigPlatformAdapter):
    """Nacos 配置读写；用户名密码会在每个远端操作前短暂换取访问令牌。"""

    async def probe(self, connection: PlatformConnection) -> ProviderProbe:
        response = await self._request(
            connection, "GET", "/nacos/v1/console/health/readiness", operation="probe",
        )
        self._ensure_success(response, "probe")
        return ProviderProbe(reachable=True, version=response.headers.get("Server"))

    async def list_namespaces(self, connection: PlatformConnection) -> list[dict[str, str]]:
        """读取命名空间目录；默认 public 的展示名不能作为配置 API 的 tenant。"""
        headers = await self._auth_headers(connection)
        response = await self._request(
            connection, "GET", "/nacos/v1/console/namespaces", headers=headers, operation="namespaces",
        )
        self._ensure_success(response, "namespaces")
        try:
            payload = response.json()
            if payload["code"] != 200 or not isinstance(payload["data"], list):
                raise ValueError("invalid namespace response")
            entries = payload["data"]
            if not all(isinstance(item, dict) and isinstance(item.get("namespace"), str) for item in entries):
                raise ValueError("invalid namespace entry")
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderRejectedError("namespaces", "invalid_response") from exc
        return [
            {"id": "" if item.get("type") == 0 else item["namespace"],
             "name": str(item.get("namespaceShowName") or item["namespace"] or "public")}
            for item in entries
        ]

    async def discover(
        self, connection: PlatformConnection, query: DiscoverQuery,
    ) -> list[RemoteConfigResource]:
        headers = await self._auth_headers(connection)
        scope = {key: value for key, value in query.values.items()
                 if key in {"tenant", "group", "dataId"} and (value or key == "tenant")}
        items = []
        page = 1
        while True:
            response = await self._request(
                connection, "GET", "/nacos/v1/cs/configs",
                params={"dataId": "", "group": "", **scope, "pageNo": str(page), "pageSize": "200", "search": "blur"},
                headers=headers, operation="discover",
            )
            self._ensure_success(response, "discover")
            try:
                result = response.json()
                batch = result["pageItems"]
                total = result.get("totalCount")
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderRejectedError("discover", "invalid_response") from exc
            if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
                raise ProviderRejectedError("discover", "invalid_response")
            if total is not None and (not isinstance(total, int) or total < len(items) + len(batch)):
                raise ProviderRejectedError("discover", "invalid_response")
            items.extend(batch)
            if (total is not None and len(items) >= total) or len(batch) < 200:
                if total is not None and len(items) < total:
                    raise ProviderRejectedError("discover", "incomplete_page")
                break
            page += 1
        return [
            RemoteConfigResource(
                locator={
                    "namespace": str(item.get("tenant") or item.get("namespace") or ""),
                    "group": str(item.get("group") or "DEFAULT_GROUP"),
                    "data_id": str(item["dataId"]),
                },
                display_name=str(item["dataId"]),
                revision=str(item.get("md5")) if item.get("md5") else None,
            )
            for item in items
            if item.get("dataId")
        ]

    async def read(self, connection: PlatformConnection, locator: RemoteLocator) -> RemoteContent | None:
        namespace, group, data_id = self._locator(locator)
        headers = await self._auth_headers(connection)
        params = {"tenant": namespace, "group": group, "dataId": data_id}
        response = await self._request(
            connection, "GET", "/nacos/v1/cs/configs", params=params, headers=headers, operation="read",
        )
        if response.status_code == 404:
            return None
        self._ensure_success(response, "read")
        return RemoteContent(
            locator={"namespace": namespace, "group": group, "data_id": data_id},
            content=response.text,
            revision=response.headers.get("Last-Modified") or response.headers.get("Content-MD5"),
        )

    async def write(
        self,
        connection: PlatformConnection,
        locator: RemoteLocator,
        content: NormalizedContent,
        *,
        expect: RemoteExpectation,
    ) -> RemoteWriteResult:
        namespace, group, data_id = self._locator(locator)
        remote = await self.read(connection, locator)
        if (remote is None) != (not expect.resource_exists) or (
            remote is not None and remote.content != expect.content
        ):
            raise ProviderRejectedError("write", "remote_changed")
        headers = await self._auth_headers(connection)
        payload = {
            "tenant": namespace,
            "group": group,
            "dataId": data_id,
            "content": content.canonical,
            "type": "yaml" if content.format == "yaml" else content.format,
        }
        response = await self._request(
            connection,
            "POST",
            "/nacos/v1/cs/configs",
            headers=headers,
            data=payload,
            operation="write",
            is_write=True,
        )
        self._ensure_success(response, "write")
        if response.text.strip().lower() != "true":
            raise ProviderRejectedError("write", "publish_rejected")
        return RemoteWriteResult(revision=response.headers.get("Last-Modified"))

    async def _auth_headers(self, connection: PlatformConnection) -> dict[str, str]:
        if connection.auth_type == "api_token":
            return {"Authorization": f"Bearer {connection.secret}"}
        if connection.auth_type != "username_password" or not connection.username:
            raise ProviderRejectedError("auth", "credential_type")
        response = await self._request(
            connection,
            "POST",
            "/nacos/v1/auth/login",
            data={"username": connection.username, "password": connection.secret},
            operation="auth",
        )
        self._ensure_success(response, "auth")
        try:
            token = str(response.json()["accessToken"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderRejectedError("auth", "invalid_response") from exc
        return {"Authorization": f"Bearer {token}"}

    def _locator(self, locator: RemoteLocator) -> tuple[str, str, str]:
        if "namespace" not in locator:
            raise ProviderRejectedError("locator", "missing_field")
        group, data_id = self._required(locator, "group", "data_id")
        return str(locator.get("namespace", "")).strip(), group, data_id
