"""持久提交与本人任务访问；API 不接触目标数据库。"""
from datetime import datetime, timedelta
import hashlib
import json
import uuid

from sqlalchemy import and_, case, cast, func, literal, literal_column, Numeric, or_, select, String

from app.core.response import BizError, Errors
from app.core.security import encrypt_text, decrypt_text
from app.database_sql_policy import validate_sql
from app.models.database_management import DatabaseAuditEvent, DatabaseInstance, DatabaseResult, DatabaseTask
from app.models.job import Credential
from app.schemas.database_management import TaskSubmission
from app.services import config_service
from app.services.database_authorization_service import require_permission, require_query_instance
from app.services.database_instance_service import ACTIVE_STATES, get_instance
from app.services.database_audit_service import append_audit_event
from app.services.database_quota_service import bucket, finish_task, policy, TERMINAL_STATES
from app.services.database_result_service import canonical
from app.services.list_search import label_matches

HISTORY_ENGINE_LABELS = {"mysql": "MySQL", "postgresql": "PostgreSQL", "mongodb": "MongoDB", "clickhouse": "ClickHouse", "redis": "Redis"}
HISTORY_KIND_LABELS = {"sql": "SQL 查询", "mongo_read": "MongoDB 查询", "redis_read": "Redis 读取", "metadata": "结构浏览", "probe": "连接测试"}
HISTORY_STATUS_LABELS = {"queued": "排队中", "running": "执行中", "stopping": "取消处理中", "recovering": "停止待确认",
    "succeeded": "已完成", "failed": "失败", "cancelled": "已取消", "timed_out": "已超时", "rejected": "未执行", "queue_expired": "排队过期"}


def _context(req):
    return req.context.model_dump(by_alias=True, exclude_none=True) if hasattr(req, "context") else {}


def prepare_request(instance, req):
    """按引擎解释已通过严格格式校验的用户输入，不放宽原生入口。"""
    context = _context(req)
    if req.kind == "probe":
        return context, {}, None, "连接状态检测"
    if req.kind == "sql":
        if instance.engine not in ("mysql", "postgresql", "clickhouse"):
            raise ValueError("该引擎不支持 SQL 查询")
        if context.get("schema") and instance.engine != "postgresql":
            raise ValueError("仅 PostgreSQL 支持 Schema 上下文")
        parsed = validate_sql(instance.engine, req.sql)
        return context, {}, parsed.executed_statement, f"执行第 {parsed.selected_statement_index+1}/{parsed.statement_count} 条 SELECT"
    if req.kind == "metadata":
        allowed = {
            "mysql": {"databases", "tables", "columns"}, "postgresql": {"databases", "schemas", "tables", "columns"},
            "clickhouse": {"databases", "tables", "columns"}, "mongodb": {"databases", "collections", "fields"},
            "redis": set(),
        }
        if req.operation not in allowed[instance.engine]:
            raise ValueError("该引擎不支持此元数据操作")
        if req.operation == "columns" and not context.get("table"):
            raise ValueError("查询列结构需要 table")
        if req.operation == "fields" and not context.get("collection"):
            raise ValueError("采样字段需要 collection")
        return context, {"operation": req.operation}, None, f"元数据：{req.operation}"
    expected = {"mongo_read": "mongodb", "redis_read": "redis"}
    if expected.get(req.kind) != instance.engine:
        raise ValueError("任务类型与数据库引擎不匹配")
    operation = req.operation.model_dump(exclude_none=True)
    from app.database_adapters.native_policy import validate_native_operation
    validate_native_operation(instance.engine, context, operation)
    return context, operation, None, f"只读操作：{operation['type']}"


async def _denied_audit(session, actor, instance_id, reason):
    """未授权请求只留安全身份，不复制未获授权的实例详情。"""
    event = DatabaseAuditEvent(task_id=uuid.uuid4().int >> 65, event_type="denied", actor_id=actor.user_id,
        actor_name=actor.username, source_ip=actor.source_ip, instance_id=None, engine=None,
        target_snapshot={"kind": "denied", "status": "rejected", "operation_summary": "未授权操作"},
        credential_snapshot={}, context={}, validation={"executed": False},
        error_code="access_denied", error_message="实例不存在或未授权", terminal_at=datetime.now())
    session.add(event)
    await session.commit()


async def submit_task(session, actor, req):
    """提交前持久化任务与起始审计；规则拒绝也先 commit 后返回422。"""
    try:
        if req.kind == "probe":
            await require_permission(session, actor.user_id, "database:write")
            instance = await get_instance(session, req.instance_id)
        else:
            instance = await require_query_instance(session, actor.user_id, req.instance_id, require_enabled=False)
    except BizError:
        await _denied_audit(session, actor, req.instance_id, "access_denied")
        raise
    limits = await policy(session)
    # 与删除和认领统一先锁 global，再锁实例和用户计量。
    global_row = await bucket(session, "global", "all")
    instance = await get_instance(session, req.instance_id, lock=True)
    credential = await session.get(Credential, instance.credential_id) if instance.credential_id is not None else None
    instance_row = await bucket(session, "instance", instance.id)
    user_row = await bucket(session, "user", actor.user_id)
    submitted = req.sql if req.kind == "sql" else canonical(req.model_dump(by_alias=True, exclude_none=True))
    raw = submitted.encode("utf-8")
    bounded_input = len(raw) <= 65536
    task = DatabaseTask(owner_id=actor.user_id, actor_name=actor.username, source_ip=actor.source_ip,
        instance_id=instance.id, kind=req.kind, engine=instance.engine, context=_context(req),
        target_snapshot={"id": instance.id, "name": instance.name, "engine": instance.engine,
            "environment": instance.environment, "host": instance.host, "port": instance.port},
        credential_snapshot={"id": credential.id, "name": credential.name, "login_user": credential.login_user,
            "updated_at": credential.updated_at.isoformat()} if credential else {},
        credential_id=instance.credential_id, credential_updated_at=credential.updated_at if credential else datetime.now(),
        config_revision=instance.config_revision, submitted_bytes=len(raw), submitted_sha256=hashlib.sha256(raw).hexdigest(),
        submitted_text_enc=encrypt_text(submitted) if bounded_input else None,
        status="queued", operation_summary="待校验", queue_deadline_at=datetime.now()+timedelta(seconds=limits["queue_timeout_seconds"]))
    session.add(task)
    await session.flush()
    reason, code, executed = None, None, None
    try:
        if not bounded_input:
            raise ValueError("输入超过 65536 UTF-8 字节；正文未留存")
        if instance.credential_id is None:
            raise ValueError("实例尚未配置数据库只读凭据")
        if not instance.enabled:
            raise ValueError("实例已停用")
        if credential is None or credential.auth_type != "username_password":
            raise ValueError("数据库凭据不存在或不兼容")
        context, parameters, executed, summary = prepare_request(instance, req)
        task.context, task.operation_summary = context, summary
        task.executed_statement_enc = encrypt_text(executed) if executed else None
        task.prepared_operation_enc = encrypt_text(canonical({"context": context, "parameters": parameters,
            "executed_statement": executed, "operation_summary": summary}))
        if global_row.queued_count >= limits["queue_max_tasks"] or user_row.queued_count >= limits["user_queue_max_tasks"]:
            code = "queue_full"
            raise ValueError("排队容量已满，请稍后手动提交")
    except (ValueError, TypeError) as exc:
        reason, code = str(exc)[:512], code or getattr(exc, "code", "input_rejected")
    except Exception as exc:
        # 策略异常只用受控 reason，未知异常阻断而不泄露 SQL/驱动资料。
        if hasattr(exc, "reason"):
            reason, code = str(exc.reason)[:512], getattr(exc, "code", "input_rejected")
        else:
            raise
    if reason:
        task.status, task.error_code, task.error_message = "rejected", code, reason
        task.finished_at, task.quota_released = datetime.now(), True
        await append_audit_event(session, task, "rejected", terminal=True,
                                 validation={"executed": False, "reason": reason, "body_saved": bounded_input})
    else:
        for row in (global_row, instance_row, user_row):
            row.queued_count += 1
        await append_audit_event(session, task, "submitted", validation={"executed": False})
    await session.commit()
    return TaskSubmission(task.id, task.status, executed, task.operation_summary,
        422 if reason else 202, Errors.BIZ_REJECTED if reason else 0, reason)


async def own_task(session, actor, task_id, *, include_expired=False):
    task = (await session.execute(select(DatabaseTask).where(DatabaseTask.id == task_id)
        .execution_options(populate_existing=True))).scalar_one_or_none()
    if task is None:
        raise Errors.not_found("任务不存在")
    if task.owner_id != actor.user_id:
        raise BizError(Errors.OBJECT_DENIED, "只能访问本人的任务", 403)
    if task.kind == "probe":
        await require_permission(session, actor.user_id, "database:write")
    else:
        await require_permission(session, actor.user_id, "database:query")
    days = await config_service.get_config(session, "database.history_retention_days")
    if not include_expired and task.finished_at and task.finished_at <= datetime.now()-timedelta(days=days):
        raise Errors.not_found("任务历史已过保留期")
    return task


async def task_view(session, task):
    result = (await session.execute(select(DatabaseResult).where(DatabaseResult.task_id == task.id))).scalar_one_or_none()
    return dict(task_id=task.id, kind=task.kind, status=task.status, instance_id=task.instance_id,
        instance_name=task.target_snapshot.get("name"), engine=task.engine, context=task.context,
        submitted_text=decrypt_text(task.submitted_text_enc) if task.submitted_text_enc else None,
        executed_statement=decrypt_text(task.executed_statement_enc) if task.executed_statement_enc else None,
        operation_summary=task.operation_summary, created_at=task.created_at, started_at=task.started_at,
        finished_at=task.finished_at, expires_at=result.expires_at if result else (task.result_summary or {}).get("expires_at"), stop_reason=task.stop_reason,
        error_code=task.error_code, error_message=task.error_message, stop_confirmation=task.stop_confirmation,
        result_summary=task.result_summary, policy_snapshot=task.policy_snapshot,
        target_snapshot=task.target_snapshot, credential_snapshot=task.credential_snapshot)


def _history_json_type(column, key, dialect):
    path = f'$."{key}"'
    return func.json_type(func.json_extract(column, path)) if dialect == "mysql" else func.json_type(column, path)


def _history_number(key, dialect):
    """仅搜索页面认可的非负 JSON 数字，不把字符串或布尔值转换成数量。"""
    value = DatabaseTask.result_summary[key].as_float()
    numeric = _history_json_type(DatabaseTask.result_summary, key, dialect).in_(("INTEGER", "DOUBLE", "integer", "real"))
    return case((and_(numeric, value >= 0), value), else_=None)


def _history_decimal(value, places, dialect):
    return (cast(cast(func.round(value, places), Numeric(20, places)), String) if dialect == "mysql"
            else func.printf(f"%.{places}f", value))


def _history_context(dialect):
    """只拼接入口允许的上下文字段，不能通过整个 JSON 搜到内部属性。"""
    pieces = []
    for key in ("database", "schema", "table", "collection"):
        value = DatabaseTask.context[key].as_string()
        if key == "database":
            value = case((DatabaseTask.engine == "redis", literal("DB ") + cast(value, String)), else_=value)
        pieces.append(case((and_(value.is_not(None), value != ""), value + " / "), else_=""))
    joined = pieces[0] + pieces[1] + pieces[2] + pieces[3]
    # 仅删除最后一个分隔符；MySQL LENGTH 按字节计数，截中文必须使用 CHAR_LENGTH。
    length = func.char_length(joined) if dialect == "mysql" else func.length(joined)
    return case((joined == "", "—"), else_=func.substr(joined, 1, length-3))


def _history_duration(now, dialect):
    end = func.coalesce(DatabaseTask.finished_at, now)
    seconds = (func.timestampdiff(literal_column("MICROSECOND"), DatabaseTask.started_at, end) / 1000000
               if dialect == "mysql" else (func.julianday(end)-func.julianday(DatabaseTask.started_at))*86400)
    unavailable = or_(DatabaseTask.started_at.is_(None), seconds < 0,
        and_(DatabaseTask.finished_at.is_(None), DatabaseTask.status.not_in(ACTIVE_STATES)))
    return case((unavailable, "—"), else_=_history_decimal(seconds, 3, dialect) + " s")


def _history_result(now, dialect):
    """复刻历史列表的非时间结果标签；过期/不可用时不再匹配隐藏的数量摘要。"""
    summary = DatabaseTask.result_summary
    rows, size = _history_number("returned_rows", dialect), _history_number("returned_bytes", dialect)
    row_text = func.format(rows, 0, "en_US") if dialect == "mysql" else func.printf("%,d", rows)
    size_text = case((size.is_(None), "—"), (size < 1024, cast(cast(size, Numeric(20, 0)), String) + " B"),
        (size < 1048576, _history_decimal(size / 1024, 1, dialect) + " KiB"),
        else_=_history_decimal(size / 1048576, 2, dialect) + " MiB")
    message = case((_history_json_type(summary, "message", dialect).in_(("STRING", "text")), summary["message"].as_string()), else_="连接测试")
    fallback_expiry = case((_history_json_type(summary, "expires_at", dialect).in_(("STRING", "text")),
        summary["expires_at"].as_string()), else_=None)
    has_expiry = or_(DatabaseResult.expires_at.is_not(None), and_(fallback_expiry.is_not(None), fallback_expiry != ""))
    # 写入方保存 canonical naive ISO；直接比较同一时钟的 ISO，SQLite CAST DATETIME 会丢失月日时间。
    expired = or_(DatabaseResult.expires_at <= now, and_(DatabaseResult.expires_at.is_(None), fallback_expiry <= now.isoformat()))
    truncated = and_(_history_json_type(summary, "truncated", dialect).in_(("BOOLEAN", "true")), summary["truncated"].as_boolean().is_(True))
    detail = case((truncated, "已截断 · "), else_="") + case((has_expiry, "有效至"), else_="按需查看结果")
    status_label = case(HISTORY_STATUS_LABELS, value=DatabaseTask.status, else_="")
    return case((DatabaseTask.kind == "probe", literal("不产生结果集 ") + message),
        (DatabaseTask.status != "succeeded", case((DatabaseTask.status.in_(ACTIVE_STATES), "结果暂不可用 "), else_="无可查看结果 ") + status_label),
        (expired, "结果已过期 操作详情继续保留"),
        else_=case((rows.is_(None), "结果摘要暂不可用"), else_=row_text + " 条 · " + size_text) + " " + detail)


def _history_keyword(keyword, now, dialect):
    fields = [literal("#") + cast(DatabaseTask.id, String), DatabaseTask.target_snapshot["name"].as_string(),
        _history_context(dialect), DatabaseTask.operation_summary, _history_duration(now, dialect), _history_result(now, dialect)]
    return or_(*[field.contains(keyword, autoescape=True) for field in fields],
        label_matches(DatabaseTask.engine, keyword, HISTORY_ENGINE_LABELS),
        label_matches(DatabaseTask.kind, keyword, HISTORY_KIND_LABELS),
        label_matches(DatabaseTask.status, keyword, HISTORY_STATUS_LABELS))


async def list_tasks(session, actor, *, page=1, page_size=20, instance_id=None, engine=None,
                     status=None, kind=None, started_from=None, started_to=None, keyword=None):
    now = datetime.now()
    perms = await current_task_permissions(session, actor)
    days = await config_service.get_config(session, "database.history_retention_days")
    query = select(DatabaseTask).where(DatabaseTask.owner_id == actor.user_id,
        (DatabaseTask.finished_at.is_(None)) | (DatabaseTask.finished_at > now-timedelta(days=days)))
    if "database:query" not in perms:
        query = query.where(DatabaseTask.kind == "probe")
    elif "database:write" not in perms:
        query = query.where(DatabaseTask.kind != "probe")
    for key, value in (("instance_id", instance_id), ("engine", engine), ("status", status), ("kind", kind)):
        if value is not None:
            query = query.where(getattr(DatabaseTask, key) == value)
    if started_from:
        query = query.where(DatabaseTask.created_at >= started_from)
    if started_to:
        query = query.where(DatabaseTask.created_at <= started_to)
    keyword = (keyword or "").strip()
    if keyword:
        query = query.outerjoin(DatabaseResult, DatabaseResult.task_id == DatabaseTask.id).where(
            _history_keyword(keyword, now, session.get_bind().dialect.name))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    tasks = (await session.execute(query.order_by(DatabaseTask.id.desc()).offset((page-1)*page_size).limit(page_size))).scalars()
    return {"items": [await task_view(session, task) for task in tasks], "total": total, "page": page, "page_size": page_size}


async def current_task_permissions(session, actor):
    from app.services.database_authorization_service import current_permissions
    perms = await current_permissions(session, actor.user_id)
    if not perms & {"database:query", "database:write"}:
        raise BizError(Errors.NO_PERM, "缺少数据库操作权限", 403)
    return perms


async def request_cancel(session, actor, task_id):
    await bucket(session, "global", "all")
    task = await own_task(session, actor, task_id)
    task = (await session.execute(select(DatabaseTask).where(DatabaseTask.id == task_id)
        .with_for_update().execution_options(populate_existing=True))).scalar_one()
    if task.status not in TERMINAL_STATES:
        if task.cancel_requested_at is None:
            task.cancel_requested_at, task.stop_reason = datetime.now(), "user_cancel"
            await append_audit_event(session, task, "cancel_requested")
        if task.status == "queued":
            task.stop_confirmation = {"confirmed": True, "reason": "not_started", "evidence": {"target_attempted": False}}
            await finish_task(session, task, "cancelled")
        elif task.status == "running":
            task.status = "stopping"
        await session.flush()
    return await task_view(session, task)
