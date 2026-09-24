"""应用配置持久化远端任务：逐项接入及租约恢复。"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.config_providers import get_config_adapter
from app.config_providers.base import (
    DiscoverQuery, ProviderError, ProviderRejectedError, ProviderUnavailableError, ProviderWriteUncertainError, RemoteConfigResource,
    RemoteContent, RemoteExpectation,
)
from app.core.constants import (
    ConfigDriftStatus, ConfigFileStatus, ConfigProvider, ConfigTaskItemStatus,
    ConfigTaskKind, ConfigTaskStatus, ConfigVersionStatus,
)
from app.core.response import Errors
from app.core.security import decrypt_text, encrypt_text
from app.models.application_config import (
    ConfigFile,
    ConfigFileApplication,
    ConfigDraft,
    ConfigPlatformInstance,
    ConfigRemoteSnapshot,
    ConfigTask,
    ConfigTaskItem,
    ConfigVersion,
)
from app.schemas.application_config import ConfigFileSelection
from app.services import application_config_service as config_service
from app.services.application_config_service import ConfigActor
from app.services.config_content_service import redact_content, validate_and_normalize_content


_TASK_LEASE = timedelta(minutes=10)
_PUBLISH_RETRIES = 3


class _LeaseLost(Exception):
    pass


async def _assert_task_lease(session: AsyncSession, task: ConfigTask, token: str) -> None:
    """远端 I/O 返回后重新核对认领者，阻止过期 Worker 写入或结算。"""
    await session.refresh(task)
    now = datetime.now()
    if (task.lease_token != token or task.status != ConfigTaskStatus.RUNNING.value
            or task.lease_expires_at is None or task.lease_expires_at <= now):
        raise _LeaseLost()
    if task.config_file_id is None:
        return
    owner = (await session.execute(select(ConfigFile.operation_token, ConfigFile.operation_expires_at).where(
        ConfigFile.id == task.config_file_id,
    ))).one_or_none()
    if (owner is None or owner.operation_token != str(task.id)
            or owner.operation_expires_at is None or owner.operation_expires_at <= now):
        raise _LeaseLost()


async def discover_resources(
    session: AsyncSession,
    instance_id: int,
    query: dict[str, str],
) -> list[RemoteConfigResource]:
    """按显式范围发现远端资源，不隐式导入或持久化正文。"""
    instance = await config_service.get_platform_instance(session, instance_id)
    if not instance.enabled:
        raise Errors.rejected("配置平台实例已停用")
    connection = await config_service.resolve_platform_connection(session, instance)
    try:
        return await get_config_adapter(instance.provider).discover(connection, DiscoverQuery(values=query))
    except ProviderError:
        raise Errors.upstream("发现远端配置失败，请检查平台连通性及授权") from None


async def list_nacos_namespaces(session: AsyncSession, instance_id: int) -> list[dict[str, str]]:
    """按所选实例的凭据读取命名空间，仅返回选择器所需的名称与 ID。"""
    instance = await config_service.get_platform_instance(session, instance_id)
    if instance.provider != ConfigProvider.NACOS.value:
        raise Errors.param("所选平台实例不是 Nacos")
    if not instance.enabled:
        raise Errors.rejected("配置平台实例已停用")
    connection = await config_service.resolve_platform_connection(session, instance)
    try:
        return await get_config_adapter(instance.provider).list_namespaces(connection)
    except ProviderError:
        raise Errors.upstream("获取 Nacos 命名空间失败，请检查平台连通性及授权") from None


async def create_import_task(
    session: AsyncSession,
    instance_id: int,
    selections: list[ConfigFileSelection],
    actor: ConfigActor,
) -> ConfigTask:
    """多选接入仅创建任务；Worker 对每一项独立读取并落正式版本。"""
    instance = await config_service.get_platform_instance(session, instance_id)
    if not instance.enabled:
        raise Errors.rejected("配置平台实例已停用")
    task = ConfigTask(
        kind=ConfigTaskKind.IMPORT.value,
        platform_instance_id=instance_id,
        created_by=actor.id,
        actor_name=actor.name,
        source_ip=actor.source_ip,
        total_count=len(selections),
    )
    session.add(task)
    await session.flush()
    seen: set[str] = set()
    for selection in selections:
        locator, key = await config_service.normalize_locator(session, instance, selection.locator)
        await config_service._validate_content_format(instance.provider, selection.content_format)
        await config_service._ensure_approval_role(session, selection.approval_role_id)
        await config_service._validate_application_ids(session, selection.application_ids)
        if key in seen:
            raise Errors.param("本次接入选择了重复的远端定位器")
        seen.add(key)
        session.add(ConfigTaskItem(
            task_id=task.id,
            item_key=key,
            locator=locator,
            name=selection.name,
            content_format=selection.content_format,
            approval_role_id=selection.approval_role_id,
            application_ids=selection.application_ids,
        ))
    await session.flush()
    # MySQL 无 INSERT RETURNING，响应序列化前显式加载数据库生成的时间戳。
    await session.refresh(task)
    audit.log(module="config", action="import_task.create", actor_id=actor.id, actor_name=actor.name,
              source_ip=actor.source_ip, target_type="config_task", target_id=str(task.id),
              detail={"provider": instance.provider, "count": len(selections)})
    return task


async def get_config_task(session: AsyncSession, task_id: int) -> tuple[ConfigTask, list[ConfigTaskItem]]:
    task = await session.get(ConfigTask, task_id)
    if task is None:
        raise Errors.not_found("配置任务不存在")
    rows = await session.execute(select(ConfigTaskItem).where(ConfigTaskItem.task_id == task_id).order_by(ConfigTaskItem.id))
    return task, list(rows.scalars())


def task_to_dict(task: ConfigTask, items: list[ConfigTaskItem] | None = None) -> dict:
    """任务响应不暴露连接地址、凭据、租约和正文。"""
    data = {
        "id": task.id,
        "kind": task.kind,
        "config_file_id": task.config_file_id,
        "config_version_id": task.config_version_id,
        "status": task.status,
        "total_count": task.total_count,
        "success_count": task.success_count,
        "skipped_count": task.skipped_count,
        "failed_count": task.failed_count,
        "last_error": task.last_error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }
    if items is not None:
        data["items"] = [
            {"id": item.id, "name": item.name, "status": item.status,
             "config_file_id": item.config_file_id, "reason": item.reason}
            for item in items
        ]
    return data


async def process_next_config_task(session: AsyncSession) -> bool:
    """原子认领一项已排队或过期任务；处理失败不泄露厂商响应。"""
    now = datetime.now()
    task = (await session.execute(
        select(ConfigTask)
        .where(or_(
            (ConfigTask.status == ConfigTaskStatus.QUEUED.value) &
            (ConfigTask.next_attempt_at.is_(None) | (ConfigTask.next_attempt_at <= now)),
            (ConfigTask.status == ConfigTaskStatus.RUNNING.value) & (ConfigTask.lease_expires_at <= now),
        ))
        .order_by(ConfigTask.id).limit(1)
    )).scalar_one_or_none()
    if task is None:
        await session.rollback()
        return False
    token = str(uuid4())
    claimed = await session.execute(
        update(ConfigTask).where(
            ConfigTask.id == task.id,
            or_((ConfigTask.status == ConfigTaskStatus.QUEUED.value) &
                (ConfigTask.next_attempt_at.is_(None) | (ConfigTask.next_attempt_at <= now)),
                (ConfigTask.status == ConfigTaskStatus.RUNNING.value) & (ConfigTask.lease_expires_at <= now)),
        ).values(status=ConfigTaskStatus.RUNNING.value, lease_token=token,
                 lease_expires_at=now + _TASK_LEASE, started_at=task.started_at or now)
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return False
    task_id = task.id
    if task.config_file_id is not None:
        await session.execute(update(ConfigFile).where(
            ConfigFile.id == task.config_file_id, ConfigFile.operation_token == str(task_id),
        ).values(operation_expires_at=now + _TASK_LEASE))
    await session.execute(update(ConfigTaskItem).where(
        ConfigTaskItem.task_id == task_id,
        ConfigTaskItem.status == ConfigTaskItemStatus.RUNNING.value,
    ).values(status=ConfigTaskItemStatus.PENDING.value, lease_token=None))
    await session.commit()

    task, items = await get_config_task(session, task_id)
    if task.kind == ConfigTaskKind.IMPORT.value:
        for item in items:
            if item.status != ConfigTaskItemStatus.PENDING.value:
                continue
            await _process_import_item(session, task, item, token)
        await _finish_import_task(session, task_id, token)
    else:
        await _process_single_task(session, task_id, token)
    return True


async def _process_import_item(
    session: AsyncSession,
    task: ConfigTask,
    item: ConfigTaskItem,
    token: str,
) -> None:
    item_id = item.id
    item.status = ConfigTaskItemStatus.RUNNING.value
    item.lease_token = token
    item.started_at = datetime.now()
    await session.commit()
    try:
        instance = await config_service.get_platform_instance(session, task.platform_instance_id)
        if not instance.enabled:
            raise Errors.rejected("配置平台实例已停用")
        existing = (await session.execute(select(ConfigFile.id).where(
            ConfigFile.platform_instance_id == instance.id, ConfigFile.locator_key == item.item_key,
        ))).scalar_one_or_none()
        if existing is not None:
            item.status = ConfigTaskItemStatus.SKIPPED.value
            item.reason = "远端定位器已被管理"
        else:
            connection = await config_service.resolve_platform_connection(session, instance)
            remote = await get_config_adapter(instance.provider).read(connection, item.locator)
            await _assert_task_lease(session, task, token)
            if remote is None:
                raise Errors.rejected("远端配置不存在")
            normalized = validate_and_normalize_content(item.content_format, remote.content)
            if instance.provider == ConfigProvider.CONSUL.value and len(normalized.structured) > 64:
                raise Errors.rejected("Consul KV 前缀超过 64 项")
            config_file = ConfigFile(
                name=item.name,
                platform_instance_id=instance.id,
                locator=item.locator,
                locator_key=item.item_key,
                content_format=item.content_format,
                approval_role_id=item.approval_role_id,
                created_by=task.created_by,
            )
            session.add(config_file)
            await session.flush()
            snapshot = ConfigRemoteSnapshot(
                config_file_id=config_file.id, exists=True,
                content_enc=encrypt_text(normalized.canonical),
                provider_revision=remote.revision, observed_at=datetime.now(),
            )
            session.add(snapshot)
            await session.flush()
            version = ConfigVersion(
                config_file_id=config_file.id, version_no=1, source="external_import",
                status="published", content_enc=encrypt_text(normalized.canonical),
                base_snapshot_id=snapshot.id, published_at=datetime.now(),
            )
            session.add(version)
            await session.flush()
            config_file.current_version_id = version.id
            config_file.current_snapshot_id = snapshot.id
            config_file.latest_snapshot_id = snapshot.id
            config_file.last_synced_at = datetime.now()
            if instance.first_bound_at is None:
                instance.first_bound_at = datetime.now()
            for app_id in item.application_ids or []:
                session.add(ConfigFileApplication(config_file_id=config_file.id, application_id=app_id))
            item.config_file_id = config_file.id
            item.status = ConfigTaskItemStatus.SUCCESS.value
        item.lease_token = None
        item.finished_at = datetime.now()
        await session.commit()
    except _LeaseLost:
        await session.rollback()
    except Exception as exc:
        await session.rollback()
        item = await session.get(ConfigTaskItem, item_id)
        item.status = ConfigTaskItemStatus.FAILED.value
        item.reason = _safe_error(exc)
        item.lease_token = None
        item.finished_at = datetime.now()
        await session.commit()


async def _finish_import_task(session: AsyncSession, task_id: int, token: str) -> None:
    task, items = await get_config_task(session, task_id)
    if task.lease_token != token:
        await session.rollback()
        return
    task.success_count = sum(item.status == ConfigTaskItemStatus.SUCCESS.value for item in items)
    task.skipped_count = sum(item.status == ConfigTaskItemStatus.SKIPPED.value for item in items)
    task.failed_count = sum(item.status == ConfigTaskItemStatus.FAILED.value for item in items)
    task.status = (
        ConfigTaskStatus.PARTIAL_FAILED.value if task.failed_count and (task.success_count or task.skipped_count)
        else ConfigTaskStatus.FAILED.value if task.failed_count else ConfigTaskStatus.SUCCESS.value
    )
    task.finished_at = datetime.now()
    task.lease_token = None
    task.lease_expires_at = None
    await session.commit()
    audit.log(module="config", action="import_task.complete", actor_id=task.created_by,
              actor_name=task.actor_name, source_ip=task.source_ip, target_type="config_task",
              target_id=str(task.id), detail={"success_count": task.success_count,
                                              "skipped_count": task.skipped_count,
                                              "failed_count": task.failed_count})


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ProviderError):
        return f"平台操作失败: {exc.code}"
    if isinstance(exc, ValueError):
        return "远端配置正文格式不符合当前文件格式"
    return "配置任务处理失败"


async def _process_single_task(session: AsyncSession, task_id: int, token: str) -> None:
    task = await session.get(ConfigTask, task_id)
    if task.lease_token != token or task.status != ConfigTaskStatus.RUNNING.value:
        await session.rollback()
        return
    file_id = task.config_file_id
    version_id = task.config_version_id
    try:
        config_file = await config_service.get_config_file(session, file_id)
        if config_file.operation_token != str(task.id):
            raise Errors.conflict("配置文件任务所有权已变更")
        if config_file.status != ConfigFileStatus.ACTIVE.value:
            raise Errors.rejected("已归档配置文件不能操作远端")
        if (task.kind == ConfigTaskKind.PUBLISH.value and task.confirmed_snapshot_id is not None
                and (config_file.latest_snapshot_id != task.confirmed_snapshot_id
                     or config_file.drift_status not in {
                         ConfigDriftStatus.DRIFTED.value, ConfigDriftStatus.REMOTE_MISSING.value,
                     })):
            raise Errors.conflict("已确认的漂移快照失效，请重新同步并确认")
        instance = await config_service.get_platform_instance(session, config_file.platform_instance_id)
        if not instance.enabled:
            raise Errors.rejected("配置平台实例已停用")
        connection = await config_service.resolve_platform_connection(session, instance)
        adapter = get_config_adapter(instance.provider)
        if task.kind == ConfigTaskKind.SYNC.value:
            remote = await adapter.read(connection, config_file.locator)
            await _assert_task_lease(session, task, token)
            snapshot = await _persist_snapshot(session, config_file, remote)
            baseline = await session.get(ConfigRemoteSnapshot, config_file.current_snapshot_id) if config_file.current_snapshot_id else None
            config_file.drift_status = _drift_status(config_file.content_format, baseline, remote)
            config_file.latest_snapshot_id = snapshot.id
            config_file.last_synced_at = datetime.now()
            config_file.last_sync_error = None
            result = True
        elif task.kind == ConfigTaskKind.PUBLISH.value:
            version = await config_service.get_candidate(session, file_id, version_id)
            await _process_publish(session, task, config_file, version, adapter, connection, token)
            result = config_file.drift_status == ConfigDriftStatus.CLEAN.value
        else:
            raise Errors.rejected("未知配置任务类型")
        await _assert_task_lease(session, task, token)
        task.status = ConfigTaskStatus.SUCCESS.value if result else ConfigTaskStatus.FAILED.value
        task.success_count = 1 if result else 0
        task.failed_count = 0 if result else 1
        if not result:
            task.last_error = "远端内容与 OpsPilot 基线不一致"
        await _finish_single_task(session, task, config_file)
    except _LeaseLost:
        await session.rollback()
    except Exception as exc:
        await session.rollback()
        task = await session.get(ConfigTask, task_id)
        config_file = await session.get(ConfigFile, file_id)
        if task.lease_token != token:
            await session.rollback()
            return
        if (task.kind == ConfigTaskKind.PUBLISH.value and isinstance(exc, ProviderUnavailableError)
                and task.retry_count < _PUBLISH_RETRIES and config_file is not None
                and config_file.operation_token == str(task.id)):
            # 不确定的写入已记录 write_attempted；后续尝试只回读，绝不再次写入。
            task.retry_count += 1
            task.next_attempt_at = datetime.now() + timedelta(seconds=2 ** task.retry_count)
            task.status = ConfigTaskStatus.QUEUED.value
            task.lease_token = None
            task.lease_expires_at = None
            task.last_error = _safe_error(exc)
            await session.commit()
            return
        task.status = ConfigTaskStatus.FAILED.value
        task.failed_count = 1
        task.last_error = _safe_error(exc)
        if config_file is not None:
            if task.kind == ConfigTaskKind.SYNC.value and config_file.drift_status == ConfigDriftStatus.CLEAN.value:
                config_file.drift_status = ConfigDriftStatus.SYNC_FAILED.value
            config_file.last_sync_error = task.last_error
            if task.kind == ConfigTaskKind.PUBLISH.value and version_id:
                version = await session.get(ConfigVersion, version_id)
                if version and version.status == ConfigVersionStatus.PUBLISHING.value:
                    version.status = ConfigVersionStatus.APPROVED.value
        await _finish_single_task(session, task, config_file)


async def _process_publish(session, task, config_file, version, adapter, connection, token) -> None:
    """写入前比对任务确认或提交时快照；写入尝试前持久化标记，恢复时只回读。"""
    desired = validate_and_normalize_content(config_file.content_format, decrypt_text(version.content_enc))
    if version.status == ConfigVersionStatus.APPROVED.value:
        version.status = ConfigVersionStatus.PUBLISHING.value
        await session.commit()
    elif version.status not in {ConfigVersionStatus.PUBLISHING.value, ConfigVersionStatus.PUBLISHED.value}:
        raise Errors.rejected("版本当前不可发布")

    if task.write_attempted:
        readback = await adapter.read(connection, config_file.locator)
        await _assert_task_lease(session, task, token)
    else:
        remote = await adapter.read(connection, config_file.locator)
        await _assert_task_lease(session, task, token)
        baseline_id = task.confirmed_snapshot_id or version.base_snapshot_id
        baseline = await session.get(ConfigRemoteSnapshot, baseline_id) if baseline_id else None
        if _drift_status(config_file.content_format, baseline, remote) != ConfigDriftStatus.CLEAN.value:
            snapshot = await _persist_snapshot(session, config_file, remote)
            config_file.latest_snapshot_id = snapshot.id
            config_file.drift_status = _drift_status(config_file.content_format, baseline, remote)
            if version.status == ConfigVersionStatus.PUBLISHING.value:
                version.status = ConfigVersionStatus.APPROVED.value
            await session.commit()
            return
        task.write_attempted = True
        await session.commit()
        try:
            await adapter.write(
                connection, config_file.locator, desired,
                expect=RemoteExpectation.exists(remote) if remote else RemoteExpectation.missing(),
            )
        except ProviderWriteUncertainError:
            pass
        except ProviderRejectedError as exc:
            if exc.code not in {"remote_changed", "http_409"}:
                raise
        readback = await adapter.read(connection, config_file.locator)
        await _assert_task_lease(session, task, token)

    if readback is None or not _same_content(config_file.content_format, desired.canonical, readback.content):
        snapshot = await _persist_snapshot(session, config_file, readback)
        config_file.latest_snapshot_id = snapshot.id
        config_file.drift_status = (
            ConfigDriftStatus.REMOTE_MISSING.value if readback is None else ConfigDriftStatus.DRIFTED.value
        )
        if version.status == ConfigVersionStatus.PUBLISHING.value:
            version.status = ConfigVersionStatus.APPROVED.value
        return

    snapshot = await _persist_snapshot(session, config_file, readback)
    config_file.current_version_id = version.id
    config_file.current_snapshot_id = snapshot.id
    config_file.latest_snapshot_id = snapshot.id
    config_file.drift_status = ConfigDriftStatus.CLEAN.value
    config_file.last_synced_at = datetime.now()
    config_file.last_sync_error = None
    version.status = ConfigVersionStatus.PUBLISHED.value
    version.published_at = datetime.now()


async def _persist_snapshot(session, config_file, remote: RemoteContent | None) -> ConfigRemoteSnapshot:
    canonical = validate_and_normalize_content(config_file.content_format, remote.content).canonical if remote else None
    snapshot = ConfigRemoteSnapshot(
        config_file_id=config_file.id,
        exists=remote is not None,
        content_enc=encrypt_text(canonical) if canonical is not None else None,
        provider_revision=remote.revision if remote else None,
        observed_at=datetime.now(),
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


def _same_content(content_format: str, left: str, right: str) -> bool:
    return validate_and_normalize_content(content_format, left).canonical == validate_and_normalize_content(
        content_format, right,
    ).canonical


def _drift_status(content_format: str, baseline: ConfigRemoteSnapshot | None, remote: RemoteContent | None) -> str:
    if baseline is None:
        return ConfigDriftStatus.REMOTE_MISSING.value if remote is None else ConfigDriftStatus.DRIFTED.value
    if not baseline.exists and remote is None:
        return ConfigDriftStatus.CLEAN.value
    if baseline.exists and remote is None:
        return ConfigDriftStatus.REMOTE_MISSING.value
    if not baseline.exists and remote is not None:
        return ConfigDriftStatus.DRIFTED.value
    if _same_content(content_format, decrypt_text(baseline.content_enc), remote.content):
        return ConfigDriftStatus.CLEAN.value
    return ConfigDriftStatus.DRIFTED.value


async def _finish_single_task(session: AsyncSession, task: ConfigTask, config_file: ConfigFile | None) -> None:
    if config_file is not None and config_file.operation_token == str(task.id):
        config_file.operation_token = None
        config_file.operation_kind = None
        config_file.operation_expires_at = None
    task.lease_token = None
    task.lease_expires_at = None
    task.finished_at = datetime.now()
    await session.commit()
    audit.log(module="config", action=f"task.{task.kind}.complete", actor_id=task.created_by,
              actor_name=task.actor_name, source_ip=task.source_ip, target_type="config_task",
              target_id=str(task.id), result="success" if task.status == "success" else "failed",
              detail={"config_file_id": task.config_file_id, "status": task.status})


async def _create_single_task(session, file_id: int, kind: str, actor: ConfigActor, version_id: int | None = None,
                              write_attempted: bool = False, confirmed_snapshot_id: int | None = None) -> ConfigTask:
    config_file = await config_service.get_config_file(session, file_id)
    if config_file.status != ConfigFileStatus.ACTIVE.value:
        raise Errors.rejected("已归档配置文件不能操作远端")
    instance = await config_service.get_platform_instance(session, config_file.platform_instance_id)
    if not instance.enabled:
        raise Errors.rejected("配置平台实例已停用")
    task = ConfigTask(
        kind=kind, config_file_id=file_id, config_version_id=version_id,
        confirmed_snapshot_id=confirmed_snapshot_id,
        platform_instance_id=instance.id, created_by=actor.id, actor_name=actor.name,
        source_ip=actor.source_ip, total_count=1, write_attempted=write_attempted,
    )
    session.add(task)
    await session.flush()
    now = datetime.now()
    lock = update(ConfigFile).where(
        ConfigFile.id == file_id,
        ConfigFile.status == ConfigFileStatus.ACTIVE.value,
        or_(ConfigFile.operation_token.is_(None), ConfigFile.operation_expires_at <= now),
    )
    if confirmed_snapshot_id is not None:
        lock = lock.where(ConfigFile.latest_snapshot_id == confirmed_snapshot_id)
    locked = await session.execute(lock.values(
        operation_token=str(task.id), operation_kind=kind, operation_expires_at=now + _TASK_LEASE,
    ))
    if locked.rowcount != 1:
        raise Errors.conflict("配置文件正在同步或发布")
    await session.flush()
    await session.refresh(task)
    audit.log(module="config", action=f"task.{kind}.create", actor_id=actor.id, actor_name=actor.name,
              source_ip=actor.source_ip, target_type="config_task", target_id=str(task.id),
              detail={"config_file_id": file_id, "version_id": version_id,
                      "confirmed_snapshot_id": confirmed_snapshot_id})
    return task


async def create_sync_task(session: AsyncSession, file_id: int, actor: ConfigActor) -> ConfigTask:
    """仅手动触发，不建立定时计划。"""
    return await _create_single_task(session, file_id, ConfigTaskKind.SYNC.value, actor)


async def create_publish_task(session: AsyncSession, file_id: int, version_id: int, actor: ConfigActor,
                              confirmed_snapshot_id: int | None = None) -> ConfigTask:
    version = await config_service.get_candidate(session, file_id, version_id)
    if version.status not in {ConfigVersionStatus.APPROVED.value, ConfigVersionStatus.PUBLISHED.value}:
        raise Errors.rejected("仅已批准候选或正式历史版本可发布")
    if confirmed_snapshot_id is not None:
        config_file = await config_service.get_config_file(session, file_id)
        if (config_file.drift_status not in {ConfigDriftStatus.DRIFTED.value, ConfigDriftStatus.REMOTE_MISSING.value}
                or config_file.latest_snapshot_id != confirmed_snapshot_id):
            raise Errors.conflict("远端快照已变化，请重新同步并确认漂移")
    previous = (await session.execute(select(ConfigTask).where(
        ConfigTask.config_file_id == file_id, ConfigTask.config_version_id == version_id,
        ConfigTask.kind == ConfigTaskKind.PUBLISH.value,
    ).order_by(ConfigTask.id.desc()).limit(1))).scalar_one_or_none()
    # 人工重试也必须继承不确定写入状态，只能回读，不能发出第二次写请求。
    uncertain = bool(confirmed_snapshot_id is None and previous and
                     previous.status == ConfigTaskStatus.FAILED.value and previous.write_attempted)
    return await _create_single_task(session, file_id, ConfigTaskKind.PUBLISH.value, actor, version_id,
                                     write_attempted=uncertain, confirmed_snapshot_id=confirmed_snapshot_id)


async def get_drift_view(session: AsyncSession, file_id: int, can_read_secret: bool, version_id: int | None = None) -> dict:
    config_file = await config_service.get_config_file(session, file_id)
    snapshot = await session.get(ConfigRemoteSnapshot, config_file.latest_snapshot_id) if config_file.latest_snapshot_id else None
    version = await session.get(ConfigVersion, version_id or config_file.current_version_id) if (version_id or config_file.current_version_id) else None
    if version is not None and version.config_file_id != file_id:
        raise Errors.not_found("配置版本不存在")
    if version is None:
        version = (await session.execute(select(ConfigVersion).where(
            ConfigVersion.config_file_id == file_id,
            ConfigVersion.status.in_(["approved", "publishing"]),
        ).order_by(ConfigVersion.id.desc()).limit(1))).scalar_one_or_none()
    def display(encrypted: str | None) -> str | None:
        if encrypted is None:
            return None
        return redact_content(
            validate_and_normalize_content(config_file.content_format, decrypt_text(encrypted)), can_read_secret,
        )
    return {
        "drift_status": config_file.drift_status,
        "latest_snapshot_id": snapshot.id if snapshot else None,
        "baseline_version_id": version.id if version else None,
        "baseline_content": display(version.content_enc) if version else None,
        "external_exists": bool(snapshot and snapshot.exists),
        "external_content": display(snapshot.content_enc) if snapshot else None,
    }


async def import_latest_external_snapshot(session: AsyncSession, file_id: int, actor: ConfigActor) -> ConfigVersion:
    """把最近一次漂移快照提升为正式版本，只更新 OpsPilot 内部状态。"""
    config_file = await config_service.get_config_file(session, file_id)
    if config_file.status != ConfigFileStatus.ACTIVE.value:
        raise Errors.rejected("已归档配置文件不能导入")
    if config_file.operation_token and config_file.operation_expires_at > datetime.now():
        raise Errors.conflict("配置文件正在同步或发布")
    snapshot = await session.get(ConfigRemoteSnapshot, config_file.latest_snapshot_id) if config_file.latest_snapshot_id else None
    if snapshot is None or not snapshot.exists or not snapshot.content_enc:
        raise Errors.rejected("外部资源缺失，不能导入")
    if config_file.drift_status != ConfigDriftStatus.DRIFTED.value:
        raise Errors.rejected("当前不存在可导入的漂移")
    next_no = (await session.execute(select(func.max(ConfigVersion.version_no)).where(
        ConfigVersion.config_file_id == file_id,
    ))).scalar_one()
    version = ConfigVersion(
        config_file_id=file_id, version_no=(next_no or 0) + 1,
        source="external_import", status=ConfigVersionStatus.PUBLISHED.value,
        content_enc=snapshot.content_enc, base_snapshot_id=snapshot.id,
        published_at=datetime.now(),
    )
    session.add(version)
    await session.flush()
    await session.execute(update(ConfigVersion).where(
        ConfigVersion.config_file_id == file_id,
        ConfigVersion.id != version.id,
        ConfigVersion.status.in_(["approved", "pending_approval"]),
    ).values(status="invalidated"))
    await session.execute(ConfigDraft.__table__.delete().where(ConfigDraft.config_file_id == file_id))
    config_file.current_version_id = version.id
    config_file.current_snapshot_id = snapshot.id
    config_file.drift_status = ConfigDriftStatus.CLEAN.value
    await session.flush()
    await session.refresh(version)
    audit.log(module="config", action="drift.import", actor_id=actor.id, actor_name=actor.name,
              source_ip=actor.source_ip, target_type="config_file", target_id=str(file_id),
              detail={"version_no": version.version_no})
    return version
