"""PostgreSQL 只读事务与单行游标，协议长度检查先于 DataRow 分配。"""
import asyncio
import struct
import uuid

import asyncpg

from app.database_adapters.base import (
    AdapterError, ConnectionSpec, PreparedOperation, ResultColumn, RowBatch,
    RunningOperation, StopConfirmation, bounded, completed, remaining, tls_context,
    validate_text_identifier, connection_not_established, ControlCleanup, local_closed_error,
)
from app.database_sql_policy import validate_sql


class BoundedPostgresProtocol(asyncio.Protocol):
    """只缓存五字节消息头；超限包不交给 asyncpg 的行解码缓冲。"""

    def __init__(self, protocol, transport, limit_bytes: int):
        self.protocol, self.transport, self.limit_bytes = protocol, transport, limit_bytes
        self.header = bytearray()
        self.body_remaining = 0
        self.violation = None

    def data_received(self, data):
        if self.violation is not None:
            return
        position = 0
        while position < len(data):
            if self.body_remaining:
                count = min(self.body_remaining, len(data) - position)
                self.protocol.data_received(data[position:position + count])
                self.body_remaining -= count
                position += count
                continue
            count = min(5 - len(self.header), len(data) - position)
            self.header.extend(data[position:position + count])
            position += count
            if len(self.header) < 5:
                return
            length = struct.unpack("!I", self.header[1:])[0]
            if length < 4 or length > self.limit_bytes:
                self.violation = AdapterError("protocol_value_too_large", "目标返回单行超过安全读取上限")
                self.transport.abort()
                return
            self.protocol.data_received(bytes(self.header))
            self.header.clear()
            self.body_remaining = length - 4

    def connection_lost(self, exc):
        self.protocol.connection_lost(exc)

    def eof_received(self):
        return self.protocol.eof_received()

    def pause_writing(self):
        self.protocol.pause_writing()

    def resume_writing(self):
        self.protocol.resume_writing()


async def _connect(spec: ConnectionSpec, database, marker: str, deadline: float, *, read_only: bool = True):
    try:
        return await bounded(asyncpg.connect(
            host=spec.host, port=spec.port, user=spec.login_user, password=spec.secret,
            database=database or spec.options.get("default_database") or "postgres",
            ssl=tls_context(spec.options) or False, timeout=min(10, remaining(deadline)),
            command_timeout=remaining(deadline), statement_cache_size=0,
            server_settings={
                "application_name": marker,
                **({"default_transaction_read_only": "on"} if read_only else {}),
                "statement_timeout": str(max(1, int(remaining(deadline) * 1000))),
                "idle_in_transaction_session_timeout": str(max(1000, int(remaining(deadline) * 1000))),
            },
        ), deadline)
    except (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError, asyncpg.InvalidCatalogNameError) as exc:
        raise AdapterError("target_authentication_failed", "数据库认证或初始库权限失败",
                           StopConfirmation(True, "server_rejected_connection", {}, {"source": "postgres_error_response", "local_closed": True})) from exc
    except Exception as exc:
        error = connection_not_established(exc)
        if error is not None:
            raise local_closed_error(error) from exc
        if isinstance(exc, asyncpg.PostgresError) and not str(exc.sqlstate or "").startswith("08"):
            raise AdapterError("target_connection_failed", "目标拒绝建立数据库会话",
                               StopConfirmation(True, "server_rejected_connection", {}, {"source": "postgres_startup_error_response", "local_closed": True})) from exc
        raise


async def _server_identity(client, deadline):
    row = await bounded(client.fetchrow(
        "SELECT inet_server_addr()::text AS address,inet_server_port() AS port,pg_postmaster_start_time() AS postmaster_started"
    ), deadline)
    return {"address": row["address"], "port": row["port"], "postmaster_started": row["postmaster_started"].isoformat()}


class PostgreSQLAdapter:
    target_connection_cost = 1
    control_connection_cost = 1

    async def open_operation(self, connection: ConnectionSpec, prepared: PreparedOperation, *, deadline: float) -> RunningOperation:
        database = prepared.context.get("database") or connection.options.get("default_database") or "postgres"
        validate_text_identifier(database)
        marker = f"opspilot:{prepared.task_id}:{uuid.uuid4().hex}"
        client = await _connect(connection, database, marker, deadline, read_only=prepared.kind != "probe")
        try:
            guard = BoundedPostgresProtocol(client._protocol, client._transport, prepared.max_bytes + 65536)
            client._transport.set_protocol(guard)
            # 游标读取需要事务，连接探活使用普通事务而不强制设置只读。
            transaction = client.transaction() if prepared.kind == "probe" else client.transaction(readonly=True)
            await bounded(transaction.start(), deadline)
            await bounded(client.execute("SELECT set_config('application_name',$1,true)", marker), deadline)
            server_identity = await _server_identity(client, deadline)
            identity = await bounded(client.fetchrow(
                "SELECT pg_backend_pid() AS pid,backend_start FROM pg_stat_activity WHERE pid=pg_backend_pid()"
            ), deadline)
            return RunningOperation(
                {"pid": identity["pid"], "backend_start": identity["backend_start"].isoformat(), "marker": marker, "database": database, "server_identity": server_identity},
                driver_state={"client": client, "prepared": prepared, "deadline": deadline, "connection": connection, "guard": guard, "transaction": transaction},
            )
        except BaseException as exc:
            client.terminate()
            if isinstance(exc, Exception):
                raise local_closed_error(exc) from exc
            raise

    async def run(self, handle: RunningOperation) -> None:
        state = handle.driver_state
        prepared, deadline, client = state["prepared"], state["deadline"], state["client"]
        try:
            schema = prepared.context.get("schema") or state["connection"].options.get("default_schema")
            if schema and prepared.kind != "probe":
                validate_text_identifier(schema)
                await bounded(client.execute('SET LOCAL search_path TO "' + schema.replace('"', '""') + '"'), deadline)
            query, params = self._statement(prepared, state["connection"])
            statement = await bounded(client.prepare(f"/*{handle.backend_ref['marker']}*/ {query}"), deadline)
            handle.columns = [ResultColumn(index, item.name, item.type.name) for index, item in enumerate(statement.get_attributes())]
            state["cursor"] = statement.cursor(*params, prefetch=1).__aiter__()
            if prepared.kind == "probe":
                handle.summary = {"message": "连接和认证成功"}
        except asyncpg.PostgresError as exc:
            if state["guard"].violation is not None:
                raise state["guard"].violation from exc
            if str(exc.sqlstate or "").startswith("08") or isinstance(exc, (asyncpg.ConnectionDoesNotExistError, asyncpg.ConnectionFailureError)):
                raise
            completed(handle, "server_rejected_statement")
            code = "target_execution_timeout" if exc.sqlstate == "57014" else "target_query_failed"
            raise AdapterError(code, "数据库执行超过目标时限" if code.endswith("timeout") else "数据库拒绝查询或执行失败", handle.completion) from exc
        except ValueError as exc:
            completed(handle, "no_user_statement_dispatched")
            exc.completion = handle.completion
            raise

    def _statement(self, prepared: PreparedOperation, connection: ConnectionSpec):
        if prepared.kind == "sql":
            return validate_sql("postgresql", prepared.executed_statement or "").executed_statement, ()
        if prepared.kind == "probe":
            return "SELECT 1 AS connected", ()
        operation = prepared.parameters.get("operation")
        if operation == "databases":
            return "SELECT datname AS name FROM pg_database WHERE datallowconn AND has_database_privilege(datname,'CONNECT') ORDER BY datname", ()
        if operation == "schemas":
            return "SELECT schema_name AS name FROM information_schema.schemata ORDER BY schema_name", ()
        schema = prepared.context.get("schema") or connection.options.get("default_schema") or "public"
        if operation == "tables":
            return "SELECT table_name AS name,table_type AS type FROM information_schema.tables WHERE table_schema=$1 ORDER BY table_name", (schema,)
        if operation == "columns" and prepared.context.get("table"):
            return ("SELECT column_name AS name,data_type AS type,is_nullable AS nullable "
                    "FROM information_schema.columns WHERE table_schema=$1 AND table_name=$2 ORDER BY ordinal_position"), (schema, prepared.context["table"])
        raise AdapterError("unsupported_metadata", "该引擎不支持此元数据上下文")

    async def batches(self, handle: RunningOperation):
        state = handle.driver_state
        try:
            while True:
                try:
                    row = await bounded(state["cursor"].__anext__(), state["deadline"])
                except StopAsyncIteration:
                    completed(handle)
                    return
                yield RowBatch(handle.columns, [list(row)])
        except asyncpg.PostgresError as exc:
            if state["guard"].violation is not None:
                raise state["guard"].violation from exc
            if str(exc.sqlstate or "").startswith("08") or isinstance(exc, (asyncpg.ConnectionDoesNotExistError, asyncpg.ConnectionFailureError)):
                raise
            completed(handle, "server_rejected_statement")
            code = "target_execution_timeout" if exc.sqlstate == "57014" else "target_query_failed"
            raise AdapterError(code, "数据库执行超过目标时限" if code.endswith("timeout") else "数据库执行或结果读取失败", handle.completion) from exc
        except Exception as exc:
            if state["guard"].violation is not None:
                raise state["guard"].violation from exc
            raise

    async def request_stop(self, connection: ConnectionSpec, backend_ref: dict, *, reason: str, deadline: float) -> StopConfirmation:
        cleanup = ControlCleanup()
        pid, marker, started = backend_ref.get("pid"), backend_ref.get("marker"), backend_ref.get("backend_start")
        if type(pid) is not int or not marker or not started:
            return cleanup.result(False, "target_identity_unavailable", backend_ref, {"control_closed": True})
        client = None
        try:
            client = await _connect(connection, backend_ref.get("database"), "opspilot:control", deadline)
            await bounded(client.transaction(readonly=True).start(), deadline)
            identity = await _server_identity(client, deadline)
            if not backend_ref.get("server_identity") or identity != backend_ref["server_identity"]:
                return cleanup.result(False, "backend_identity_mismatch", backend_ref)
            row = await bounded(client.fetchrow(
                "SELECT backend_start,application_name,state FROM pg_stat_activity WHERE pid=$1 AND usename=current_user", pid
            ), deadline)
            if row is None:
                return cleanup.result(True, "backend_absent", backend_ref, {"source": "pg_stat_activity"})
            if row["backend_start"].isoformat() != started or row["application_name"] != marker:
                return cleanup.result(True, "original_backend_absent", backend_ref, {"source": "pg_stat_activity_identity"})
            if row["state"] != "active":
                return cleanup.result(True, "query_inactive", backend_ref, {"source": "pg_stat_activity"})
            await bounded(client.fetchval("SELECT pg_cancel_backend($1)", pid), deadline)
            for _ in range(20):
                # 控制连接为事务亲和性保持事务；每次复查前清掉统计快照。
                await bounded(client.execute("SELECT pg_stat_clear_snapshot()"), deadline)
                row = await bounded(client.fetchrow(
                    "SELECT backend_start,application_name,state FROM pg_stat_activity WHERE pid=$1 AND usename=current_user", pid
                ), deadline)
                if row is None or row["backend_start"].isoformat() != started or row["application_name"] != marker or row["state"] != "active":
                    return cleanup.result(True, reason, backend_ref, {"source": "postgres_query_inactive_after_cancel"})
                await bounded(asyncio.sleep(0.1), deadline)
        except Exception as exc:
            evidence = getattr(getattr(exc, "completion", None), "evidence", {})
            return cleanup.result(False, "stop_not_confirmed", backend_ref, {"control_closed": True} if evidence.get("local_closed") is True else {})
        finally:
            if client is not None:
                try:
                    await client.close(timeout=1)
                except Exception as exc:
                    raise cleanup.failed() from exc
                cleanup.closed()
        return cleanup.result(False, "stop_not_confirmed", backend_ref, {"control_closed": True})

    async def close(self, handle: RunningOperation) -> None:
        await handle.driver_state["client"].close(timeout=1)
