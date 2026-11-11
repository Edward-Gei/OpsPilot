"""租约恢复与三类独立保留期清理，仅一个维护租约持有者处理。"""
from dataclasses import asdict
from datetime import datetime, timedelta
import uuid

from sqlalchemy import delete, func, select

from app.database_adapters import get_adapter
from app.database_adapters.base import StopConfirmation
from app.models.database_management import (DatabaseAuditEvent, DatabaseQuotaBucket, DatabaseResult,
    DatabaseResultChunk, DatabaseTask)
from app.services import config_service
from app.services.database_audit_service import append_audit_event
from app.services.database_quota_service import bucket, ClaimedTask, finish_task, TERMINAL_STATES
from app.services.database_execution_service import load_connection, stop_target, _settle


async def executor_heartbeat(factory, executor_id):
    """空闲期也落心跳；本地健康检查不依赖某一个目标库。"""
    async with factory() as session:
        async with session.begin():
            row = await bucket(session, "executor", executor_id)
            row.owner, row.heartbeat_at = executor_id, datetime.now()
            row.lease_expires_at = datetime.now()+timedelta(seconds=30)


async def _maintenance_lease(session, executor_id):
    row = await bucket(session, "maintenance", "all")
    now = datetime.now()
    if row.owner != executor_id and row.lease_expires_at and row.lease_expires_at > now:
        return False
    row.owner, row.token, row.heartbeat_at = executor_id, uuid.uuid4().hex, now
    row.lease_expires_at = now+timedelta(seconds=30)
    return True


async def run_maintenance_once(factory, executor_id):
    """只认领恢复控制权，不重放任何已尝试目标的用户操作。"""
    recoveries = []
    async with factory() as session:
        async with session.begin():
            # 先global，维护租约与常规结算的锁序一致。
            await bucket(session, "global", "all")
            if not await _maintenance_lease(session, executor_id):
                return
            now = datetime.now()
            queued_expired = list((await session.execute(select(DatabaseTask).where(
                DatabaseTask.status == "queued", DatabaseTask.queue_deadline_at <= now)
                .order_by(DatabaseTask.id).limit(100).with_for_update(skip_locked=True))).scalars())
            for task in queued_expired:
                task.stop_confirmation = {"confirmed": True, "reason": "not_started", "evidence": {"target_attempted": False}}
                await finish_task(session, task, "queue_expired", error_code="queue_expired", error_message="排队等待已过期")
            # 控制连接崩溃不能仅凭租约归零；保留其物理占用和任务身份供审计/恢复。
            lost_controls = list((await session.execute(select(DatabaseQuotaBucket).where(
                DatabaseQuotaBucket.scope == "control", DatabaseQuotaBucket.active_count > 0,
                DatabaseQuotaBucket.lease_expires_at <= now).with_for_update())).scalars())
            for control in lost_controls:
                control.owner = ("recovering:" + (control.owner or "unknown"))[:64]
                control.lease_expires_at = None
                task = await session.get(DatabaseTask, int(control.scope_key)) if control.scope_key.isdigit() else None
                if task:
                    await append_audit_event(session, task, "control_recovering", validation={
                        "confirmed": False, "control_token": control.token, "control_sockets": control.control_sockets})
            expired = list((await session.execute(select(DatabaseTask).where(
                DatabaseTask.status.in_(("running", "stopping", "recovering")),
                DatabaseTask.lease_expires_at <= now).order_by(DatabaseTask.id).limit(20)
                .with_for_update(skip_locked=True))).scalars())
            for task in expired:
                if task.target_attempted_at is None:
                    task.stop_confirmation = {"confirmed": True, "reason": "not_started", "evidence": {"target_attempted": False}}
                    await finish_task(session, task, "failed", error_code="executor_lost", error_message="执行器失联，目标操作尚未开始")
                    continue
                task.status, task.executor_id = "recovering", executor_id
                task.lease_token = uuid.uuid4().hex
                task.lease_expires_at = now+timedelta(seconds=30)
                task.stop_reason = task.stop_reason or "executor_lost"
                await append_audit_event(session, task, "recovery_claimed")
                recoveries.append(ClaimedTask(task.id, task.lease_token, executor_id,
                    task.deadline_at, task.policy_snapshot, task.quota_snapshot))
    for claim in recoveries:
        async with factory() as session:
            task = await session.get(DatabaseTask, claim.task_id)
            backend_ref, reason = task.backend_ref or {}, task.stop_reason
            closed_probe = task.kind == "probe" and backend_ref.get("local_cleanup", {}).get("closed") is True
            if closed_probe:
                # 固定探测已无本地资源，不为补结算再建立目标或控制连接。
                connection, adapter = None, None
            else:
                try:
                    connection = await load_connection(session, task, recheck=False)
                    adapter = get_adapter(task.engine)
                except Exception:
                    continue
        confirmation = (StopConfirmation(False, "probe_remote_stop_unconfirmed", backend_ref, {"local_closed": True})
            if closed_probe else await stop_target(factory, connection, adapter, backend_ref, reason))
        if confirmation.confirmed or closed_probe:
            status = "cancelled" if reason == "user_cancel" else "timed_out" if reason == "execution_timeout" else "failed"
            await _settle(factory, claim, status, confirmation, error_code=reason,
                          error_message="已确认探测本地资源关闭，恢复结算完成" if closed_probe else "已确认目标操作结束，恢复结算完成")
    await cleanup_retention(factory, executor_id)


async def cleanup_retention(factory, executor_id):
    """结果、历史、审计按各自时钟清理；活动任务和未到期结果始终保留。"""
    async with factory() as session:
        async with session.begin():
            global_row = await bucket(session, "global", "all")
            lease = await bucket(session, "maintenance", "all")
            if lease.owner != executor_id or not lease.lease_expires_at or lease.lease_expires_at <= datetime.now():
                return
            now = datetime.now()
            expired_results = list((await session.execute(select(DatabaseResult).where(
                DatabaseResult.expires_at <= now).limit(100).with_for_update(skip_locked=True))).scalars())
            for result in expired_results:
                if not result.storage_released:
                    global_row.payload_bytes = max(0, global_row.payload_bytes-result.stored_bytes)
                    result.storage_released = True
                await session.execute(delete(DatabaseResultChunk).where(DatabaseResultChunk.result_id == result.id))
                await session.delete(result)
            history_days = await config_service.get_config(session, "database.history_retention_days")
            audit_days = await config_service.get_config(session, "database.audit_retention_days")
            historic_tasks = select(DatabaseTask.id).where(DatabaseTask.status.in_(TERMINAL_STATES),
                DatabaseTask.finished_at <= now-timedelta(days=history_days),
                ~select(DatabaseResult.id).where(DatabaseResult.task_id == DatabaseTask.id).correlate(DatabaseTask).exists()).limit(100)
            ids = list((await session.execute(historic_tasks)).scalars())
            if ids:
                await session.execute(delete(DatabaseTask).where(DatabaseTask.id.in_(ids)))
            # 仅终态事件时间决定整个事件组到期，不依赖已经删除的 task。
            expired_groups = select(DatabaseAuditEvent.task_id).group_by(DatabaseAuditEvent.task_id).having(
                func.max(DatabaseAuditEvent.terminal_at) <= now-timedelta(days=audit_days)).limit(100)
            groups = list((await session.execute(expired_groups)).scalars())
            if groups:
                await session.execute(delete(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id.in_(groups)))
