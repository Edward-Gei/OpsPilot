"""正式异步 PyMongo、固定直连单节点、批量一文档与受控原生只读查询。"""
import uuid

from bson import Binary, DBRef, Decimal128, MaxKey, MinKey, ObjectId, Regex, Timestamp
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure

from app.database_adapters.base import (
    AdapterError, ConnectionSpec, PreparedOperation, ResultColumn, RowBatch,
    RunningOperation, StopConfirmation, bounded, completed, remaining, connection_not_established,
    ControlCleanup, local_closed_error,
)
from app.database_adapters.native_policy import validate_native_operation


def _bson_value(value):
    """将 BSON 专属值显式标型，普通数字/日期/二进制交由统一 canonical 编码。"""
    if isinstance(value, dict):
        return {key: _bson_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_value(child) for child in value]
    if isinstance(value, ObjectId):
        return {"$type": "objectid", "value": str(value)}
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, Binary):
        return {"$type": "bson_binary", "subtype": value.subtype, "value": bytes(value)}
    if isinstance(value, Timestamp):
        return {"$type": "bson_timestamp", "value": f"{value.time}:{value.inc}"}
    if isinstance(value, Regex):
        return {"$type": "bson_regex", "value": value.pattern, "flags": int(value.flags)}
    if isinstance(value, DBRef):
        return {"$type": "bson_dbref", "collection": value.collection, "id": _bson_value(value.id), "database": value.database}
    if isinstance(value, MinKey):
        return {"$type": "bson_minkey", "value": "-1"}
    if isinstance(value, MaxKey):
        return {"$type": "bson_maxkey", "value": "1"}
    return value


def _check_document_budget(document, maximum):
    """BSON 本身有 16 MiB 硬上限；在额外 canonical 编码前拒绝超大单值。"""
    budget = maximum
    stack = [document]
    while stack:
        value = stack.pop()
        budget -= 16
        if budget < 0:
            raise AdapterError("protocol_value_too_large", "MongoDB 文档超过本次安全读取上限")
        if isinstance(value, dict):
            stack.extend(value.values())
            stack.extend(value.keys())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
        elif isinstance(value, str):
            if len(value) > budget:
                raise AdapterError("protocol_value_too_large", "MongoDB 单值超过本次安全读取上限")
            budget -= len(value.encode("utf-8"))
        elif isinstance(value, bytes):
            budget -= len(value)
        if budget < 0:
            raise AdapterError("protocol_value_too_large", "MongoDB 文档超过本次安全读取上限")


def _client(spec: ConnectionSpec, deadline: float):
    timeout = max(1, int(remaining(deadline) * 1000))
    kwargs = dict(
        host=spec.host, port=spec.port, username=spec.login_user, password=spec.secret,
        authSource=spec.options.get("auth_database") or "admin", directConnection=True,
        maxPoolSize=1, minPoolSize=0, maxConnecting=1, retryReads=False, retryWrites=False,
        connectTimeoutMS=min(10000, timeout), serverSelectionTimeoutMS=min(10000, timeout),
        socketTimeoutMS=timeout, timeoutMS=timeout, appname="OpsPilot database executor",
        tz_aware=True,
    )
    if spec.options.get("tls"):
        kwargs.update(tls=True, tlsAllowInvalidCertificates=not spec.options.get("tls_verify", True),
                      tlsAllowInvalidHostnames=not spec.options.get("tls_verify", True))
    return AsyncMongoClient(**kwargs)


class MongoDBAdapter:
    # PyMongo 每个直连 server 除池连接外最多有两条监控连接，均纳入额度。
    target_connection_cost = 3
    control_connection_cost = 3

    async def open_operation(self, connection: ConnectionSpec, prepared: PreparedOperation, *, deadline: float) -> RunningOperation:
        client = _client(connection, deadline)
        try:
            await bounded(client.admin.command("ping"), deadline)
            try:
                hello = await bounded(client.admin.command("hello"), deadline)
                server_process_id = str(hello.get("topologyVersion", {}).get("processId") or "") or None
            except OperationFailure:
                server_process_id = None
        except OperationFailure as exc:
            await client.close()
            raise AdapterError("target_authentication_failed", "MongoDB 认证或连接权限失败",
                               StopConfirmation(True, "server_rejected_connection", {}, {"source": "mongo_error_response", "code": exc.code, "local_closed": True})) from exc
        except BaseException as exc:
            await client.close()
            error = connection_not_established(exc)
            if error is not None:
                raise local_closed_error(error) from exc
            if isinstance(exc, Exception):
                raise local_closed_error(exc) from exc
            raise
        return RunningOperation(
            {"comment": f"opspilot:{prepared.task_id}:{uuid.uuid4().hex}", "server_process_id": server_process_id},
            driver_state={"client": client, "prepared": prepared, "deadline": deadline, "connection": connection},
        )

    async def run(self, handle: RunningOperation) -> None:
        state = handle.driver_state
        prepared, client, deadline = state["prepared"], state["client"], state["deadline"]
        try:
            if prepared.kind == "probe":
                handle.columns = [ResultColumn(0, "connected", "bool")]
                state["rows"] = [[True]]
                handle.summary = {"message": "连接和认证成功"}
                completed(handle)
                return
            milliseconds = max(1, int(remaining(deadline) * 1000))
            database = prepared.context.get("database") or state["connection"].options.get("default_database")
            marker = handle.backend_ref["comment"]
            if prepared.kind == "metadata":
                operation = prepared.parameters.get("operation")
                if operation == "databases":
                    state["cursor"] = await bounded(client.list_databases(authorizedDatabases=True, nameOnly=True, comment=marker, maxTimeMS=milliseconds), deadline)
                    handle.columns = [ResultColumn(0, "name", "string")]
                    state["mode"] = "database"
                elif operation == "collections" and database:
                    state["cursor"] = await bounded(client[database].list_collections(nameOnly=True, authorizedCollections=True, comment=marker), deadline)
                    handle.columns = [ResultColumn(0, "name", "string")]
                    state["mode"] = "collection"
                elif operation == "fields" and database and prepared.context.get("collection"):
                    state["cursor"] = client[database][prepared.context["collection"]].find({}, batch_size=1, comment=marker, max_time_ms=milliseconds).limit(1)
                    handle.columns = [ResultColumn(0, "name", "string"), ResultColumn(1, "type", "string"), ResultColumn(2, "inferred", "bool")]
                    state["mode"] = "fields"
                else:
                    raise AdapterError("unsupported_metadata", "该引擎不支持此元数据上下文")
                return
            validate_native_operation("mongodb", prepared.context, prepared.parameters)
            collection = client[prepared.context["database"]][prepared.context["collection"]]
            operation = prepared.parameters
            filter_value = operation.get("filter", {})
            if operation["type"] == "find":
                cursor = collection.find(filter_value, projection=operation.get("projection"), batch_size=1, comment=marker, max_time_ms=milliseconds)
                if operation.get("sort"):
                    cursor = cursor.sort([(item["field"], item["direction"]) for item in operation["sort"]])
                state["cursor"] = cursor.limit(min(operation.get("limit", 1000), prepared.max_rows + 1))
                handle.columns = [ResultColumn(0, "document", "bson")]
                state["mode"] = "document"
            elif operation["type"] == "count_documents":
                count = await bounded(collection.count_documents(filter_value, maxTimeMS=milliseconds, comment=marker), deadline)
                handle.columns, state["rows"] = [ResultColumn(0, "count", "int64")], [[count]]
                completed(handle)
            elif operation["type"] == "distinct":
                # distinct 命令会先构造完整数组；改为固定分组模板并受限游标读取。
                cursor = await bounded(collection.aggregate([
                    {"$match": filter_value}, {"$group": {"_id": "$" + operation["field"]}},
                    {"$limit": min(operation.get("limit", 1000), prepared.max_rows + 1)},
                ], allowDiskUse=False, batchSize=1, maxTimeMS=milliseconds, comment=marker), deadline)
                state["cursor"], state["mode"] = cursor, "distinct"
                handle.columns = [ResultColumn(0, "value", "bson")]
        except OperationFailure as exc:
            completed(handle, "server_rejected_statement")
            code = "target_execution_timeout" if exc.code == 50 else "target_query_failed"
            raise AdapterError(code, "MongoDB 执行超过目标时限" if code.endswith("timeout") else "MongoDB 拒绝操作或执行失败", handle.completion) from exc
        except AdapterError as exc:
            completed(handle, "no_user_statement_dispatched")
            exc.completion = handle.completion
            raise

    async def batches(self, handle: RunningOperation):
        state = handle.driver_state
        if "rows" in state:
            for row in state["rows"]:
                yield RowBatch(handle.columns, [row])
            return
        try:
            while True:
                try:
                    document = await bounded(state["cursor"].__anext__(), state["deadline"])
                except StopAsyncIteration:
                    completed(handle)
                    return
                try:
                    _check_document_budget(document, state["prepared"].max_bytes)
                except AdapterError as exc:
                    completed(handle, "server_batch_received")
                    exc.completion = handle.completion
                    raise
                if state["mode"] in ("database", "collection"):
                    rows = [[document["name"]]]
                elif state["mode"] == "fields":
                    rows = [[key, type(value).__name__, True] for key, value in document.items()]
                elif state["mode"] == "distinct":
                    rows = [[_bson_value(document.get("_id"))]]
                else:
                    rows = [[_bson_value(document)]]
                for row in rows:
                    yield RowBatch(handle.columns, [row])
        except OperationFailure as exc:
            completed(handle, "server_rejected_statement")
            code = "target_execution_timeout" if exc.code == 50 else "target_query_failed"
            raise AdapterError(code, "MongoDB 执行超过目标时限" if code.endswith("timeout") else "MongoDB 执行或结果读取失败", handle.completion) from exc

    async def request_stop(self, connection: ConnectionSpec, backend_ref: dict, *, reason: str, deadline: float) -> StopConfirmation:
        cleanup = ControlCleanup()
        marker = backend_ref.get("comment")
        if not isinstance(marker, str) or not marker.startswith("opspilot:"):
            return cleanup.result(False, "target_identity_unavailable", backend_ref, {"control_closed": True})
        client = _client(connection, deadline)
        try:
            hello = await bounded(client.admin.command("hello"), deadline)
            identity = str(hello.get("topologyVersion", {}).get("processId") or "")
            if not backend_ref.get("server_process_id") or identity != backend_ref["server_process_id"]:
                return cleanup.result(False, "backend_identity_mismatch", backend_ref)
            async def operations():
                cursor = await bounded(client.admin.aggregate([
                    {"$currentOp": {"allUsers": False, "idleConnections": False}},
                    {"$match": {"command.comment": marker}},
                    {"$project": {"opid": 1}}, {"$limit": 2},
                ], batchSize=1, maxTimeMS=max(1, int(remaining(deadline) * 1000))), deadline)
                return await bounded(cursor.to_list(length=2), deadline)
            found = await operations()
            if not found:
                return cleanup.result(True, "operation_comment_absent", backend_ref, {"source": "mongo_own_current_op"})
            for item in found:
                if type(item.get("opid")) is not int:
                    return cleanup.result(False, "unsupported_operation_identity", backend_ref)
                await bounded(client.admin.command("killOp", 1, op=item["opid"]), deadline)
            for _ in range(20):
                if not await operations():
                    return cleanup.result(True, reason, backend_ref, {"source": "mongo_own_operation_absent_after_kill"})
                await bounded(__import__("asyncio").sleep(0.1), deadline)
        except Exception:
            return cleanup.result(False, "stop_not_confirmed", backend_ref)
        finally:
            try:
                await client.close()
            except Exception as exc:
                raise cleanup.failed() from exc
            cleanup.closed()
        return cleanup.result(False, "stop_not_confirmed", backend_ref, {"control_closed": True})

    async def close(self, handle: RunningOperation) -> None:
        cursor = handle.driver_state.get("cursor")
        if cursor is not None:
            try:
                await bounded(cursor.close(), __import__("time").monotonic() + 1)
            except Exception:
                pass
        await handle.driver_state["client"].close()
