"""Redis 固定只读命令、受限 RESP2 解码及绑定上下文的扫描游标。"""
import asyncio
import hashlib
import hmac
import time
import uuid

from redis.asyncio import ConnectionPool, Redis
from redis._parsers.resp2 import _AsyncRESP2Parser
from redis.exceptions import AuthenticationError, ConnectionError, InvalidResponse, ResponseError

from app.database_adapters.base import (
    AdapterError, ConnectionSpec, PreparedOperation, ResultColumn, RowBatch,
    RunningOperation, StopConfirmation, bounded, completed, remaining, connection_not_established,
    ControlCleanup, local_closed_error,
)
from app.database_adapters.native_policy import validate_native_operation


class BoundedRESP2Parser(_AsyncRESP2Parser):
    """先读 RESP 长度再分配；COUNT 仅为扫描提示，不能作为内存护栏。"""
    limit_bytes = 16 * 1024 * 1024 + 65536

    async def read_response(self, disable_decoding=False):
        self.response_bytes = 0
        self.response_items = 0
        self.response_depth = 0
        return await super().read_response(disable_decoding)

    async def _readline(self):
        raw = await super()._readline()
        self.response_bytes += len(raw) + 2
        if len(raw) > 4096 or self.response_bytes > self.limit_bytes:
            raise AdapterError("protocol_value_too_large", "Redis 返回内容超过安全读取上限")
        return raw

    async def _read(self, length):
        if length < 0 or self.response_bytes + length + 2 > self.limit_bytes:
            raise AdapterError("protocol_value_too_large", "Redis 返回单值超过安全读取上限")
        self.response_bytes += length + 2
        return await super()._read(length)

    async def _read_response(self, disable_decoding=False):
        self.response_items += 1
        self.response_depth += 1
        if self.response_items > 20000 or self.response_depth > 8:
            raise AdapterError("protocol_value_too_large", "Redis 返回集合超过安全读取上限")
        try:
            raw = await self._readline()
            kind, value = raw[:1], raw[1:]
            if kind == b"-":
                error = self.parse_error(value.decode("utf-8", errors="replace"))
                if isinstance(error, ConnectionError):
                    self._clear()
                    raise error
                return error
            if kind == b":":
                return int(value)
            if kind in (b"$", b"*") and value == b"-1":
                return None
            if kind == b"$":
                result = await self._read(int(value))
            elif kind == b"*":
                length = int(value)
                if not 0 <= length <= 20000:
                    raise AdapterError("protocol_value_too_large", "Redis 返回集合超过安全读取上限")
                return [await self._read_response(disable_decoding) for _ in range(length)]
            elif kind == b"+":
                result = value
            else:
                raise InvalidResponse("Unsupported Redis response")
            return result if disable_decoding else self.encoder.decode(result)
        finally:
            self.response_depth -= 1


def _display(value):
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


def _cursor_scope(connection: ConnectionSpec, context: dict, operation: dict):
    raw = f"{connection.host}:{connection.port}:{connection.credential_id}:{context['database']}:{operation['type']}:{operation.get('key','')}:{operation.get('pattern','*')}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _sign(value: str) -> str:
    # 与平台密钥同生命周期；游标不含权限，使用时仍走本人和当前实例授权。
    from app.core.config import settings
    return hmac.new(settings.jwt_secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def _seal_cursor(cursor, connection, context, operation):
    if int(cursor) == 0:
        return "0"
    body = f"v1.{int(time.time())}.{int(cursor)}.{_cursor_scope(connection, context, operation)}"
    return f"{body}.{_sign(body)}"


def _unseal_cursor(token, connection, context, operation):
    if token in (None, "", "0"):
        return 0
    try:
        version, issued, cursor, scope, signature = token.split(".")
        body = ".".join((version, issued, cursor, scope))
        if version != "v1" or not issued.isdecimal() or not cursor.isdecimal() or len(cursor) > 20:
            raise ValueError
        if not 0 <= time.time() - int(issued) <= 600 or scope != _cursor_scope(connection, context, operation):
            raise ValueError
        if not hmac.compare_digest(signature, _sign(body)):
            raise ValueError
        return int(cursor)
    except (ValueError, TypeError, AttributeError) as exc:
        raise AdapterError("invalid_cursor", "扫描游标已失效或不属于当前查询上下文") from exc


def _client(spec, database, deadline, max_bytes):
    class _Parser(BoundedRESP2Parser):
        limit_bytes = max_bytes + 65536
    kwargs = dict(
        host=spec.host, port=spec.port, db=database, password=spec.secret,
        username=spec.login_user if spec.options.get("auth_mode", "acl") == "acl" else None,
        max_connections=1, socket_timeout=remaining(deadline), socket_connect_timeout=min(10, remaining(deadline)),
        decode_responses=False, parser_class=_Parser, protocol=2,
    )
    if spec.options.get("tls"):
        from redis.asyncio.connection import SSLConnection
        kwargs.update(connection_class=SSLConnection, ssl_cert_reqs="required" if spec.options.get("tls_verify", True) else "none",
                      ssl_check_hostname=spec.options.get("tls_verify", True))
    return Redis(connection_pool=ConnectionPool(**kwargs))


class RedisAdapter:
    target_connection_cost = 1
    control_connection_cost = 1

    async def open_operation(self, connection: ConnectionSpec, prepared: PreparedOperation, *, deadline: float) -> RunningOperation:
        database = prepared.context.get("database", connection.options.get("database", 0))
        client = _client(connection, database, deadline, prepared.max_bytes)
        marker = f"opspilot:{prepared.task_id}:{uuid.uuid4().hex}"
        try:
            await bounded(client.ping(), deadline)
            client_id = await bounded(client.client_id(), deadline)
            await bounded(client.client_setname(marker), deadline)
            try:
                server_run_id = (await bounded(client.info("server"), deadline)).get("run_id")
            except ResponseError:
                server_run_id = None
        except (AuthenticationError, ResponseError) as exc:
            await client.aclose(close_connection_pool=True)
            raise AdapterError("target_authentication_failed", "Redis 认证或必要只读连接能力不足",
                               StopConfirmation(True, "server_rejected_connection", {}, {"source": "redis_error_response", "local_closed": True})) from exc
        except BaseException as exc:
            await client.aclose(close_connection_pool=True)
            error = connection_not_established(exc)
            if error is not None:
                raise local_closed_error(error) from exc
            if isinstance(exc, Exception):
                raise local_closed_error(exc) from exc
            raise
        return RunningOperation(
            {"client_id": client_id, "marker": marker, "database": database, "server_run_id": server_run_id},
            driver_state={"client": client, "prepared": prepared, "connection": connection, "deadline": deadline},
        )

    async def run(self, handle: RunningOperation) -> None:
        state = handle.driver_state
        client, prepared, deadline = state["client"], state["prepared"], state["deadline"]
        try:
            if prepared.kind == "probe":
                rows, names = [[True]], ["connected"]
                handle.summary = {"message": "连接和认证成功"}
            elif prepared.kind == "metadata":
                if prepared.parameters.get("operation") != "databases":
                    raise AdapterError("unsupported_metadata", "Redis 仅显示已登记的 DB 上下文")
                rows, names = [[str(handle.backend_ref["database"])]], ["name"]
            else:
                operation = prepared.parameters
                validate_native_operation("redis", prepared.context, operation)
                kind, key = operation["type"], operation.get("key")
                count, offset = operation.get("count", 100), operation.get("offset", 0)
                cursor = _unseal_cursor(operation.get("cursor"), state["connection"], prepared.context, operation)
                if kind in ("scan", "hash_page", "set_page"):
                    if kind == "scan":
                        next_cursor, values = await bounded(client.scan(cursor, match=operation.get("pattern", "*"), count=count), deadline)
                        names, rows = ["key", "next_cursor"], [[_display(value)] for value in values]
                    elif kind == "hash_page":
                        next_cursor, values = await bounded(client.hscan(key, cursor, count=count), deadline)
                        names, rows = ["field", "value", "next_cursor"], [[_display(field), _display(value)] for field, value in values.items()]
                    else:
                        next_cursor, values = await bounded(client.sscan(key, cursor, count=count), deadline)
                        names, rows = ["member", "next_cursor"], [[_display(value)] for value in values]
                    token = _seal_cursor(next_cursor, state["connection"], prepared.context, operation)
                    rows = [row + [token] for row in rows] or [[None] * (len(names) - 1) + [token]]
                elif kind == "type":
                    names, rows = ["type"], [[_display(await bounded(client.type(key), deadline))]]
                elif kind == "ttl":
                    names, rows = ["ttl_seconds"], [[await bounded(client.ttl(key), deadline)]]
                elif kind == "string_preview":
                    length = operation.get("length", 4096)
                    value = await bounded(client.getrange(key, offset, offset + length - 1), deadline)
                    names, rows = ["value", "offset", "preview_bytes"], [[_display(value), offset, len(value)]]
                elif kind == "list_page":
                    values = await bounded(client.lrange(key, offset, offset + count - 1), deadline)
                    names, rows = ["index", "value"], [[offset + index, _display(value)] for index, value in enumerate(values)]
                elif kind == "zset_page":
                    values = await bounded(client.zrange(key, offset, offset + count - 1, withscores=True), deadline)
                    names, rows = ["index", "member", "score"], [[offset + index, _display(value), score] for index, (value, score) in enumerate(values)]
                elif kind == "length":
                    value_type = await bounded(client.type(key), deadline)
                    method = {b"string": client.strlen, b"hash": client.hlen, b"list": client.llen, b"set": client.scard, b"zset": client.zcard}.get(value_type)
                    value = await bounded(method(key), deadline) if method else 0
                    names, rows = ["type", "length"], [[_display(value_type), value]]
                else:
                    raise AdapterError("unsupported_operation", "不支持的 Redis 只读操作")
            handle.columns = [ResultColumn(index, name, "redis") for index, name in enumerate(names)]
            state["rows"] = rows
            # 固定命令的完整响应已经收到，是可信结束事实，不由 close 推断。
            completed(handle)
        except ResponseError as exc:
            completed(handle, "server_rejected_statement")
            raise AdapterError("target_query_failed", "Redis 拒绝操作或类型不匹配", handle.completion) from exc
        except AdapterError as exc:
            completed(handle, "server_response_received" if exc.code.startswith("protocol_") else "no_user_statement_dispatched")
            exc.completion = handle.completion
            raise

    async def batches(self, handle: RunningOperation):
        for row in handle.driver_state.get("rows", []):
            yield RowBatch(handle.columns, [row])

    async def request_stop(self, connection: ConnectionSpec, backend_ref: dict, *, reason: str, deadline: float) -> StopConfirmation:
        cleanup = ControlCleanup()
        client_id, marker = backend_ref.get("client_id"), backend_ref.get("marker")
        if type(client_id) is not int or not isinstance(marker, str) or not marker.startswith("opspilot:"):
            return cleanup.result(False, "target_identity_unavailable", backend_ref, {"control_closed": True})
        client = _client(connection, backend_ref.get("database", 0), deadline, 65536)
        try:
            identity = (await bounded(client.info("server"), deadline)).get("run_id")
            if not backend_ref.get("server_run_id") or identity != backend_ref["server_run_id"]:
                return cleanup.result(False, "backend_identity_mismatch", backend_ref)
            async def find():
                rows = await bounded(client.client_list(client_id=[str(client_id)]), deadline)
                return next((row for row in rows if int(row["id"]) == client_id), None)
            row = await find()
            if row is None or row.get("name") != marker:
                return cleanup.result(True, "original_client_absent", backend_ref, {"source": "redis_client_list"})
            await bounded(client.client_kill_filter(_id=client_id, skipme=True), deadline)
            if await find() is None:
                return cleanup.result(True, reason, backend_ref, {"source": "redis_client_absent_after_kill"})
        except Exception as exc:
            return cleanup.result(False, "stop_not_confirmed", backend_ref, {"error_type": type(exc).__name__})
        finally:
            try:
                await client.aclose(close_connection_pool=True)
            except Exception as exc:
                raise cleanup.failed() from exc
            cleanup.closed()
        return cleanup.result(False, "stop_not_confirmed", backend_ref, {"control_closed": True})

    async def close(self, handle: RunningOperation) -> None:
        await handle.driver_state["client"].aclose(close_connection_pool=True)
