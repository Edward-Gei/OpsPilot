"""任务独立审计：追加快照，无结果 payload 读取路径。"""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.core.security import decrypt_text
from app.core.response import Errors
from app.models.database_management import DatabaseAuditEvent
from app.services import config_service


async def append_audit_event(session, task, event_type, *, validation=None, terminal=False):
    """各状态转换与证据同事务提交，不依赖通用异步审计队列。"""
    event = DatabaseAuditEvent(
        task_id=task.id, event_type=event_type, actor_id=task.owner_id,
        actor_name=task.actor_name, source_ip=task.source_ip, instance_id=task.instance_id,
        instance_name=task.target_snapshot.get("name"), engine=task.engine,
        target_snapshot={**task.target_snapshot, "kind": task.kind, "status": task.status,
                         "operation_summary": task.operation_summary},
        credential_snapshot=task.credential_snapshot, context=task.context,
        submitted_text_enc=task.submitted_text_enc, executed_statement_enc=task.executed_statement_enc,
        submitted_bytes=task.submitted_bytes, submitted_sha256=task.submitted_sha256,
        validation=validation, stop_confirmation=task.stop_confirmation,
        result_summary=task.result_summary, error_code=task.error_code, error_message=task.error_message,
        terminal_at=task.finished_at if terminal else None,
    )
    session.add(event)
    await session.flush()
    return event


def _summary(first, last):
    return dict(id=first.task_id, task_id=first.task_id, kind=last.target_snapshot.get("kind"),
        status=last.target_snapshot.get("status"), actor_id=first.actor_id, actor_name=first.actor_name,
        source_ip=first.source_ip, instance_id=first.instance_id, instance_name=first.instance_name,
        engine=first.engine, context=first.context,
        operation_summary=last.target_snapshot.get("operation_summary", ""),
        created_at=first.created_at, finished_at=last.terminal_at, error_code=last.error_code,
        error_message=last.error_message, result_summary=last.result_summary)


async def list_audit(session, *, page=1, page_size=20, actor_id=None, keyword=None, source_ip=None,
                     instance_id=None, engine=None, database=None, kind=None, status=None,
                     started_from=None, started_to=None):
    """按独立事件组最后事实分页；历史清理后仍可审计。"""
    cutoff = datetime.now() - timedelta(days=await config_service.get_config(session, "database.audit_retention_days"))
    grouped = select(DatabaseAuditEvent.task_id, func.min(DatabaseAuditEvent.id).label("first_id"),
        func.max(DatabaseAuditEvent.id).label("last_id"), func.max(DatabaseAuditEvent.terminal_at).label("terminal_at")
    ).group_by(DatabaseAuditEvent.task_id).subquery()
    query = select(grouped.c.task_id).where(
        (grouped.c.terminal_at.is_(None)) | (grouped.c.terminal_at > cutoff))
    latest = select(DatabaseAuditEvent).where(DatabaseAuditEvent.id.in_(select(grouped.c.last_id)))
    for key, value in (("actor_id", actor_id), ("source_ip", source_ip), ("instance_id", instance_id), ("engine", engine)):
        if value is not None:
            latest = latest.where(getattr(DatabaseAuditEvent, key) == value)
    if keyword:
        latest = latest.where(DatabaseAuditEvent.instance_name.like(f"%{keyword}%") |
                              DatabaseAuditEvent.actor_name.like(f"%{keyword}%"))
    if database:
        latest = latest.where(DatabaseAuditEvent.context["database"].as_string() == database)
    if kind:
        latest = latest.where(DatabaseAuditEvent.target_snapshot["kind"].as_string() == kind)
    if status:
        latest = latest.where(DatabaseAuditEvent.target_snapshot["status"].as_string() == status)
    # 时间筛选与页面展示一致，取首次提交时间；末事件只决定当前状态。
    if started_from or started_to:
        first = select(DatabaseAuditEvent.id)
        if started_from:
            first = first.where(DatabaseAuditEvent.created_at >= started_from)
        if started_to:
            first = first.where(DatabaseAuditEvent.created_at <= started_to)
        query = query.where(grouped.c.first_id.in_(first))
    query = query.where(grouped.c.last_id.in_(latest.with_only_columns(DatabaseAuditEvent.id)))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    ids = list((await session.execute(query.order_by(grouped.c.task_id.desc()).offset((page-1)*page_size).limit(page_size))).scalars())
    items = []
    for task_id in ids:
        events = list((await session.execute(select(DatabaseAuditEvent).where(
            DatabaseAuditEvent.task_id == task_id).order_by(DatabaseAuditEvent.id))).scalars())
        items.append(_summary(events[0], events[-1]))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def audit_detail(session, task_id):
    events = list((await session.execute(select(DatabaseAuditEvent).where(
        DatabaseAuditEvent.task_id == task_id).order_by(DatabaseAuditEvent.id))).scalars())
    if not events:
        raise Errors.not_found("审计记录不存在")
    last = events[-1]
    cutoff = datetime.now() - timedelta(days=await config_service.get_config(session, "database.audit_retention_days"))
    if last.terminal_at and last.terminal_at <= cutoff:
        raise Errors.not_found("审计记录已过保留期")
    first = events[0]
    data = _summary(first, last)
    data.update(submitted_text=decrypt_text(first.submitted_text_enc) if first.submitted_text_enc else None,
                executed_statement=decrypt_text(last.executed_statement_enc) if last.executed_statement_enc else None,
                target_snapshot=first.target_snapshot, credential_snapshot=first.credential_snapshot,
                submitted_bytes=first.submitted_bytes, submitted_sha256=first.submitted_sha256)
    data["events"] = [{key: getattr(e, key) for key in ("id", "event_type", "created_at", "validation",
        "stop_confirmation", "error_code", "error_message", "result_summary")} for e in events]
    return data
