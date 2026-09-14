"""DNS Zone 的凭据解析、绑定与快照同步服务。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.core.constants import CredentialAuthType, DnsSyncStatus, DomainProvider
from app.core.response import BizError, Errors
from app.core.security import decrypt_text
from app.dns_providers import get_adapter
from app.dns_providers.base import (
    MAX_EDITABLE_TTL,
    MIN_EDITABLE_TTL,
    WRITABLE_RECORD_TYPES,
    DnsProviderAdapter,
    ProviderCredential,
    ProviderRejectedError,
    ProviderUnavailableError,
    RecordSetDraft,
    RemoteRecordSet,
    ZoneRef,
    normalize_owner_name,
    normalize_record_values,
)
from app.models.domain import DnsRecordSet, DnsZone
from app.models.job import Credential
from app.schemas.domain import ZoneBindResult, ZoneBindSelection
from app.services.credential_service import get_credential_or_404


COMPATIBLE_AUTH_TYPES = {
    DomainProvider.AWS_ROUTE53: CredentialAuthType.USERNAME_PASSWORD.value,
    DomainProvider.TENCENT_DNSPOD: CredentialAuthType.USERNAME_PASSWORD.value,
    DomainProvider.GOOGLE_CLOUD_DNS: CredentialAuthType.SECRET_FILE.value,
}
_LEASE_DURATION = timedelta(minutes=2)
_READ_ATTEMPTS = 3


@dataclass(frozen=True)
class DomainAuditContext:
    """域名操作写审计所需的调用者信息。"""

    actor_id: int
    actor_name: str
    source_ip: str | None


async def resolve_provider_credential(
    session: AsyncSession,
    provider: DomainProvider,
    credential_id: int,
) -> ProviderCredential:
    """仅在服务端短暂解密与服务商匹配的已有凭据。"""
    credential = await get_credential_or_404(session, credential_id)
    if credential.auth_type != COMPATIBLE_AUTH_TYPES[provider]:
        raise Errors.rejected("所选凭据类型与 DNS 服务商不兼容")
    try:
        secret = decrypt_text(credential.secret_enc)
    except Exception:
        raise Errors.rejected("所选凭据无法解密") from None
    return ProviderCredential(
        provider=provider,
        credential_id=credential.id,
        login_user=credential.login_user,
        secret=secret,
    )


async def list_compatible_credentials(
    session: AsyncSession,
    provider: DomainProvider,
) -> list[dict]:
    """返回可用于指定 DNS 服务商的凭据安全元数据。"""
    rows = await session.execute(
        select(Credential.id, Credential.name, Credential.auth_type, Credential.description)
        .where(Credential.auth_type == COMPATIBLE_AUTH_TYPES[provider])
        .order_by(Credential.id.desc())
    )
    return [
        {
            "id": credential_id,
            "name": name,
            "auth_type": auth_type,
            "description": description,
        }
        for credential_id, name, auth_type, description in rows
    ]


async def discover_zones(
    session: AsyncSession,
    provider: DomainProvider,
    credential_id: int,
    audit_context: DomainAuditContext,
) -> list[ZoneRef]:
    """使用兼容凭据发现可访问的公网 Zone。"""
    credential = await resolve_provider_credential(session, provider, credential_id)
    await session.commit()
    try:
        zones = await _discover_with_retries(get_adapter(provider), credential)
    except Exception as exc:
        error = _provider_error(exc)
        _audit(
            audit_context,
            "zone.discover",
            "failed",
            detail={"provider": provider.value, "credential_id": credential_id, "reason": error.message},
        )
        raise error from None
    _audit(
        audit_context,
        "zone.discover",
        detail={"provider": provider.value, "credential_id": credential_id, "count": len(zones)},
    )
    return zones


async def get_zone(session: AsyncSession, zone_id: int) -> DnsZone:
    """按 ID 获取本地 Zone 快照。"""
    zone = await session.get(DnsZone, zone_id)
    if zone is None:
        raise Errors.not_found("Zone 不存在")
    return zone


async def list_zones(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    provider: DomainProvider | str | None = None,
    sync_status: str | None = None,
) -> tuple[list[DnsZone], int]:
    """分页读取本地 Zone 台账，按名称、说明和状态筛选。"""
    query = select(DnsZone)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(DnsZone.zone_name.like(like) | DnsZone.description.like(like))
    if provider:
        query = query.where(
            DnsZone.provider == (provider.value if isinstance(provider, DomainProvider) else provider)
        )
    if sync_status:
        query = query.where(DnsZone.sync_status == sync_status)
    total = (await session.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar_one()
    rows = await session.execute(
        query.order_by(DnsZone.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def list_record_sets(
    session: AsyncSession,
    zone_id: int,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    record_type: str | None = None,
    read_only: bool | None = None,
) -> tuple[list[DnsRecordSet], int]:
    """分页读取本地记录快照，不向服务商发起请求。"""
    await get_zone(session, zone_id)
    query = select(DnsRecordSet).where(DnsRecordSet.zone_id == zone_id)
    if keyword:
        query = query.where(DnsRecordSet.record_name.like(f"%{keyword}%"))
    if record_type:
        query = query.where(DnsRecordSet.record_type == record_type)
    if read_only is not None:
        query = query.where(DnsRecordSet.read_only == read_only)
    total = (await session.execute(
        select(func.count()).select_from(query.subquery())
    )).scalar_one()
    rows = await session.execute(
        query.order_by(
            DnsRecordSet.record_name.asc(),
            DnsRecordSet.record_type.asc(),
            DnsRecordSet.id.asc(),
        ).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def update_zone_description(
    session: AsyncSession,
    zone_id: int,
    description: str | None,
    audit_context: DomainAuditContext,
) -> DnsZone:
    """仅更新本地 Zone 说明，不触发远端请求。"""
    zone = await get_zone(session, zone_id)
    zone.description = description
    await session.flush()
    _audit(
        audit_context,
        "zone.update",
        target_id=str(zone.id),
        target_name=zone.zone_name,
        detail={"provider": zone.provider, "description": description},
    )
    return zone


async def unbind_zone(session: AsyncSession, zone_id: int, audit_context: DomainAuditContext) -> DnsZone:
    """解除本地绑定并删除快照，不调用服务商删除远端资源。"""
    zone = await get_zone(session, zone_id)
    records = (await session.execute(
        select(DnsRecordSet).where(DnsRecordSet.zone_id == zone.id)
    )).scalars()
    for record in records:
        await session.delete(record)
    await session.delete(zone)
    await session.flush()
    _audit(
        audit_context,
        "zone.unbind",
        target_id=str(zone.id),
        target_name=zone.zone_name,
        detail={"provider": zone.provider, "remote_zone_id": zone.remote_zone_id},
    )
    return zone


async def acquire_zone_lease(session: AsyncSession, zone_id: int, kind: str) -> str:
    """通过条件更新串行化同一 Zone 的远端操作。"""
    now = datetime.now()
    token = str(uuid4())
    values = {
        "operation_token": token,
        "operation_kind": kind,
        "operation_expires_at": now + _LEASE_DURATION,
    }
    if kind == "sync":
        values["sync_status"] = DnsSyncStatus.SYNCING.value
    changed = await session.execute(
        update(DnsZone)
        .where(
            DnsZone.id == zone_id,
            or_(DnsZone.operation_expires_at.is_(None), DnsZone.operation_expires_at <= now),
        )
        .values(**values)
    )
    await session.commit()
    if changed.rowcount != 1:
        raise Errors.conflict("Zone 正在同步或变更，请稍后重试")
    return token


async def bind_zones(
    session: AsyncSession,
    *,
    provider: DomainProvider,
    credential_id: int,
    selections: list[ZoneBindSelection],
    created_by: int,
    audit_context: DomainAuditContext,
) -> list[ZoneBindResult]:
    """绑定已发现的公网 Zone；每个 Zone 的首次快照独立成败。"""
    credential = await resolve_provider_credential(session, provider, credential_id)
    await session.commit()
    adapter = get_adapter(provider)
    try:
        discovered = await _discover_with_retries(adapter, credential)
    except Exception as exc:
        error = _provider_error(exc)
        _audit(audit_context, "zone.bind", "failed", detail={"provider": provider.value, "reason": error.message})
        return [
            ZoneBindResult(remote_zone_id=item.remote_zone_id, status="failed", reason=error.message)
            for item in selections
        ]

    available = {zone.remote_zone_id: zone for zone in discovered}
    results: list[ZoneBindResult] = []
    selected_ids: set[str] = set()
    for selection in selections:
        if selection.remote_zone_id in selected_ids:
            results.append(ZoneBindResult(
                remote_zone_id=selection.remote_zone_id,
                status="skipped",
                reason="Zone 已在本次提交中选择",
            ))
            continue
        selected_ids.add(selection.remote_zone_id)
        zone_ref = available.get(selection.remote_zone_id)
        if zone_ref is None:
            results.append(ZoneBindResult(
                remote_zone_id=selection.remote_zone_id,
                status="failed",
                reason="Zone 不可访问或不是公网 Zone",
            ))
            continue

        existing = (await session.execute(
            select(DnsZone.id).where(
                DnsZone.provider == provider.value,
                DnsZone.remote_zone_id == zone_ref.remote_zone_id,
            )
        )).scalar_one_or_none()
        await session.commit()
        if existing is not None:
            results.append(ZoneBindResult(
                remote_zone_id=selection.remote_zone_id,
                zone_id=existing,
                status="skipped",
                reason="Zone 已绑定，已跳过",
            ))
            continue

        try:
            remote_records = await _list_with_retries(adapter, zone_ref, credential)
            snapshots = [_snapshot_fields(record) for record in remote_records]
        except Exception as exc:
            error = _provider_error(exc)
            _audit(
                audit_context,
                "zone.bind",
                "failed",
                target_name=zone_ref.zone_name,
                detail={"provider": provider.value, "remote_zone_id": zone_ref.remote_zone_id, "reason": error.message},
            )
            results.append(ZoneBindResult(
                remote_zone_id=selection.remote_zone_id,
                status="failed",
                reason=error.message,
            ))
            continue

        zone = DnsZone(
            provider=provider.value,
            remote_zone_id=zone_ref.remote_zone_id,
            zone_name=zone_ref.zone_name,
            credential_id=credential.credential_id,
            description=selection.description,
            record_count=len(snapshots),
            sync_status=DnsSyncStatus.SUCCESS.value,
            last_synced_at=datetime.now(),
            created_by=created_by,
        )
        try:
            session.add(zone)
            await session.flush()
            for snapshot in snapshots:
                session.add(DnsRecordSet(zone_id=zone.id, **snapshot))
            await session.commit()
        except IntegrityError:
            await session.rollback()
            results.append(ZoneBindResult(
                remote_zone_id=selection.remote_zone_id,
                status="skipped",
                reason="Zone 已绑定，已跳过",
            ))
            continue

        _audit(
            audit_context,
            "zone.bind",
            target_id=str(zone.id),
            target_name=zone.zone_name,
            detail={"provider": provider.value, "remote_zone_id": zone.remote_zone_id, "record_count": zone.record_count},
        )
        results.append(ZoneBindResult(
            remote_zone_id=selection.remote_zone_id,
            zone_id=zone.id,
            status="success",
        ))
    return results


async def sync_zone(session: AsyncSession, zone_id: int, audit_context: DomainAuditContext) -> DnsZone:
    """在租约下完整刷新一个已绑定 Zone 的本地快照。"""
    zone = await get_zone(session, zone_id)
    provider = DomainProvider(zone.provider)
    credential = await resolve_provider_credential(session, provider, zone.credential_id)
    token = await acquire_zone_lease(session, zone.id, "sync")
    return await _sync_zone_under_lease(session, zone, token, credential, audit_context)


async def create_record_set(
    session: AsyncSession,
    zone_id: int,
    draft: RecordSetDraft,
    audit_context: DomainAuditContext,
) -> DnsZone:
    """创建简单记录集，并在远端成功后立即刷新本地快照。"""
    zone = await get_zone(session, zone_id)
    after = _validate_record_draft(zone, draft)
    credential = await resolve_provider_credential(
        session,
        DomainProvider(zone.provider),
        zone.credential_id,
    )
    token = await acquire_zone_lease(session, zone.id, "record.create")
    adapter = get_adapter(credential.provider)
    zone_ref = ZoneRef(credential.provider, zone.remote_zone_id, zone.zone_name)
    try:
        existing = await _find_record_by_identity(adapter, zone_ref, after, credential)
    except Exception as exc:
        error = _record_provider_error(exc)
        await _record_write_failed(session, zone, token, audit_context, "record.create", None, after, error)
        raise error from None
    if existing is not None:
        error = Errors.conflict("DNS 记录已在服务商侧存在，请先手动同步")
        await _record_write_failed(session, zone, token, audit_context, "record.create", None, after, error)
        raise error

    try:
        await adapter.create_simple_record_set(zone_ref, after, credential)
    except Exception as exc:
        error = _record_provider_error(exc)
        await _record_write_failed(session, zone, token, audit_context, "record.create", None, after, error)
        raise error from None
    return await _complete_record_write(
        session,
        zone,
        token,
        credential,
        audit_context,
        "record.create",
        None,
        after,
    )


async def update_record_set(
    session: AsyncSession,
    zone_id: int,
    record_id: int,
    draft: RecordSetDraft,
    audit_context: DomainAuditContext,
) -> DnsZone:
    """修改简单记录集；记录名称和类型不可变。"""
    zone = await get_zone(session, zone_id)
    record = await _get_zone_record(session, zone.id, record_id)
    _ensure_record_editable(record)
    after = _validate_record_draft(zone, draft, existing=record)
    credential = await resolve_provider_credential(
        session,
        DomainProvider(zone.provider),
        zone.credential_id,
    )
    token = await acquire_zone_lease(session, zone.id, "record.update")
    adapter = get_adapter(credential.provider)
    zone_ref = ZoneRef(credential.provider, zone.remote_zone_id, zone.zone_name)
    try:
        before = await _ensure_remote_matches_snapshot(adapter, zone_ref, record, credential)
    except BizError as error:
        await _record_write_failed(session, zone, token, audit_context, "record.update", record, after, error)
        raise
    except Exception as exc:
        error = _record_provider_error(exc)
        await _record_write_failed(session, zone, token, audit_context, "record.update", record, after, error)
        raise error from None

    try:
        await adapter.replace_simple_record_set(zone_ref, before, after, credential)
    except Exception as exc:
        error = _record_provider_error(exc)
        await _record_write_failed(session, zone, token, audit_context, "record.update", before, after, error)
        raise error from None
    return await _complete_record_write(
        session,
        zone,
        token,
        credential,
        audit_context,
        "record.update",
        before,
        after,
    )


async def delete_record_set(
    session: AsyncSession,
    zone_id: int,
    record_id: int,
    audit_context: DomainAuditContext,
) -> DnsZone:
    """删除简单记录集，并在远端成功后立即刷新本地快照。"""
    zone = await get_zone(session, zone_id)
    record = await _get_zone_record(session, zone.id, record_id)
    _ensure_record_editable(record)
    credential = await resolve_provider_credential(
        session,
        DomainProvider(zone.provider),
        zone.credential_id,
    )
    token = await acquire_zone_lease(session, zone.id, "record.delete")
    adapter = get_adapter(credential.provider)
    zone_ref = ZoneRef(credential.provider, zone.remote_zone_id, zone.zone_name)
    try:
        before = await _ensure_remote_matches_snapshot(adapter, zone_ref, record, credential)
    except BizError as error:
        await _record_write_failed(session, zone, token, audit_context, "record.delete", record, None, error)
        raise
    except Exception as exc:
        error = _record_provider_error(exc)
        await _record_write_failed(session, zone, token, audit_context, "record.delete", record, None, error)
        raise error from None

    try:
        await adapter.delete_simple_record_set(zone_ref, before, credential)
    except Exception as exc:
        error = _record_provider_error(exc)
        await _record_write_failed(session, zone, token, audit_context, "record.delete", before, None, error)
        raise error from None
    return await _complete_record_write(
        session,
        zone,
        token,
        credential,
        audit_context,
        "record.delete",
        before,
        None,
    )


async def _sync_zone_under_lease(
    session: AsyncSession,
    zone: DnsZone,
    lease_token: str,
    credential: ProviderCredential,
    audit_context: DomainAuditContext,
) -> DnsZone:
    """复用已获取的租约刷新快照，远端读取失败时保留旧数据。"""
    adapter = get_adapter(credential.provider)
    zone_ref = ZoneRef(credential.provider, zone.remote_zone_id, zone.zone_name)
    try:
        remote_records = await _list_with_retries(adapter, zone_ref, credential)
        snapshots = [_snapshot_fields(record) for record in remote_records]
    except Exception as exc:
        error = _provider_error(exc)
        await _mark_sync_failed(session, zone, lease_token, error.message, audit_context)
        raise error from None

    try:
        existing = {
            record.record_key: record
            for record in (await session.execute(
                select(DnsRecordSet).where(DnsRecordSet.zone_id == zone.id)
            )).scalars()
        }
        incoming_keys = set()
        for snapshot in snapshots:
            record_key = snapshot["record_key"]
            incoming_keys.add(record_key)
            record = existing.get(record_key)
            if record is None:
                session.add(DnsRecordSet(zone_id=zone.id, **snapshot))
            else:
                for field, value in snapshot.items():
                    setattr(record, field, value)
        for record_key, record in existing.items():
            if record_key not in incoming_keys:
                await session.delete(record)

        changed = await session.execute(
            update(DnsZone)
            .where(DnsZone.id == zone.id, DnsZone.operation_token == lease_token)
            .values(
                record_count=len(snapshots),
                sync_status=DnsSyncStatus.SUCCESS.value,
                last_synced_at=datetime.now(),
                last_sync_error=None,
                operation_token=None,
                operation_kind=None,
                operation_expires_at=None,
            )
        )
        if changed.rowcount != 1:
            await session.rollback()
            raise Errors.conflict("Zone 操作租约已过期，请手动同步")
        await session.commit()
    except BizError:
        raise
    except Exception:
        await session.rollback()
        error = Errors.upstream("DNS 服务商暂时不可用，请稍后重试")
        await _mark_sync_failed(session, zone, lease_token, error.message, audit_context)
        raise error from None

    await session.refresh(zone)
    _audit(
        audit_context,
        "zone.sync",
        target_id=str(zone.id),
        target_name=zone.zone_name,
        detail={"provider": zone.provider, "remote_zone_id": zone.remote_zone_id, "record_count": zone.record_count},
    )
    return zone


async def _get_zone_record(session: AsyncSession, zone_id: int, record_id: int) -> DnsRecordSet:
    record = await session.get(DnsRecordSet, record_id)
    if record is None or record.zone_id != zone_id:
        raise Errors.not_found("DNS 记录不存在")
    return record


def _ensure_record_editable(record: DnsRecordSet) -> None:
    if record.read_only:
        raise Errors.rejected(record.read_only_reason or "该 DNS 记录不可编辑或删除")


def _validate_record_draft(
    zone: DnsZone,
    draft: RecordSetDraft,
    *,
    existing: DnsRecordSet | None = None,
) -> RecordSetDraft:
    try:
        record_type = draft.record_type.upper()
        if record_type not in WRITABLE_RECORD_TYPES:
            raise ValueError("记录类型暂不支持")
        if (
            isinstance(draft.ttl, bool)
            or not isinstance(draft.ttl, int)
            or not MIN_EDITABLE_TTL <= draft.ttl <= MAX_EDITABLE_TTL
        ):
            raise ValueError("TTL 必须在 300 至 86400 秒之间")
        record_name = normalize_owner_name(draft.record_name, zone.zone_name)
        values = normalize_record_values(record_type, draft.values)
    except (AttributeError, ValueError) as exc:
        raise Errors.param(str(exc)) from None
    if existing and (record_name != existing.record_name or record_type != existing.record_type):
        raise Errors.param("修改记录不能变更名称或类型")
    return RecordSetDraft(record_name, record_type, draft.ttl, values)


async def _find_record_by_identity(
    adapter: DnsProviderAdapter,
    zone: ZoneRef,
    draft: RecordSetDraft,
    credential: ProviderCredential,
) -> RemoteRecordSet | None:
    records = await _list_with_retries(adapter, zone, credential)
    return next(
        (
            record
            for record in records
            if record.record_name == draft.record_name and record.record_type == draft.record_type
        ),
        None,
    )


async def _ensure_remote_matches_snapshot(
    adapter: DnsProviderAdapter,
    zone: ZoneRef,
    record: DnsRecordSet,
    credential: ProviderCredential,
) -> RemoteRecordSet:
    remote = await adapter.get_record_set(zone, record.record_key, credential)
    if remote is None or not _same_record_snapshot(remote, record):
        raise Errors.conflict("DNS 记录已在服务商侧变更，请先手动同步")
    return remote


def _same_record_snapshot(remote: RemoteRecordSet, snapshot: DnsRecordSet) -> bool:
    return (
        remote.record_name == snapshot.record_name
        and remote.record_type == snapshot.record_type
        and remote.ttl == snapshot.ttl
        and set(remote.values) == set(snapshot.values)
        and remote.read_only_reason == snapshot.read_only_reason
        and _record_locator(remote.provider_meta) == _record_locator(snapshot.provider_meta)
    )


def _record_locator(provider_meta: dict) -> dict:
    locator = provider_meta.get("locator") if isinstance(provider_meta, dict) else None
    return locator if isinstance(locator, dict) else {}


async def _complete_record_write(
    session: AsyncSession,
    zone: DnsZone,
    lease_token: str,
    credential: ProviderCredential,
    audit_context: DomainAuditContext,
    action: str,
    before: DnsRecordSet | RemoteRecordSet | None,
    after: RecordSetDraft | None,
) -> DnsZone:
    _audit_record(audit_context, action, zone, "success", before, after)
    try:
        return await _sync_zone_under_lease(session, zone, lease_token, credential, audit_context)
    except BizError:
        raise Errors.upstream("远端记录已变更，但快照同步失败，请手动同步") from None


async def _record_write_failed(
    session: AsyncSession,
    zone: DnsZone,
    lease_token: str,
    audit_context: DomainAuditContext,
    action: str,
    before: DnsRecordSet | RemoteRecordSet | None,
    after: RecordSetDraft | None,
    error: BizError,
) -> None:
    await _release_zone_lease(session, zone.id, lease_token)
    _audit_record(audit_context, action, zone, "failed", before, after, error.message)


async def _release_zone_lease(session: AsyncSession, zone_id: int, lease_token: str) -> None:
    await session.rollback()
    await session.execute(
        update(DnsZone)
        .where(DnsZone.id == zone_id, DnsZone.operation_token == lease_token)
        .values(operation_token=None, operation_kind=None, operation_expires_at=None)
    )
    await session.commit()


def _record_provider_error(exc: Exception) -> BizError:
    if isinstance(exc, ProviderRejectedError):
        return Errors.rejected("DNS 服务商拒绝该记录变更")
    return Errors.upstream("DNS 服务商暂时不可用，请稍后重试")


def _audit_record(
    context: DomainAuditContext,
    action: str,
    zone: DnsZone,
    result: str,
    before: DnsRecordSet | RemoteRecordSet | None,
    after: RecordSetDraft | None,
    error: str | None = None,
) -> None:
    detail = {
        "provider": zone.provider,
        "remote_zone_id": zone.remote_zone_id,
        "before": _record_audit_payload(before),
        "after": _record_audit_payload(after),
    }
    if error:
        detail["error"] = error
    _audit(
        context,
        action,
        result,
        target_id=str(zone.id),
        target_name=zone.zone_name,
        detail=detail,
    )


def _record_audit_payload(record: DnsRecordSet | RemoteRecordSet | RecordSetDraft | None) -> dict | None:
    if record is None:
        return None
    return {
        "record_name": record.record_name,
        "record_type": record.record_type,
        "ttl": record.ttl,
        "values": list(record.values),
    }


async def _mark_sync_failed(
    session: AsyncSession,
    zone: DnsZone,
    lease_token: str,
    reason: str,
    audit_context: DomainAuditContext,
) -> None:
    """仅在仍持有同一租约时标记失败并释放它。"""
    await session.rollback()
    changed = await session.execute(
        update(DnsZone)
        .where(DnsZone.id == zone.id, DnsZone.operation_token == lease_token)
        .values(
            sync_status=DnsSyncStatus.FAILED.value,
            last_sync_error=reason[:512],
            operation_token=None,
            operation_kind=None,
            operation_expires_at=None,
        )
    )
    await session.commit()
    if changed.rowcount:
        _audit(
            audit_context,
            "zone.sync",
            "failed",
            target_id=str(zone.id),
            target_name=zone.zone_name,
            detail={"provider": zone.provider, "remote_zone_id": zone.remote_zone_id, "reason": reason},
        )


async def _discover_with_retries(
    adapter: DnsProviderAdapter,
    credential: ProviderCredential,
) -> list[ZoneRef]:
    return await _read_with_retries(lambda: adapter.discover_public_zones(credential))


async def _list_with_retries(
    adapter: DnsProviderAdapter,
    zone: ZoneRef,
    credential: ProviderCredential,
) -> list[RemoteRecordSet]:
    return await _read_with_retries(lambda: adapter.list_record_sets(zone, credential))


async def _read_with_retries(operation):
    for attempt in range(_READ_ATTEMPTS):
        try:
            return await operation()
        except ProviderUnavailableError:
            if attempt == _READ_ATTEMPTS - 1:
                raise
            await asyncio.sleep(0)
    raise RuntimeError("不可达")


def _snapshot_fields(record: RemoteRecordSet) -> dict:
    if (
        not record.record_key
        or len(record.record_key) > 512
        or not record.record_name
        or len(record.record_name) > 253
        or not record.record_type
        or len(record.record_type) > 16
        or not isinstance(record.provider_meta, dict)
    ):
        raise ValueError("服务商记录快照无效")
    return {
        "record_key": record.record_key,
        "record_name": record.record_name,
        "record_type": record.record_type,
        "ttl": record.ttl,
        "values": list(record.values),
        "read_only": record.read_only_reason is not None,
        "read_only_reason": record.read_only_reason,
        "provider_meta": record.provider_meta,
    }


def _provider_error(exc: Exception) -> BizError:
    if isinstance(exc, ProviderRejectedError):
        return Errors.rejected("DNS 服务商拒绝该请求")
    return Errors.upstream("DNS 服务商暂时不可用，请稍后重试")


def _audit(
    context: DomainAuditContext,
    action: str,
    result: str = "success",
    *,
    target_id: str | None = None,
    target_name: str | None = None,
    detail: dict | None = None,
) -> None:
    audit.log(
        module="domain",
        action=action,
        result=result,
        actor_id=context.actor_id,
        actor_name=context.actor_name,
        source_ip=context.source_ip,
        target_type="dns_zone",
        target_id=target_id,
        target_name=target_name,
        detail=detail,
    )
