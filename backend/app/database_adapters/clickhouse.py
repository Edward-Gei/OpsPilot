"""ClickHouse HTTP 流读取，不缓冲完整响应；query_id 用于跨进程核实取消。"""
from datetime import date, datetime
from decimal import Decimal, localcontext
import json
import uuid

import httpx

from app.database_adapters.base import (
    AdapterError, ConnectionSpec, PreparedOperation, ResultColumn, RowBatch,
    RunningOperation, StopConfirmation, bounded, completed, remaining, validate_text_identifier, connection_not_established,
    ControlCleanup,
)
from app.database_sql_policy import validate_sql


def _client(spec: ConnectionSpec, deadline: float):
    scheme = "https" if spec.options.get("tls") else "http"
    host = f"[{spec.host}]" if ":" in spec.host else spec.host
    return httpx.AsyncClient(
        base_url=f"{scheme}://{host}:{spec.port}",
        auth=httpx.BasicAuth(spec.login_user or "", spec.secret),
        verify=spec.options.get("tls_verify", True), follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(remaining(deadline), connect=min(10, remaining(deadline))),
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
    )


async def _lines(response: httpx.Response, deadline: float, cap: int):
    buffer = bytearray()
    iterator = response.aiter_bytes(chunk_size=16384).__aiter__()
    while True:
        try:
            chunk = await bounded(iterator.__anext__(), deadline)
        except StopAsyncIteration:
            if buffer:
                yield bytes(buffer)
            return
        start = 0
        while start < len(chunk):
            newline = chunk.find(b"\n", start)
            end = len(chunk) if newline < 0 else newline
            if len(buffer) + end - start > cap:
                raise AdapterError("protocol_value_too_large", "目标返回单行超过安全读取上限")
            buffer.extend(chunk[start:end])
            if newline < 0:
                break
            yield bytes(buffer)
            buffer.clear()
            start = newline + 1


async def _control_response(client, query, query_id, deadline, cap=131072):
    """控制查询同样有界流读，KILL 输出可能包含原 SQL，不能无界缓冲。"""
    response = await bounded(client.send(
        client.build_request("POST", "/", params={
            "param_id": query_id, "max_execution_time": str(max(1, int(remaining(deadline)))),
            "max_result_rows": "1", "max_result_bytes": str(cap), "max_block_size": "1",
        }, content=query),
        stream=True,
    ), deadline)
    content = bytearray()
    try:
        iterator = response.aiter_bytes(chunk_size=16384).__aiter__()
        while True:
            try:
                chunk = await bounded(iterator.__anext__(), deadline)
            except StopAsyncIteration:
                break
            if len(content) + len(chunk) > cap:
                raise AdapterError("stop_not_confirmed", "目标控制响应超过安全上限")
            content.extend(chunk)
        return response.status_code, bytes(content)
    finally:
        await response.aclose()


def _decode_value(value, column_type):
    base = column_type
    while base.startswith(("Nullable(", "LowCardinality(")) and base.endswith(")"):
        base = base[base.index("(") + 1:-1]
    if value is None:
        return None
    if base.startswith(("UInt", "Int")):
        return int(value)
    if base.startswith("Decimal"):
        scale = int(base.rsplit(",", 1)[1].strip(" )")) if "," in base else int(base[base.index("(") + 1:-1])
        with localcontext() as context:
            context.prec = 76  # Decimal256 的最大精度，不能使用 Python 默认的 28 位。
            return Decimal(str(value)).quantize(Decimal(1).scaleb(-scale))
    if base.startswith(("Float", "BFloat")):
        return float(value)
    if base.startswith("DateTime") and isinstance(value, str):
        return datetime.fromisoformat(value)
    if base in ("Date", "Date32") and isinstance(value, str):
        return date.fromisoformat(value)
    return value


class ClickHouseAdapter:
    target_connection_cost = 1
    control_connection_cost = 1

    async def open_operation(self, connection: ConnectionSpec, prepared: PreparedOperation, *, deadline: float) -> RunningOperation:
        return RunningOperation(
            {"query_id": f"opspilot-{prepared.task_id}-{uuid.uuid4().hex}"},
            driver_state={"client": _client(connection, deadline), "prepared": prepared, "connection": connection, "deadline": deadline},
        )

    def _statement(self, prepared: PreparedOperation):
        if prepared.kind == "sql":
            return validate_sql("clickhouse", prepared.executed_statement or "").executed_statement, {}
        if prepared.kind == "probe":
            return "SELECT 1 AS connected", {}
        operation = prepared.parameters.get("operation")
        if operation == "databases":
            return "SELECT name FROM system.databases ORDER BY name", {}
        database = prepared.context.get("database")
        if operation == "tables" and database:
            return "SELECT name,engine AS type FROM system.tables WHERE database={db:String} ORDER BY name", {"param_db": database}
        if operation == "columns" and database and prepared.context.get("table"):
            return "SELECT name,type,default_kind,default_expression FROM system.columns WHERE database={db:String} AND table={table:String} ORDER BY position", {"param_db": database, "param_table": prepared.context["table"]}
        raise AdapterError("unsupported_metadata", "该引擎不支持此元数据上下文")

    async def run(self, handle: RunningOperation) -> None:
        state = handle.driver_state
        prepared, deadline = state["prepared"], state["deadline"]
        try:
            query, params = self._statement(prepared)
        except (AdapterError, ValueError) as exc:
            completed(handle, "no_user_statement_dispatched")
            exc.completion = handle.completion
            raise
        database = prepared.context.get("database") or state["connection"].options.get("default_database")
        if database:
            validate_text_identifier(database)
            params["database"] = database
        params.update({
            "query_id": handle.backend_ref["query_id"],
            "max_execution_time": str(max(1, int(remaining(deadline)))),
            "max_result_rows": str(prepared.max_rows + 1),
            "max_result_bytes": str(prepared.max_bytes + 65536),
            "result_overflow_mode": "break", "max_block_size": "1",
            "max_memory_usage": "268435456", "max_threads": "2",
            "output_format_json_quote_64bit_integers": "1",
            "output_format_json_validate_utf8": "0",
        })
        try:
            response = await bounded(state["client"].send(
                state["client"].build_request("POST", "/", params=params, content=(query + " FORMAT JSONCompactEachRowWithNamesAndTypes").encode("utf-8")),
                stream=True,
            ), deadline)
        except Exception as exc:
            error = connection_not_established(exc)
            if error is not None:
                raise error from exc
            raise
        state["response"] = response
        if response.status_code != 200:
            exception_code = response.headers.get("x-clickhouse-exception-code", "")
            if exception_code.isdecimal():
                handle.completion = StopConfirmation(True, "server_rejected_statement", handle.backend_ref, {"source": "clickhouse_exception_header", "code": int(exception_code)})
            code = "target_execution_timeout" if exception_code == "159" else "target_query_failed"
            raise AdapterError(code, "ClickHouse 执行超过目标时限" if code.endswith("timeout") else "ClickHouse 拒绝查询或执行失败", handle.completion)
        state["lines"] = _lines(response, deadline, prepared.max_bytes + 65536).__aiter__()
        try:
            names = json.loads(await bounded(state["lines"].__anext__(), deadline))
            types = json.loads(await bounded(state["lines"].__anext__(), deadline))
            if not isinstance(names, list) or not isinstance(types, list) or len(names) != len(types):
                raise ValueError
        except (ValueError, UnicodeError, StopAsyncIteration) as exc:
            raise AdapterError("invalid_target_response", "ClickHouse 返回了不支持的结果格式") from exc
        handle.columns = [ResultColumn(index, str(name), str(types[index])) for index, name in enumerate(names)]
        if prepared.kind == "probe":
            handle.summary = {"message": "连接和认证成功"}

    async def batches(self, handle: RunningOperation):
        state = handle.driver_state
        while True:
            try:
                line = await bounded(state["lines"].__anext__(), state["deadline"])
            except StopAsyncIteration:
                completed(handle)
                return
            if not line:
                continue
            try:
                # 严格 UTF-8，不能将非法二进制字符串默默替换成乱码。
                row = json.loads(line.decode("utf-8"), parse_float=Decimal)
                if not isinstance(row, list) or len(row) != len(handle.columns):
                    raise ValueError
                yield RowBatch(handle.columns, [[_decode_value(value, column.type) for column, value in zip(handle.columns, row)]])
            except (ValueError, TypeError, UnicodeError) as exc:
                raise AdapterError("unsupported_target_value", "ClickHouse 返回不支持的值或查询异常") from exc

    async def request_stop(self, connection: ConnectionSpec, backend_ref: dict, *, reason: str, deadline: float) -> StopConfirmation:
        cleanup = ControlCleanup()
        query_id = backend_ref.get("query_id")
        if not isinstance(query_id, str) or not query_id.startswith("opspilot-"):
            return cleanup.result(False, "target_identity_unavailable", backend_ref, {"control_closed": True})
        client = None
        try:
            client = _client(connection, deadline)
            status, content = await _control_response(
                client, b"KILL QUERY WHERE query_id={id:String} AND user=currentUser() SYNC FORMAT JSONEachRow",
                query_id, deadline,
            )
            if status == 200:
                for line in content.splitlines():
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get("query_id") == query_id and row.get("user") == connection.login_user and row.get("kill_status") == "finished":
                        return cleanup.result(True, reason, backend_ref, {"source": "clickhouse_exact_kill_sync_result"})
            # HTTP 代理可能把控制请求路由到另一台节点；零匹配不构成结束事实。
            status, content = await _control_response(
                client,
                b"SELECT query_id,user,type,exception_code FROM system.query_log "
                b"WHERE query_id={id:String} AND user=currentUser() "
                b"AND type IN ('QueryFinish','ExceptionBeforeStart','ExceptionWhileProcessing') "
                b"ORDER BY event_time_microseconds DESC LIMIT 1 FORMAT JSONCompactEachRow",
                query_id, deadline, cap=2048,
            )
            if status == 200:
                for line in content.splitlines():
                    row = json.loads(line)
                    if isinstance(row, list) and len(row) == 4 and row[0] == query_id and row[1] == connection.login_user and row[2] in ("QueryFinish", "ExceptionBeforeStart", "ExceptionWhileProcessing"):
                        return cleanup.result(True, reason, backend_ref, {"source": "clickhouse_exact_terminal_query_log", "event": row[2], "exception_code": row[3]})
        except Exception as exc:
            return cleanup.result(False, "stop_not_confirmed", backend_ref, {"error_type": type(exc).__name__})
        finally:
            if client is not None:
                try:
                    await client.aclose()
                except Exception as exc:
                    raise cleanup.failed() from exc
                cleanup.closed()
        return cleanup.result(False, "positive_stop_evidence_unavailable", backend_ref, {"control_closed": True})

    async def close(self, handle: RunningOperation) -> None:
        response = handle.driver_state.get("response")
        if response is not None:
            await response.aclose()
        await handle.driver_state["client"].aclose()
