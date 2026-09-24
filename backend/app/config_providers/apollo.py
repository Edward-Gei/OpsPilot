"""Apollo Open API 的 Namespace 读写适配。"""
from __future__ import annotations

from app.config_providers.base import (
    ConfigPlatformAdapter,
    DiscoverQuery,
    HttpConfigAdapter,
    PlatformConnection,
    ProviderProbe,
    ProviderError,
    ProviderRejectedError,
    ProviderWriteUncertainError,
    RemoteConfigResource,
    RemoteContent,
    RemoteExpectation,
    RemoteLocator,
    RemoteWriteResult,
)
from app.services.config_content_service import NormalizedContent, validate_and_normalize_content


class ApolloAdapter(HttpConfigAdapter, ConfigPlatformAdapter):
    """Apollo Namespace 以单个 ``content`` Item 承载非 properties 配置正文。"""

    async def probe(self, connection: PlatformConnection) -> ProviderProbe:
        response = await self._request(
            connection, "GET", "/openapi/v1/apps", headers=self._headers(connection), operation="probe",
        )
        self._ensure_success(response, "probe")
        return ProviderProbe(reachable=True)

    async def discover(
        self, connection: PlatformConnection, query: DiscoverQuery,
    ) -> list[RemoteConfigResource]:
        app_id = query.values.get("app_id", "").strip()
        cluster = query.values.get("cluster", "").strip()
        if not app_id or not cluster:
            raise ProviderRejectedError("discover", "missing_scope")
        response = await self._request(
            connection,
            "GET",
            f"/openapi/v1/envs/PRO/apps/{app_id}/clusters/{cluster}/namespaces",
            headers=self._headers(connection),
            operation="discover",
        )
        self._ensure_success(response, "discover")
        try:
            namespaces = response.json()
        except ValueError as exc:
            raise ProviderRejectedError("discover", "invalid_response") from exc
        if not isinstance(namespaces, list):
            raise ProviderRejectedError("discover", "invalid_response")
        return [
            RemoteConfigResource(
                locator={"app_id": app_id, "cluster": cluster, "namespace": str(item["namespaceName"])},
                display_name=str(item["namespaceName"]),
            )
            for item in namespaces
            if isinstance(item, dict) and item.get("namespaceName")
        ]

    async def read(self, connection: PlatformConnection, locator: RemoteLocator) -> RemoteContent | None:
        app_id, cluster, namespace = self._locator(locator)
        path = self._namespace_path(app_id, cluster, namespace)
        metadata = await self._request(
            connection, "GET", path, headers=self._headers(connection), operation="read",
        )
        if metadata.status_code == 404:
            return None
        self._ensure_success(metadata, "read")
        try:
            content_format = metadata.json()["format"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderRejectedError("read", "invalid_response") from exc
        if content_format not in {"properties", "json", "yaml", "yml"}:
            raise ProviderRejectedError("read", "unsupported_format")
        response = await self._request(
            connection,
            "GET",
            f"{path}/releases/latest",
            headers=self._headers(connection),
            operation="read",
        )
        if response.status_code == 404:
            return None
        self._ensure_success(response, "read")
        try:
            payload = response.json()
            values = payload["configurations"]
        except (AttributeError, ValueError) as exc:
            raise ProviderRejectedError("read", "invalid_response") from exc
        except (KeyError, TypeError) as exc:
            raise ProviderRejectedError("read", "invalid_response") from exc
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise ProviderRejectedError("read", "invalid_response")
        if content_format == "properties":
            text = validate_and_normalize_content("properties", "\n".join(
                f"{key}={value}" for key, value in values.items()
            )).canonical
        elif set(values) == {"content"}:
            text = values["content"]
        else:
            raise ProviderRejectedError("read", "invalid_response")
        return RemoteContent(
            locator={"app_id": app_id, "cluster": cluster, "namespace": namespace},
            content=text,
            revision=str(payload.get("id")) if payload.get("id") else None,
        )

    async def write(
        self,
        connection: PlatformConnection,
        locator: RemoteLocator,
        content: NormalizedContent,
        *,
        expect: RemoteExpectation,
    ) -> RemoteWriteResult:
        app_id, cluster, namespace = self._locator(locator)
        path = self._namespace_path(app_id, cluster, namespace)
        if content.format not in {"properties", "yaml", "json"}:
            raise ProviderRejectedError("write", "unsupported_format")
        published = await self.read(connection, locator)
        if (published is None) != (not expect.resource_exists) or (
            published is not None and published.content != expect.content
        ):
            raise ProviderRejectedError("write", "remote_changed")
        draft = await self._request(
            connection, "GET", path, headers=self._headers(connection), operation="write_check",
        )
        if draft.status_code == 404 and not expect.resource_exists:
            if content.format != "properties" and not namespace.endswith(f".{content.format}"):
                raise ProviderRejectedError("write", "format_mismatch")
            parent = await self._request(
                connection, "GET", f"/openapi/v1/envs/PRO/apps/{app_id}/clusters/{cluster}",
                headers=self._headers(connection), operation="write_check",
            )
            self._ensure_success(parent, "write_check")
            name = namespace[:-(len(content.format) + 1)] if content.format != "properties" else namespace
            if not name:
                raise ProviderRejectedError("write", "invalid_namespace")
            try:
                created = await self._request(
                    connection, "POST", f"/openapi/v1/apps/{app_id}/appnamespaces",
                    headers=self._headers(connection), json={
                        "appId": app_id, "name": name, "format": content.format,
                        "isPublic": False, "appendNamespacePrefix": False,
                        "dataChangeCreatedBy": "OpsPilot",
                    }, operation="write", is_write=True,
                )
                self._ensure_success(created, "write")
                if created.json().get("name") != namespace:
                    raise ProviderWriteUncertainError("write", "namespace_mismatch")
                draft = await self._request(
                    connection, "GET", path, headers=self._headers(connection), operation="write_check",
                )
            except (ProviderError, TypeError, AttributeError, ValueError) as exc:
                raise ProviderWriteUncertainError("write", getattr(exc, "code", "invalid_response")) from None
        self._ensure_success(draft, "write_check")
        try:
            draft_data = draft.json()
            items = draft_data["items"]
            draft_values = {item["key"]: item["value"] for item in items}
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderRejectedError("write_check", "invalid_response") from exc
        if draft_data.get("format") not in ({"yaml", "yml"} if content.format == "yaml" else {content.format}):
            raise ProviderRejectedError("write", "format_mismatch")
        expected_values = (
            validate_and_normalize_content("properties", expect.content).structured
            if content.format == "properties" and expect.resource_exists else
            {"content": expect.content} if expect.resource_exists else {}
        )
        if draft_values != expected_values:
            raise ProviderRejectedError("write", "pending_external_edits")
        path = f"{path}/items"
        if content.format == "properties":
            desired_values = content.structured
            changes = (
                ("PUT", "batch-update", [{"key": key, "value": value} for key, value in sorted(desired_values.items())
                                         if key in draft_values and draft_values[key] != value]),
                ("POST", "batch-create", [{"key": key, "value": value} for key, value in sorted(desired_values.items())
                                          if key not in draft_values]),
                ("POST", "batch-delete", sorted(draft_values.keys() - desired_values.keys())),
            )
            for method, action, body in changes:
                if not body:
                    continue
                try:
                    response = await self._request(
                        connection, method, f"{path}/{action}", headers=self._headers(connection),
                        params={"operator": "OpsPilot"}, json=body, operation="write", is_write=True,
                    )
                    self._ensure_success(response, "write")
                except ProviderError as exc:
                    raise ProviderWriteUncertainError("write", exc.code) from None
        else:
            payload = {"key": "content", "value": content.canonical}
            method, item_path = ("PUT", f"{path}/content") if "content" in draft_values else ("POST", path)
            payload["dataChangeLastModifiedBy" if method == "PUT" else "dataChangeCreatedBy"] = "OpsPilot"
            response = await self._request(
                connection, method, item_path, headers=self._headers(connection),
                json=payload, operation="write", is_write=True,
            )
            self._ensure_success(response, "write")
        try:
            release = await self._request(
                connection, "POST", f"{self._namespace_path(app_id, cluster, namespace)}/releases",
                headers=self._headers(connection),
                json={"releaseTitle": "OpsPilot", "releasedBy": "OpsPilot"},
                operation="release", is_write=True,
            )
            self._ensure_success(release, "release")
        except ProviderError as exc:
            raise ProviderWriteUncertainError("release", exc.code) from None
        return RemoteWriteResult()

    @staticmethod
    def _headers(connection: PlatformConnection) -> dict[str, str]:
        if connection.auth_type != "api_token":
            raise ProviderRejectedError("auth", "credential_type")
        return {"Authorization": connection.secret}

    def _locator(self, locator: RemoteLocator) -> tuple[str, str, str]:
        values = self._required(locator, "app_id", "cluster", "namespace")
        if any(value in {".", ".."} or any(char in value for char in "/?#%") for value in values):
            raise ProviderRejectedError("locator", "invalid_field")
        return values

    @staticmethod
    def _namespace_path(app_id: str, cluster: str, namespace: str) -> str:
        return f"/openapi/v1/envs/PRO/apps/{app_id}/clusters/{cluster}/namespaces/{namespace}"
