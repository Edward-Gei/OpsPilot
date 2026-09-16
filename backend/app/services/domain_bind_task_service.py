"""Zone 批量绑定任务的持久化入口与查询视图。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.core.constants import DnsBindTaskItemStatus, DnsBindTaskStatus, DnsSyncStatus, DomainProvider
from app.core.response import BizError, Errors
from app.models.domain import DnsZone, DnsZoneBindTask, DnsZoneBindTaskItem
from app.schemas.domain import ZoneBindResult, ZoneBindSelection
from app.services import domain_service


_TASK_LEASE_DURATION = timedelta(minutes=10)
_TERMINAL_ITEM_STATUSES = {
    DnsBindTaskItemStatus.SUCCESS.value,
    DnsBindTaskItemStatus.SKIPPED.value,
    DnsBindTaskItemStatus.FAILED.value,
}


@dataclass(frozen=True)
class _ZoneBindTaskContext:
    """任务认领后保留必要标量，避免下游回滚使 ORM 实例失效。"""

    id: int
    provider: str
    credential_id: int
    total_count: int
    created_by: int
    actor_name: str
    source_ip: str | None
    lease_token: str


@dataclass(frozen=True)
class _ZoneBindTaskItemContext:
    """子项认领后保留必要标量，支持服务层独立提交或回滚。"""

    id: int
    remote_zone_id: str
    zone_name: str | None
    description: str | None


async def create_zone_bind_task(
    session: AsyncSession,
    *,
    provider: DomainProvider,
    credential_id: int,
    selections: list[ZoneBindSelection],
    audit_context: domain_service.DomainAuditContext,
) -> DnsZoneBindTask:
    """校验本地凭据后持久化任务，不在 HTTP 请求中访问 DNS 服务商。"""
    await domain_service.resolve_provider_credential(session, provider, credential_id)
    unique_selections: list[ZoneBindSelection] = []
    selected_ids: set[str] = set()
    for selection in selections:
        if selection.remote_zone_id not in selected_ids:
            selected_ids.add(selection.remote_zone_id)
            unique_selections.append(selection)
    if not unique_selections:
        raise Errors.param("请选择至少一个 Zone")

    task = DnsZoneBindTask(
        provider=provider.value,
        credential_id=credential_id,
        status=DnsBindTaskStatus.QUEUED.value,
        total_count=len(unique_selections),
        created_by=audit_context.actor_id,
        actor_name=audit_context.actor_name,
        source_ip=audit_context.source_ip,
    )
    session.add(task)
    await session.flush()
    session.add_all([
        DnsZoneBindTaskItem(
            task_id=task.id,
            remote_zone_id=selection.remote_zone_id,
            zone_name=selection.zone_name,
            description=selection.description,
            status=DnsBindTaskItemStatus.PENDING.value,
        )
        for selection in unique_selections
    ])
    await session.commit()
    await session.refresh(task)
    audit.log(
        module="domain",
        action="zone.bind_task",
        actor_id=audit_context.actor_id,
        actor_name=audit_context.actor_name,
        source_ip=audit_context.source_ip,
        target_type="dns_zone_bind_task",
        target_id=str(task.id),
        detail={"provider": provider.value, "credential_id": credential_id, "total_count": task.total_count},
    )
    return task


async def get_zone_bind_task(
    session: AsyncSession,
    task_id: int,
) -> tuple[DnsZoneBindTask, list[DnsZoneBindTaskItem]]:
    """返回任务及所有逐 Zone 状态，供前端持续展示进度。"""
    task = await session.get(DnsZoneBindTask, task_id)
    if task is None:
        raise Errors.not_found("Zone 绑定任务不存在")
    items = list((await session.execute(
        select(DnsZoneBindTaskItem)
        .where(DnsZoneBindTaskItem.task_id == task.id)
        .order_by(DnsZoneBindTaskItem.id)
    )).scalars())
    return task, items


async def process_next_zone_bind_task(session: AsyncSession) -> bool:
    """认领一个待处理任务，串行生成所选 Zone 的本地快照。"""
    task = await _claim_next_zone_bind_task(session)
    if task is None:
        return False

    lease_token = task.lease_token
    if lease_token is None:
        return False
    audit_context = domain_service.DomainAuditContext(
        actor_id=task.created_by,
        actor_name=task.actor_name,
        source_ip=task.source_ip,
    )
    try:
        provider = DomainProvider(task.provider)
        discovered = await domain_service.discover_zones(
            session,
            provider,
            task.credential_id,
            audit_context,
        )
    except Exception as exc:  # noqa: BLE001 服务商发现异常需要结束全部未处理项
        await _fail_unprocessed_items(session, task, lease_token, _error_message(exc))
        return True

    available = {zone.remote_zone_id: zone for zone in discovered}
    while True:
        item = await _claim_next_task_item(session, task.id, lease_token)
        if item is None:
            await _refresh_task_progress(session, task, lease_token)
            return True

        zone_ref = available.get(item.remote_zone_id)
        if zone_ref is None:
            result = ZoneBindResult(
                remote_zone_id=item.remote_zone_id,
                status=DnsBindTaskItemStatus.FAILED.value,
                reason="Zone 不可访问或不是公网 Zone",
            )
        else:
            try:
                result = (
                    await domain_service.bind_zones(
                        session,
                        provider=provider,
                        credential_id=task.credential_id,
                        selections=[ZoneBindSelection(
                            remote_zone_id=item.remote_zone_id,
                            description=item.description,
                        )],
                        created_by=task.created_by,
                        audit_context=audit_context,
                        discovered=discovered,
                    )
                )[0]
            except Exception as exc:  # noqa: BLE001 单项异常不能阻断同一任务的后续 Zone
                await session.rollback()
                result = ZoneBindResult(
                    remote_zone_id=item.remote_zone_id,
                    status=DnsBindTaskItemStatus.FAILED.value,
                    reason=_error_message(exc),
                )

        zone_name = zone_ref.zone_name if zone_ref else item.zone_name
        if result.status == DnsBindTaskItemStatus.FAILED.value:
            zone_ids = await _persist_failed_zones(
                session,
                task,
                [(item, zone_name, result.reason)],
            )
            result = ZoneBindResult(
                remote_zone_id=result.remote_zone_id,
                zone_id=zone_ids.get(item.remote_zone_id),
                status=result.status,
                reason=result.reason,
            )

        if not await _complete_task_item(
            session,
            task,
            item,
            lease_token,
            result,
            zone_name=zone_name,
        ):
            return True


async def _claim_next_zone_bind_task(session: AsyncSession) -> _ZoneBindTaskContext | None:
    """通过条件更新认领排队或租约已过期的任务。"""
    now = datetime.now()
    claimable = or_(
        DnsZoneBindTask.status == DnsBindTaskStatus.QUEUED.value,
        and_(
            DnsZoneBindTask.status == DnsBindTaskStatus.RUNNING.value,
            or_(
                DnsZoneBindTask.lease_expires_at.is_(None),
                DnsZoneBindTask.lease_expires_at <= now,
            ),
        ),
    )
    task = (await session.execute(
        select(DnsZoneBindTask).where(claimable).order_by(DnsZoneBindTask.id).limit(1)
    )).scalar_one_or_none()
    if task is None:
        await session.rollback()
        return None

    context = _ZoneBindTaskContext(
        id=task.id,
        provider=task.provider,
        credential_id=task.credential_id,
        total_count=task.total_count,
        created_by=task.created_by,
        actor_name=task.actor_name,
        source_ip=task.source_ip,
        lease_token=str(uuid4()),
    )
    recovered = task.status == DnsBindTaskStatus.RUNNING.value
    claimed = await session.execute(
        update(DnsZoneBindTask)
        .where(DnsZoneBindTask.id == task.id, claimable)
        .values(
            status=DnsBindTaskStatus.RUNNING.value,
            lease_token=context.lease_token,
            lease_expires_at=now + _TASK_LEASE_DURATION,
            started_at=task.started_at or now,
            finished_at=None,
            last_error=None,
        )
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return None
    if recovered:
        await session.execute(
            update(DnsZoneBindTaskItem)
            .where(
                DnsZoneBindTaskItem.task_id == task.id,
                DnsZoneBindTaskItem.status == DnsBindTaskItemStatus.RUNNING.value,
            )
            .values(
                status=DnsBindTaskItemStatus.PENDING.value,
                lease_token=None,
                finished_at=None,
            )
    )
    await session.commit()
    return context


async def _claim_next_task_item(
    session: AsyncSession,
    task_id: int,
    lease_token: str,
) -> _ZoneBindTaskItemContext | None:
    """续租后原子认领一个待处理 Zone，防止旧 Worker 覆盖新结果。"""
    now = datetime.now()
    renewed = await session.execute(
        update(DnsZoneBindTask)
        .where(
            DnsZoneBindTask.id == task_id,
            DnsZoneBindTask.status == DnsBindTaskStatus.RUNNING.value,
            DnsZoneBindTask.lease_token == lease_token,
        )
        .values(lease_expires_at=now + _TASK_LEASE_DURATION)
    )
    if renewed.rowcount != 1:
        await session.commit()
        return None

    item = (await session.execute(
        select(DnsZoneBindTaskItem)
        .where(
            DnsZoneBindTaskItem.task_id == task_id,
            DnsZoneBindTaskItem.status == DnsBindTaskItemStatus.PENDING.value,
        )
        .order_by(DnsZoneBindTaskItem.id)
        .limit(1)
    )).scalar_one_or_none()
    if item is None:
        await session.commit()
        return None

    context = _ZoneBindTaskItemContext(
        id=item.id,
        remote_zone_id=item.remote_zone_id,
        zone_name=item.zone_name,
        description=item.description,
    )
    claimed = await session.execute(
        update(DnsZoneBindTaskItem)
        .where(
            DnsZoneBindTaskItem.id == item.id,
            DnsZoneBindTaskItem.status == DnsBindTaskItemStatus.PENDING.value,
        )
        .values(
            status=DnsBindTaskItemStatus.RUNNING.value,
            lease_token=lease_token,
            started_at=item.started_at or now,
            finished_at=None,
            reason=None,
        )
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return None
    await session.commit()
    return context


async def _complete_task_item(
    session: AsyncSession,
    task: _ZoneBindTaskContext,
    item: _ZoneBindTaskItemContext,
    lease_token: str,
    result: ZoneBindResult,
    *,
    zone_name: str | None,
) -> bool:
    """仅允许持有当前租约的 Worker 回写子项结果并刷新汇总。"""
    now = datetime.now()
    changed = await session.execute(
        update(DnsZoneBindTaskItem)
        .where(
            DnsZoneBindTaskItem.id == item.id,
            DnsZoneBindTaskItem.status == DnsBindTaskItemStatus.RUNNING.value,
            DnsZoneBindTaskItem.lease_token == lease_token,
        )
        .values(
            zone_name=zone_name,
            zone_id=result.zone_id,
            status=result.status,
            reason=_trim_message(result.reason),
            lease_token=None,
            finished_at=now,
        )
    )
    await session.commit()
    if changed.rowcount != 1:
        return False
    await _refresh_task_progress(
        session,
        task,
        lease_token,
        latest_error=result.reason if result.status == DnsBindTaskItemStatus.FAILED.value else None,
    )
    return True


async def _fail_unprocessed_items(
    session: AsyncSession,
    task: _ZoneBindTaskContext,
    lease_token: str,
    reason: str,
) -> None:
    """服务商发现失败时，持久化所有未处理 Zone 的失败原因。"""
    if not await _renew_task_lease(session, task.id, lease_token):
        return
    now = datetime.now()
    items = [
        _ZoneBindTaskItemContext(
            id=item.id,
            remote_zone_id=item.remote_zone_id,
            zone_name=item.zone_name,
            description=item.description,
        )
        for item in (await session.execute(
            select(DnsZoneBindTaskItem).where(
                DnsZoneBindTaskItem.task_id == task.id,
                DnsZoneBindTaskItem.status.in_([
                    DnsBindTaskItemStatus.PENDING.value,
                    DnsBindTaskItemStatus.RUNNING.value,
                ]),
            )
        )).scalars()
    ]
    await session.execute(
        update(DnsZoneBindTaskItem)
        .where(
            DnsZoneBindTaskItem.task_id == task.id,
            DnsZoneBindTaskItem.status.in_([
                DnsBindTaskItemStatus.PENDING.value,
                DnsBindTaskItemStatus.RUNNING.value,
            ]),
        )
        .values(
            status=DnsBindTaskItemStatus.FAILED.value,
            reason=_trim_message(reason),
            lease_token=None,
            finished_at=now,
        )
    )
    await session.commit()
    await _persist_failed_zones(
        session,
        task,
        [(item, item.zone_name, reason) for item in items],
    )
    await _refresh_task_progress(session, task, lease_token, latest_error=reason)


async def _persist_failed_zones(
    session: AsyncSession,
    task: _ZoneBindTaskContext,
    failures: list[tuple[_ZoneBindTaskItemContext, str | None, str | None]],
) -> dict[str, int]:
    """为初次绑定失败的 Zone 创建空快照，已有快照保持原样。"""
    if not failures:
        return {}

    remote_zone_ids = {item.remote_zone_id for item, _, _ in failures}
    for _ in range(2):
        existing = dict((await session.execute(
            select(DnsZone.remote_zone_id, DnsZone.id).where(
                DnsZone.provider == task.provider,
                DnsZone.remote_zone_id.in_(remote_zone_ids),
            )
        )).all())
        missing = [failure for failure in failures if failure[0].remote_zone_id not in existing]
        if not missing:
            await session.commit()
            return existing

        now = datetime.now()
        zones = [
            DnsZone(
                provider=task.provider,
                remote_zone_id=item.remote_zone_id,
                zone_name=zone_name or item.remote_zone_id[:253],
                credential_id=task.credential_id,
                description=item.description,
                record_count=0,
                sync_status=DnsSyncStatus.FAILED.value,
                last_synced_at=now,
                last_sync_error=_trim_message(reason) or "DNS 服务商暂时不可用，请稍后重试",
                created_by=task.created_by,
            )
            for item, zone_name, reason in missing
        ]
        session.add_all(zones)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            continue
        existing.update({zone.remote_zone_id: zone.id for zone in zones})
        return existing

    existing = dict((await session.execute(
        select(DnsZone.remote_zone_id, DnsZone.id).where(
            DnsZone.provider == task.provider,
            DnsZone.remote_zone_id.in_(remote_zone_ids),
        )
    )).all())
    await session.commit()
    return existing


async def _renew_task_lease(session: AsyncSession, task_id: int, lease_token: str) -> bool:
    renewed = await session.execute(
        update(DnsZoneBindTask)
        .where(
            DnsZoneBindTask.id == task_id,
            DnsZoneBindTask.status == DnsBindTaskStatus.RUNNING.value,
            DnsZoneBindTask.lease_token == lease_token,
        )
        .values(lease_expires_at=datetime.now() + _TASK_LEASE_DURATION)
    )
    await session.commit()
    return renewed.rowcount == 1


async def _refresh_task_progress(
    session: AsyncSession,
    task: _ZoneBindTaskContext,
    lease_token: str,
    *,
    latest_error: str | None = None,
) -> bool:
    """从子项状态重新汇总任务进度，避免崩溃恢复时计数漂移。"""
    rows = await session.execute(
        select(DnsZoneBindTaskItem.status, func.count(DnsZoneBindTaskItem.id))
        .where(DnsZoneBindTaskItem.task_id == task.id)
        .group_by(DnsZoneBindTaskItem.status)
    )
    counts = {status: count for status, count in rows}
    success_count = counts.get(DnsBindTaskItemStatus.SUCCESS.value, 0)
    skipped_count = counts.get(DnsBindTaskItemStatus.SKIPPED.value, 0)
    failed_count = counts.get(DnsBindTaskItemStatus.FAILED.value, 0)
    completed_count = sum(counts.get(status, 0) for status in _TERMINAL_ITEM_STATUSES)
    values: dict = {
        "success_count": success_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
    }
    if latest_error:
        values["last_error"] = _trim_message(latest_error)

    completed = completed_count == task.total_count
    if completed:
        values.update(
            status=(
                DnsBindTaskStatus.SUCCESS.value
                if failed_count == 0
                else DnsBindTaskStatus.PARTIAL_FAILED.value
                if success_count or skipped_count
                else DnsBindTaskStatus.FAILED.value
            ),
            lease_token=None,
            lease_expires_at=None,
            finished_at=datetime.now(),
        )

    changed = await session.execute(
        update(DnsZoneBindTask)
        .where(
            DnsZoneBindTask.id == task.id,
            DnsZoneBindTask.status == DnsBindTaskStatus.RUNNING.value,
            DnsZoneBindTask.lease_token == lease_token,
        )
        .values(**values)
    )
    await session.commit()
    if changed.rowcount != 1:
        return False
    if completed:
        audit.log(
            module="domain",
            action="zone.bind_task.complete",
            result="success" if failed_count == 0 else "failed",
            actor_id=task.created_by,
            actor_name=task.actor_name,
            source_ip=task.source_ip,
            target_type="dns_zone_bind_task",
            target_id=str(task.id),
            detail={
                "provider": task.provider,
                "success_count": success_count,
                "skipped_count": skipped_count,
                "failed_count": failed_count,
            },
        )
    return True


def _error_message(exc: Exception) -> str:
    if isinstance(exc, BizError):
        return _trim_message(exc.message) or "DNS 服务商暂时不可用，请稍后重试"
    return "DNS 服务商暂时不可用，请稍后重试"


def _trim_message(message: str | None) -> str | None:
    return message[:512] if message else None


def task_to_dict(task: DnsZoneBindTask, items: list[DnsZoneBindTaskItem] | None = None) -> dict:
    """任务响应不返回凭据密文、调用者 IP 或 Worker 租约。"""
    data = {
        "id": task.id,
        "provider": task.provider,
        "credential_id": task.credential_id,
        "status": task.status,
        "total_count": task.total_count,
        "success_count": task.success_count,
        "skipped_count": task.skipped_count,
        "failed_count": task.failed_count,
        "last_error": task.last_error,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }
    if items is not None:
        data["items"] = [
            {
                "remote_zone_id": item.remote_zone_id,
                "zone_name": item.zone_name,
                "zone_id": item.zone_id,
                "status": item.status,
                "reason": item.reason,
                "started_at": item.started_at.isoformat() if item.started_at else None,
                "finished_at": item.finished_at.isoformat() if item.finished_at else None,
            }
            for item in items
        ]
    return data
