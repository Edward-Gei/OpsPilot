"""应用配置平台适配器的无状态 HTTP 契约。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.constants import ConfigProvider
from app.services.config_content_service import NormalizedContent


RemoteLocator = dict[str, str]


@dataclass(frozen=True)
class PlatformConnection:
    """服务层短暂解密后交给适配器的连接信息，不可持久化或写日志。"""

    provider: ConfigProvider
    base_url: str
    credential_id: int
    auth_type: str
    secret: str
    username: str | None = None


@dataclass(frozen=True)
class DiscoverQuery:
    """发现范围由页面收集后传入，字段由各平台适配器解释。"""

    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RemoteConfigResource:
    locator: RemoteLocator
    display_name: str
    revision: str | None = None


@dataclass(frozen=True)
class RemoteContent:
    locator: RemoteLocator
    content: str
    revision: str | None = None


@dataclass(frozen=True)
class RemoteExpectation:
    resource_exists: bool
    revision: str | None = None
    content: str | None = None

    @classmethod
    def exists(cls, content: RemoteContent) -> "RemoteExpectation":
        return cls(resource_exists=True, revision=content.revision, content=content.content)

    @classmethod
    def missing(cls) -> "RemoteExpectation":
        return cls(resource_exists=False)


@dataclass(frozen=True)
class RemoteWriteResult:
    revision: str | None = None


@dataclass(frozen=True)
class ProviderProbe:
    reachable: bool
    version: str | None = None
    detail: str | None = None


class ProviderError(Exception):
    """只保留操作和受控错误码，避免厂商响应泄露到任务或审计。"""

    def __init__(self, operation: str, code: str = "unknown"):
        self.operation = operation
        self.code = code
        super().__init__(f"{operation}:{code}")


class ProviderRejectedError(ProviderError):
    """认证、权限、参数或容量限制导致的确定性拒绝。"""


class ProviderUnavailableError(ProviderError):
    """读取、发现或探测无法确定远端状态。"""


class ProviderWriteUncertainError(ProviderError):
    """写入请求未获确认；Worker 必须回读，且不得自动重写。"""


class ConfigPlatformAdapter(Protocol):
    async def probe(self, connection: PlatformConnection) -> ProviderProbe: ...

    async def discover(
        self, connection: PlatformConnection, query: DiscoverQuery,
    ) -> list[RemoteConfigResource]: ...

    async def read(self, connection: PlatformConnection, locator: RemoteLocator) -> RemoteContent | None: ...

    async def write(
        self,
        connection: PlatformConnection,
        locator: RemoteLocator,
        content: NormalizedContent,
        *,
        expect: RemoteExpectation,
    ) -> RemoteWriteResult: ...


class HttpConfigAdapter:
    """适配器共享的受控 HTTP 调用；写入绝不内建重试。"""

    def __init__(self, requester: httpx.AsyncClient | None = None):
        self._requester = requester

    async def _request(
        self,
        connection: PlatformConnection,
        method: str,
        path: str,
        *,
        operation: str,
        is_write: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        url = f"{connection.base_url.rstrip('/')}{path}"
        try:
            if self._requester is not None:
                return await self._requester.request(method, url, **kwargs)
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                return await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            error = ProviderWriteUncertainError if is_write else ProviderUnavailableError
            raise error(operation, "timeout") from exc
        except httpx.RequestError as exc:
            error = ProviderWriteUncertainError if is_write else ProviderUnavailableError
            raise error(operation, "network") from exc

    @staticmethod
    def _ensure_success(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        if response.status_code in {400, 401, 403, 404, 409, 413, 422}:
            raise ProviderRejectedError(operation, f"http_{response.status_code}")
        raise ProviderUnavailableError(operation, f"http_{response.status_code}")

    @staticmethod
    def _required(locator: RemoteLocator, *keys: str) -> tuple[str, ...]:
        values = tuple(str(locator.get(key, "")).strip() for key in keys)
        if any(not value for value in values):
            raise ProviderRejectedError("locator", "missing_field")
        return values
