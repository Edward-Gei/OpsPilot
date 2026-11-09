"""MySQL 单连接非缓冲读取；控制连接仅核实并取消带唯一标记的查询。"""
import uuid

import asyncmy
from asyncmy.cursors import SSCursor
from asyncmy.protocol import MysqlPacket

from app.database_adapters.base import (
    AdapterError, ConnectionSpec, PreparedOperation, ResultColumn, RowBatch,
    RunningOperation, StopConfirmation, bounded, completed, remaining, tls_context,
    validate_text_identifier, connection_not_established, ControlCleanup, local_closed_error,
)
from app.database_sql_policy import validate_sql


class BoundedMySQLConnection(asyncmy.connection.Connection):
    """在驱动分配包体前验证长度，同时限制多包累计长度。"""

    def __init__(self, *, packet_limit: int, **kwargs):
        self.packet_limit = packet_limit
        self.packet_bytes = 0
        super().__init__(**kwargs)

    async def read_packet(self, packet_type=MysqlPacket):
        self.packet_bytes = 0
        return await super().read_packet(packet_type)

    async def _read_bytes(self, num_bytes: int):
        self.packet_bytes += num_bytes
        if self.packet_bytes > self.packet_limit:
            raise AdapterError("protocol_value_too_large", "目标返回单行超过安全读取上限")
        return await super()._read_bytes(num_bytes)

    async def _read_query_result(self, unbuffered=False):
        # asyncmy 0.2.9 仅捕获 Exception，取消时会遗留异步析构告警；
        # 专用连接在此显式接管结果，BaseException 也不触发排空。
        self._result = None
        result = asyncmy.connection.MySQLResult(self)
        try:
            if unbuffered:
                await result.init_unbuffered_query()
            else:
                await result.read()
        except BaseException:
            result.unbuffered_active = False
            result.connection = None
            raise
        self._result = result
        self._affected_rows = result.affected_rows
        if result.server_status:
            self.server_status = result.server_status


async def _connect(spec: ConnectionSpec, deadline: float, packet_limit: int, database=None):
    connection = BoundedMySQLConnection(
        packet_limit=packet_limit, host=spec.host, port=spec.port,
        user=spec.login_user, password=spec.secret, database=database,
        charset="utf8mb4", autocommit=True, local_infile=False,
        connect_timeout=min(10, remaining(deadline)), read_timeout=remaining(deadline),
        ssl=tls_context(spec.options), program_name="OpsPilot database executor",
    )
    try:
        await bounded(connection.connect(), deadline)
    except asyncmy.errors.MySQLError as exc:
        connection.close()
        error = connection_not_established(exc)
        if error is not None:
            raise local_closed_error(error) from exc
        if exc.args and exc.args[0] in (1044, 1045, 1049, 1129, 1130, 1251, 1524):
            raise AdapterError("target_authentication_failed", "数据库认证或初始库权限失败",
                               StopConfirmation(True, "server_rejected_connection", {}, {"source": "mysql_error_response", "local_closed": True})) from exc
        raise local_closed_error(exc) from exc
    except BaseException as exc:
        connection.close()
        error = connection_not_established(exc)
        if error is not None:
            raise local_closed_error(error) from exc
        if isinstance(exc, Exception):
            raise local_closed_error(exc) from exc
        raise
    return connection


class MySQLAdapter:
    target_connection_cost = 1
    control_connection_cost = 1

    async def open_operation(self, connection: ConnectionSpec, prepared: PreparedOperation, *, deadline: float) -> RunningOperation:
        database = prepared.context.get("database") or connection.options.get("default_database")
        if database is not None:
            validate_text_identifier(database)
        client = await _connect(connection, deadline, prepared.max_bytes + 65536, database)
        try:
            async with client.cursor() as cursor:
                # 业务读取保留只读事务；连接探活不要求目标支持只读设置。
                if prepared.kind != "probe":
                    await bounded(cursor.execute("START TRANSACTION READ ONLY"), deadline)
                await bounded(cursor.execute("SELECT CONNECTION_ID(),@@server_uuid"), deadline)
                row = await bounded(cursor.fetchone(), deadline)
            marker = f"opspilot:{prepared.task_id}:{uuid.uuid4().hex}"
            return RunningOperation(
                {"connection_id": int(row[0]), "marker": marker, "login_user": connection.login_user, "server_uuid": row[1]},
                driver_state={"client": client, "prepared": prepared, "deadline": deadline, "connection": connection},
            )
        except BaseException as exc:
            client.close()
            if isinstance(exc, Exception):
                raise local_closed_error(exc) from exc
            raise

    async def run(self, handle: RunningOperation) -> None:
        state = handle.driver_state
        prepared, deadline, client = state["prepared"], state["deadline"], state["client"]
        cursor = client.cursor(SSCursor)
        state["cursor"] = cursor
        try:
            await bounded(cursor.execute(f"SET SESSION MAX_EXECUTION_TIME={max(1, int(remaining(deadline) * 1000))}"), deadline)
            query, params = self._statement(prepared, state["connection"])
            query = f"/*{handle.backend_ref['marker']}*/ {query}"
            await bounded(cursor.execute(query, params), deadline)
            handle.columns = [ResultColumn(index, item[0], str(item[1])) for index, item in enumerate(cursor.description or ())]
            if prepared.kind == "probe":
                handle.summary = {"message": "连接和认证成功"}
        except asyncmy.errors.MySQLError as exc:
            if not exc.args or not isinstance(exc.args[0], int) or 2000 <= exc.args[0] < 3000:
                raise
            handle.completion = StopConfirmation(True, "server_rejected_statement", handle.backend_ref, {"source": "mysql_error_response", "code": exc.args[0] if exc.args else None})
            code = "target_execution_timeout" if exc.args[0] == 3024 else "target_query_failed"
            raise AdapterError(code, "数据库执行超过目标时限" if code.endswith("timeout") else "数据库拒绝查询或执行失败", handle.completion) from exc
        except (AdapterError, ValueError) as exc:
            if not getattr(exc, "code", "").startswith("protocol_"):
                completed(handle, "no_user_statement_dispatched")
                exc.completion = handle.completion
            raise

    def _statement(self, prepared: PreparedOperation, connection: ConnectionSpec):
        if prepared.kind == "sql":
            return validate_sql("mysql", prepared.executed_statement or "").executed_statement, None
        if prepared.kind == "probe":
            return "SELECT 1 AS connected", None
        operation = prepared.parameters.get("operation")
        database = prepared.context.get("database") or connection.options.get("default_database")
        if operation == "databases":
            return "SELECT SCHEMA_NAME AS name FROM information_schema.SCHEMATA ORDER BY SCHEMA_NAME", None
        if operation == "tables" and database:
            return "SELECT TABLE_NAME AS name,TABLE_TYPE AS type FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (database,)
        if operation == "columns" and database and prepared.context.get("table"):
            return ("SELECT COLUMN_NAME AS name,COLUMN_TYPE AS type,IS_NULLABLE AS nullable,COLUMN_KEY AS key_type "
                    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION"), (database, prepared.context["table"])
        raise AdapterError("unsupported_metadata", "该引擎不支持此元数据上下文")

    async def batches(self, handle: RunningOperation):
        cursor, deadline = handle.driver_state["cursor"], handle.driver_state["deadline"]
        try:
            while True:
                row = await bounded(cursor.fetchone(), deadline)
                if row is None:
                    completed(handle)
                    return
                yield RowBatch(handle.columns, [list(row)])
        except asyncmy.errors.MySQLError as exc:
            if not exc.args or not isinstance(exc.args[0], int) or 2000 <= exc.args[0] < 3000:
                raise
            handle.completion = StopConfirmation(True, "server_rejected_statement", handle.backend_ref, {"source": "mysql_error_response", "code": exc.args[0] if exc.args else None})
            code = "target_execution_timeout" if exc.args[0] == 3024 else "target_query_failed"
            raise AdapterError(code, "数据库执行超过目标时限" if code.endswith("timeout") else "数据库执行或结果读取失败", handle.completion) from exc

    async def request_stop(self, connection: ConnectionSpec, backend_ref: dict, *, reason: str, deadline: float) -> StopConfirmation:
        cleanup = ControlCleanup()
        pid, marker = backend_ref.get("connection_id"), backend_ref.get("marker")
        if type(pid) is not int or not isinstance(marker, str) or not marker.startswith("opspilot:"):
            return cleanup.result(False, "target_identity_unavailable", backend_ref, {"control_closed": True})
        client = None
        try:
            client = await _connect(connection, deadline, 65536)
            async with client.cursor() as cursor:
                await bounded(cursor.execute("START TRANSACTION READ ONLY"), deadline)
                await bounded(cursor.execute("SELECT @@server_uuid"), deadline)
                identity = await bounded(cursor.fetchone(), deadline)
                if not backend_ref.get("server_uuid") or identity is None or identity[0] != backend_ref["server_uuid"]:
                    return cleanup.result(False, "backend_identity_mismatch", backend_ref)
                await bounded(cursor.execute("SELECT ID,USER,COMMAND,INFO FROM information_schema.PROCESSLIST WHERE ID=%s", (pid,)), deadline)
                row = await bounded(cursor.fetchone(), deadline)
                if row is None or row[2] == "Sleep" or marker not in (row[3] or ""):
                    return cleanup.result(True, "marked_query_absent", backend_ref, {"source": "mysql_processlist"})
                if row[1] != connection.login_user:
                    return cleanup.result(False, "target_identity_mismatch", backend_ref)
                await bounded(cursor.execute(f"KILL QUERY {pid}"), deadline)
                for _ in range(20):
                    await bounded(cursor.execute("SELECT INFO FROM information_schema.PROCESSLIST WHERE ID=%s", (pid,)), deadline)
                    row = await bounded(cursor.fetchone(), deadline)
                    if row is None or marker not in (row[0] or ""):
                        return cleanup.result(True, reason, backend_ref, {"source": "mysql_marked_query_absent_after_kill"})
                    await bounded(__import__("asyncio").sleep(0.1), deadline)
        except Exception as exc:
            evidence = getattr(getattr(exc, "completion", None), "evidence", {})
            return cleanup.result(False, "stop_not_confirmed", backend_ref, {"control_closed": True} if evidence.get("local_closed") is True else {})
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception as exc:
                    raise cleanup.failed() from exc
                cleanup.closed()
        return cleanup.result(False, "stop_not_confirmed", backend_ref, {"control_closed": True})

    async def close(self, handle: RunningOperation) -> None:
        # SSCursor.close 会排空未读结果；直接关闭专用连接避免后台无界读取。
        handle.driver_state["client"].close()
        cursor = handle.driver_state.get("cursor")
        if cursor is not None:
            if cursor._result is not None:
                cursor._result.unbuffered_active = False
                cursor._result.connection = None
            cursor.connection = None
