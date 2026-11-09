"""执行器与目标驱动的共享合同；此层不依赖 ORM、权限或 API。"""
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import asyncio
import errno
import ssl
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class ValidatedSql:
    dialect: str
    submitted_text: str
    executed_statement: str
    statement_count: int
    selected_statement_index: int


@dataclass(frozen=True)
class ConnectionSpec:
    engine: str
    host: str
    port: int
    credential_id: int
    login_user: str | None
    secret: str = field(repr=False)
    options: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedOperation:
    task_id: int
    kind: str
    context: dict
    parameters: dict
    executed_statement: str | None
    operation_summary: str
    max_rows: int
    max_bytes: int


@dataclass(frozen=True)
class ResultColumn:
    ordinal: int
    name: str
    type: str


@dataclass(frozen=True)
class RowBatch:
    columns: list[ResultColumn]
    rows: list[list[Any]]


@dataclass(frozen=True)
class StopConfirmation:
    confirmed: bool
    reason: str
    backend_ref: dict
    evidence: dict = field(default_factory=dict)


@dataclass
class RunningOperation:
    backend_ref: dict
    columns: list[ResultColumn] = field(default_factory=list)
    completion: StopConfirmation | None = None
    summary: dict = field(default_factory=dict)
    driver_state: Any = field(default=None, repr=False)


class DatabaseAdapter(Protocol):
    target_connection_cost: int
    control_connection_cost: int

    async def open_operation(self, connection: ConnectionSpec, prepared: PreparedOperation, *, deadline: float) -> RunningOperation: ...
    async def run(self, handle: RunningOperation) -> None: ...
    def batches(self, handle: RunningOperation) -> AsyncIterator[RowBatch]: ...
    async def request_stop(self, connection: ConnectionSpec, backend_ref: dict, *, reason: str, deadline: float) -> StopConfirmation: ...
    async def close(self, handle: RunningOperation) -> None: ...


class AdapterError(ValueError):
    """向外仅暴露受控错误码，驱动原始异常不能泄漏连接或业务数据。"""

    def __init__(self, code: str, reason: str, completion: StopConfirmation | None = None):
        self.code = code
        self.reason = reason
        self.completion = completion
        super().__init__(reason)


class ControlCleanup:
    """在控制连接 finally 关闭后补充事实，关闭异常仍保留已经取得的目标证据。"""
    def __init__(self):
        self.confirmation = None

    def result(self, confirmed, reason, backend_ref, evidence=None):
        self.confirmation = StopConfirmation(confirmed, reason, backend_ref, evidence or {})
        return self.confirmation

    def closed(self):
        if self.confirmation:
            self.confirmation.evidence["control_closed"] = True

    def failed(self):
        return AdapterError("control_cleanup_unconfirmed", "控制连接关闭尚未确认", self.confirmation)


def local_closed_error(exc):
    """只能在已实际关闭本地资源之后调用，绝不由此推断目标停止。"""
    completion = getattr(exc, "completion", None) or StopConfirmation(False, "target_stop_unconfirmed", {})
    completion = StopConfirmation(completion.confirmed, completion.reason, completion.backend_ref,
                                  {**completion.evidence, "local_closed": True})
    return AdapterError(getattr(exc, "code", "target_execution_timeout" if isinstance(exc, TimeoutError) else "target_connection_failed"),
                        getattr(exc, "reason", "目标连接或初始化失败"), completion)


def connection_not_established(exc: BaseException) -> AdapterError | None:
    """仅用底层明确的 connect 拒绝/无路由证据，绝不把超时或重置算作未执行。"""
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 16:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, (TimeoutError, asyncio.CancelledError)) for item in chain):
        return None
    if any(isinstance(item, OSError) and item.errno in (errno.ECONNREFUSED, errno.ENETUNREACH, errno.EHOSTUNREACH) for item in chain):
        return AdapterError("target_connection_failed", "目标连接被拒绝或网络不可达",
                            StopConfirmation(True, "connection_not_established", {}, {"source": "connect_os_error"}))
    return None


def remaining(deadline: float) -> float:
    """全流程共享一个单调时钟截止点，避免每个读批次重新计时。"""
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("database deadline exceeded")
    return seconds


async def bounded(awaitable, deadline: float):
    try:
        timeout = remaining(deadline)
    except BaseException:
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise
    return await asyncio.wait_for(awaitable, timeout)


def tls_context(options: dict) -> ssl.SSLContext | None:
    if not options.get("tls", False):
        return None
    context = ssl.create_default_context()
    if not options.get("tls_verify", True):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context


def completed(handle: RunningOperation, reason: str = "operation_completed") -> None:
    handle.completion = StopConfirmation(True, reason, handle.backend_ref, {"source": "driver_completion"})


def validate_text_identifier(value: Any, *, max_length: int = 255) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length or "\x00" in value:
        raise AdapterError("invalid_context", "数据库对象名称不合法")
    return value
