"""目标操作执行与可信结算；未知停止保留现场，不自动重试查询。"""
import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
import json
import logging
import time

from sqlalchemy import select

from app.core.response import BizError
from app.core.security import decrypt_text
from app.database_adapters import get_adapter
from app.database_adapters.base import ConnectionSpec, PreparedOperation, StopConfirmation
from app.models.database_management import DatabaseQuotaBucket, DatabaseTask
from app.models.job import Credential
from app.services.database_authorization_service import require_permission, require_query_instance
from app.services.database_instance_service import get_instance
from app.services.database_audit_service import append_audit_event
from app.services.database_quota_service import (bucket, finish_task, TERMINAL_STATES,
    acquire_control, release_control)
from app.services.database_result_service import ResultBuilder, canonical, persist_result

logger = logging.getLogger("opspilot.database_executor")
LOCAL_CLOSE_TIMEOUT = 5


async def recheck_task(session, task):
    """目标调用前复查实时授权和连接身份，绝不静默换目标或账号。"""
    if task.kind == "probe":
        await require_permission(session, task.owner_id, "database:write")
        instance = await get_instance(session, task.instance_id)
        if not instance.enabled:
            raise ValueError("instance_disabled")
    else:
        instance = await require_query_instance(session, task.owner_id, task.instance_id)
    cred = await session.get(Credential, task.credential_id, populate_existing=True)
    if (instance.config_revision != task.config_revision or instance.credential_id != task.credential_id
        or cred is None or cred.updated_at != task.credential_updated_at
        or cred.login_user != task.credential_snapshot.get("login_user") or cred.auth_type != "username_password"):
        raise ValueError("instance_changed")
    return instance, cred


async def load_connection(session, task, *, recheck=True):
    if recheck:
        instance, cred = await recheck_task(session, task)
        options = instance.connection_options
        host, port = instance.host, instance.port
    else:
        # 恢复只使用原连接身份；凭据轮换后无法可靠核实时保留 recovering。
        cred = await session.get(Credential, task.credential_id)
        if not cred or cred.updated_at != task.credential_updated_at or cred.login_user != task.credential_snapshot.get("login_user"):
            raise ValueError("credential_changed")
        original = json.loads(decrypt_text(task.prepared_operation_enc))
        options = original.get("connection_options", {})
        host, port = task.target_snapshot["host"], task.target_snapshot["port"]
    return ConnectionSpec(engine=task.engine, host=host, port=port, credential_id=cred.id,
        login_user=cred.login_user, secret=decrypt_text(cred.secret_enc), options=options)


async def _owned_task(session, claim, *, lock=False):
    query = select(DatabaseTask).where(DatabaseTask.id == claim.task_id,
        DatabaseTask.lease_token == claim.lease_token,
        DatabaseTask.status.in_(("running", "stopping", "recovering")))
    if lock:
        query = query.with_for_update()
    return (await session.execute(query.execution_options(populate_existing=True))).scalar_one_or_none()


async def _watch(factory, claim):
    last_heartbeat = 0
    while True:
        await asyncio.sleep(0.5)
        async with factory() as session:
            task = await _owned_task(session, claim)
            if task is None:
                return "lease_lost"
            if task.cancel_requested_at:
                return "user_cancel"
            if time.monotonic() - last_heartbeat >= 5:
                now = datetime.now()
                # 条件更新而非持旧 ORM 回写，租约已失效后不能续活旧副本。
                from sqlalchemy import update
                changed = await session.execute(update(DatabaseTask).where(
                    DatabaseTask.id == claim.task_id, DatabaseTask.lease_token == claim.lease_token,
                    DatabaseTask.lease_expires_at > now,
                    DatabaseTask.status.in_(("running", "stopping"))).values(
                        heartbeat_at=now, lease_expires_at=now+timedelta(seconds=30)))
                await session.commit()
                if changed.rowcount != 1:
                    return "lease_lost"
                last_heartbeat = time.monotonic()


async def _persist_backend_ref(factory, claim, handle):
    """流式响应可能补充实际后端身份，取消与崩溃恢复使用同一份持久依据。"""
    async with factory() as session:
        async with session.begin():
            current = await _owned_task(session, claim, lock=True)
            if current is None:
                return False
            handle.backend_ref = {**handle.backend_ref, "instance_id": current.instance_id,
                                  "task_id": current.id, "executor_id": claim.executor_id}
            persisted_ref = {**handle.backend_ref, "local_cleanup": (current.backend_ref or {}).get("local_cleanup")}
            if current.backend_ref != persisted_ref:
                current.backend_ref = persisted_ref
                await append_audit_event(session, current, "target_identified")
    return True


async def _record_local_cleanup(factory, claim, closed):
    """旧租约仅能证明自己的连接已关闭，不能借此结算新持有者的任务。"""
    async with factory() as session:
        async with session.begin():
            await bucket(session, "global", "all")
            task = (await session.execute(select(DatabaseTask).where(DatabaseTask.id == claim.task_id)
                .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            cleanup = (task.backend_ref or {}).get("local_cleanup", {}) if task else {}
            if not task or cleanup.get("lease_token") != claim.lease_token:
                return False
            if cleanup.get("closed") is True:
                return True
            task.backend_ref = {**task.backend_ref, "local_cleanup": {**cleanup, "closed": closed}}
            await append_audit_event(session, task, "local_cleanup_confirmed" if closed else "local_cleanup_unconfirmed")
            if task.status == "recovering" and closed:
                task.lease_expires_at = datetime.now()
    return closed


async def _collect(adapter, handle, builder, persist_identity):
    await adapter.run(handle)
    if not await persist_identity():
        raise RuntimeError("lease_lost")
    builder.set_columns(handle.columns)
    async for batch in adapter.batches(handle):
        builder.set_columns(batch.columns)
        for row in batch.rows:
            if not builder.add(row):
                return "result_limit"
    # 适配器在空结果时也必须提供列和可信完成依据。
    builder.set_columns(handle.columns)
    return "completed"


async def stop_target(factory, connection, adapter, backend_ref, reason):
    """控制连接受共享配额限制；关闭本地连接不作为停止证据。"""
    cost = adapter.control_connection_cost
    instance_id, task_id = backend_ref.get("instance_id"), backend_ref.get("task_id")
    if instance_id is None or task_id is None:
        return StopConfirmation(False, "missing_target_identity", backend_ref)
    control_token = await acquire_control(factory, instance_id, cost, task_id=task_id, owner=backend_ref.get("executor_id"))
    if not control_token:
        return StopConfirmation(False, "control_capacity", backend_ref)
    confirmation = None
    try:
        try:
            confirmation = await asyncio.wait_for(adapter.request_stop(connection, backend_ref,
                reason=reason, deadline=time.monotonic()+5), timeout=6)
        except Exception as exc:
            confirmation = getattr(exc, "completion", None) or StopConfirmation(False, "stop_unconfirmed", backend_ref)
        return confirmation
    finally:
        # 目标停止与控制连接关闭是两个事实，缺少后者时保留其物理计量。
        if confirmation and confirmation.evidence.get("control_closed") is True:
            await release_control(factory, instance_id, cost, task_id=task_id, token=control_token)


async def _settle(factory, claim, status, confirmation, *, builder=None, error_code=None, error_message=None):
    async with factory() as session:
        async with session.begin():
            await bucket(session, "global", "all")
            task = await _owned_task(session, claim, lock=True)
            if task is None:
                return False
            task.stop_confirmation = asdict(confirmation) if confirmation else None
            cleanup = (task.backend_ref or {}).get("local_cleanup", {})
            local_unconfirmed = task.target_attempted_at is not None and cleanup.get("closed") is not True
            control = (await session.execute(select(DatabaseQuotaBucket).where(
                DatabaseQuotaBucket.scope == "control", DatabaseQuotaBucket.scope_key == str(task.id)))).scalar_one_or_none()
            control_unconfirmed = control is not None and control.active_count > 0
            if not confirmation or not confirmation.confirmed or local_unconfirmed or control_unconfirmed:
                task.status = "recovering"
                if local_unconfirmed:
                    task.error_code, task.error_message = "local_cleanup_unconfirmed", "执行器连接关闭尚未确认，保留资源占用"
                elif control_unconfirmed:
                    task.error_code, task.error_message = "control_cleanup_unconfirmed", "控制连接关闭尚未确认，保留资源占用"
                else:
                    task.error_code, task.error_message = error_code or "stop_unconfirmed", error_message or "目标停止尚未确认，保留恢复现场"
                # 未知本地/控制资源没有可自动重试的关闭证据；旧执行器完成清理才唤醒恢复。
                task.lease_expires_at = None if local_unconfirmed or control_unconfirmed else datetime.now()
                await append_audit_event(session, task, "recovering")
                return False
            result = None
            if task.cancel_requested_at and status in ("succeeded", "failed", "timed_out"):
                status, builder = "cancelled", None
                task.stop_reason = "user_cancel"
                error_code, error_message = "user_cancel", "用户取消查询"
            if status == "succeeded" and builder is not None:
                task.result_summary = {"returned_rows": len(builder.rows), "returned_bytes": builder.returned_bytes,
                    "truncated": builder.truncated, "truncate_reason": builder.truncate_reason}
                result = await persist_result(session, task, builder)
                task.result_summary = {**task.result_summary, "expires_at": result.expires_at.isoformat()}
            return await finish_task(session, task, status, error_code=error_code, error_message=error_message, result=result)


async def run_claimed_task(factory, claim):
    """处理一个认领任务；任何目标 await 都在平台事务之外。"""
    adapter, handle, runner, watcher = None, None, None, None
    outcome, opening_failure = None, None
    connection, builder = None, ResultBuilder(claim.policy_snapshot["max_result_rows"], claim.policy_snapshot["max_result_bytes"])
    deadline = time.monotonic()+max(0, (claim.deadline_at-datetime.now()).total_seconds())
    try:
        async with factory() as session:
            async with session.begin():
                await bucket(session, "global", "all")
                task = await _owned_task(session, claim, lock=True)
                if task is None:
                    return
                try:
                    connection = await load_connection(session, task)
                except (BizError, ValueError):
                    task.stop_reason = "instance_changed"
                    task.stop_confirmation = {"confirmed": True, "reason": "not_started", "evidence": {"target_attempted": False}}
                    await finish_task(session, task, "rejected", error_code="instance_changed", error_message="授权、实例状态或连接身份已变化，请重新提交")
                    return
                if task.cancel_requested_at:
                    task.stop_confirmation = {"confirmed": True, "reason": "not_started", "evidence": {"target_attempted": False}}
                    await finish_task(session, task, "cancelled")
                    return
                original = json.loads(decrypt_text(task.prepared_operation_enc))
                # 保存无秘密连接选项用于恢复，不使用更新后的实例配置。
                original["connection_options"] = connection.options
                from app.core.security import encrypt_text
                task.prepared_operation_enc = encrypt_text(canonical(original))
                prepared = PreparedOperation(task.id, task.kind, original["context"], original["parameters"],
                    original["executed_statement"], original["operation_summary"], builder.max_rows, builder.max_bytes)
                adapter = get_adapter(task.engine)
                task.backend_ref = {"local_cleanup": {"closed": False,
                    "executor_id": claim.executor_id, "lease_token": claim.lease_token}}
                task.target_attempted_at = datetime.now()
                await append_audit_event(session, task, "target_attempted")
        watcher = asyncio.create_task(_watch(factory, claim))
        handle = await asyncio.wait_for(adapter.open_operation(connection, prepared, deadline=deadline),
                                        timeout=max(0.001, deadline-time.monotonic()))
        if not await _persist_backend_ref(factory, claim, handle):
            return
        runner = asyncio.create_task(_collect(adapter, handle, builder,
            lambda: _persist_backend_ref(factory, claim, handle)))
        done, _ = await asyncio.wait((runner, watcher), timeout=max(0, deadline-time.monotonic()),
                                     return_when=asyncio.FIRST_COMPLETED)
        reason = runner.result() if runner in done else watcher.result() if watcher in done else "execution_timeout"
        if reason == "completed" and handle.completion and handle.completion.confirmed:
            outcome = ("succeeded", handle.completion, builder, None, None)
            return
        if reason == "lease_lost":
            return
        async with factory() as session:
            async with session.begin():
                current = await _owned_task(session, claim, lock=True)
                if current is None:
                    return
                current.status = "stopping"
                current.stop_reason = reason
                await append_audit_event(session, current, "stop_requested")
        confirmation = handle.completion
        if not confirmation or not confirmation.confirmed:
            await _persist_backend_ref(factory, claim, handle)
            confirmation = await stop_target(factory, connection, adapter, handle.backend_ref, reason)
        status = ("succeeded" if reason == "result_limit" else "timed_out" if reason == "execution_timeout"
                  else "cancelled" if reason == "user_cancel" else "failed")
        outcome = (status, confirmation, builder if status == "succeeded" else None,
            None if status == "succeeded" else reason,
            None if status == "succeeded" else "执行超时" if status == "timed_out"
            else "用户取消查询" if status == "cancelled" else "目标完成事实未能正常确认")
    except asyncio.CancelledError:
        # 进程优雅退出也必须通过目标取消事实结算，不能仅取消协程。
        if adapter and connection:
            confirmation = handle.completion if handle else None
            if not confirmation or not confirmation.confirmed:
                if handle:
                    await _persist_backend_ref(factory, claim, handle)
                confirmation = await stop_target(factory, connection, adapter, handle.backend_ref if handle else {}, "executor_shutdown")
            outcome = ("failed", confirmation, None, "executor_shutdown", "执行器停止")
        raise
    except Exception as exc:
        opening_failure = exc if handle is None else None
        confirmation = getattr(exc, "completion", None) or (handle.completion if handle else None)
        is_timeout = isinstance(exc, TimeoutError) or getattr(exc, "code", "") == "target_execution_timeout"
        failure_reason = "execution_timeout" if is_timeout else "execution_error"
        async with factory() as session:
            async with session.begin():
                current = await _owned_task(session, claim, lock=True)
                if current:
                    current.stop_reason = "user_cancel" if current.cancel_requested_at else failure_reason
        if adapter and connection and (not confirmation or not confirmation.confirmed):
            if handle:
                await _persist_backend_ref(factory, claim, handle)
            confirmation = await stop_target(factory, connection, adapter, handle.backend_ref if handle else {}, failure_reason)
        code = "execution_timeout" if is_timeout else str(getattr(exc, "code", "execution_error"))[:64]
        # driver 原始异常可能带查询/密码，绝不进入日志或 DTO。
        safe_message = str(getattr(exc, "reason", "目标操作失败；详细信息未回显"))[:512]
        outcome = ("timed_out" if is_timeout else "failed", confirmation, None, code, safe_message)
        logger.info("database task %s: %s", claim.task_id, code)
    finally:
        for pending in (watcher, runner):
            if pending and not pending.done():
                pending.cancel()
        await asyncio.gather(*[p for p in (watcher, runner) if p], return_exceptions=True)
        if adapter and handle:
            try:
                await asyncio.wait_for(adapter.close(handle), LOCAL_CLOSE_TIMEOUT)
            except Exception:
                await _record_local_cleanup(factory, claim, False)
                logger.info("database task %s: close_unconfirmed", claim.task_id)
            else:
                await _record_local_cleanup(factory, claim, True)
        elif opening_failure:
            evidence = getattr(getattr(opening_failure, "completion", None), "evidence", {})
            await _record_local_cleanup(factory, claim, evidence.get("local_closed") is True)
        if outcome:
            status, confirmation, result_builder, code, message = outcome
            await _settle(factory, claim, status, confirmation, builder=result_builder,
                          error_code=code, error_message=message)
