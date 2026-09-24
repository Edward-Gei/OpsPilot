"""域名管理路由：仅暴露 Zone/记录快照和安全的变更入口。"""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select

from app import audit
from app.core.constants import DnsSyncStatus, DomainProvider
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import Errors, ok
from app.dns_providers.base import RecordSetDraft
from app.models.auth import User
from app.models.domain import DnsRecordSet, DnsZone
from app.models.job import Credential
from app.schemas.domain import (
    RecordSetCreateRequest,
    RecordSetUpdateRequest,
    ZoneBindRequest,
    ZoneDescriptionUpdateRequest,
    ZoneDiscoverRequest,
)
from app.services import domain_bind_task_service, domain_excel, domain_service


router = APIRouter(prefix="/domains", tags=["域名管理"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(content: bytes, filename: str) -> Response:
    """xlsx 下载统一使用 RFC 5987 编码中文文件名。"""
    return Response(
        content=content,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _timestamp(value) -> str | None:
    return value.isoformat() if value else None


async def _credential_names(session: DbSession, credential_ids: list[int]) -> dict[int, str]:
    ids = set(credential_ids)
    if not ids:
        return {}
    rows = await session.execute(select(Credential.id, Credential.name).where(Credential.id.in_(ids)))
    return dict(rows.all())


def _zone_brief(zone: DnsZone, credential_names: dict[int, str]) -> dict:
    """Zone 响应只展示凭据名称，不暴露认证字段或服务商密文。"""
    return {
        "id": zone.id,
        "provider": zone.provider,
        "remote_zone_id": zone.remote_zone_id,
        "zone_name": zone.zone_name,
        "credential_name": credential_names.get(zone.credential_id),
        "description": zone.description,
        "record_count": zone.record_count,
        "sync_status": zone.sync_status,
        "last_synced_at": _timestamp(zone.last_synced_at),
        "last_sync_error": zone.last_sync_error,
        "created_at": _timestamp(zone.created_at),
        "updated_at": _timestamp(zone.updated_at),
    }


def _record_brief(record: DnsRecordSet) -> dict:
    """记录集响应显式排除 provider_meta 与服务商定位键。"""
    return {
        "id": record.id,
        "record_name": record.record_name,
        "record_type": record.record_type,
        "ttl": record.ttl,
        "values": record.values,
        "read_only": record.read_only,
        "read_only_reason": record.read_only_reason,
        "created_at": _timestamp(record.created_at),
        "updated_at": _timestamp(record.updated_at),
    }


def _audit_context(actor: User, request: Request) -> domain_service.DomainAuditContext:
    return domain_service.DomainAuditContext(actor.id, actor.username, get_client_ip(request))


@router.get("/credentials", summary="列出服务商兼容凭据")
async def list_compatible_credentials(
    provider: DomainProvider,
    session: DbSession,
    _: User = Depends(require_perm("domain:write")),
) -> dict:
    """仅返回绑定选择需要的安全凭据元数据。"""
    return ok({"items": await domain_service.list_compatible_credentials(session, provider)})


@router.post("/zones/discover", summary="发现公网 Zone")
async def discover_zones(
    req: ZoneDiscoverRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """使用指定凭据发现当前可访问的公网 Zone。"""
    zones = await domain_service.discover_zones(
        session,
        req.provider,
        req.credential_id,
        _audit_context(actor, request),
    )
    return ok({"items": [{"remote_zone_id": zone.remote_zone_id, "zone_name": zone.zone_name} for zone in zones]})


@router.post("/zones", summary="创建 Zone 绑定任务", status_code=status.HTTP_202_ACCEPTED)
async def bind_zones(
    req: ZoneBindRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """创建后台任务，避免批量快照读取占用浏览器请求。"""
    task = await domain_bind_task_service.create_zone_bind_task(
        session,
        provider=req.provider,
        credential_id=req.credential_id,
        selections=req.selections,
        audit_context=_audit_context(actor, request),
    )
    return ok(domain_bind_task_service.task_to_dict(task))


@router.get("/zone-bind-tasks/{task_id}", summary="查询 Zone 绑定任务进度")
async def get_zone_bind_task(
    task_id: int,
    session: DbSession,
    _: User = Depends(require_perm("domain:read")),
) -> dict:
    """读取持久化任务进度，不会触发 DNS 服务商调用。"""
    task, items = await domain_bind_task_service.get_zone_bind_task(session, task_id)
    return ok(domain_bind_task_service.task_to_dict(task, items))


@router.get("/zones/import-template", summary="下载 Zone 导入模板")
async def download_zone_import_template(
    _: User = Depends(require_perm("domain:write")),
) -> Response:
    """模板仅包含 Zone 台账字段，不承载凭据密文。"""
    return _xlsx_response(domain_excel.build_zone_import_template(), "Zone导入模板.xlsx")


@router.post("/zones/import", summary="Excel 导入 Zone 台账")
async def import_zones(
    file: UploadFile,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """按凭据发现可访问公网 Zone，并创建已有记录的本地快照。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise Errors.param("仅支持 .xlsx 文件")
    result = await domain_excel.import_zones(
        session,
        await file.read(),
        created_by=actor.id,
        audit_context=_audit_context(actor, request),
    )
    audit.log(
        module="domain",
        action="zone.import",
        actor_id=actor.id,
        actor_name=actor.username,
        source_ip=get_client_ip(request),
        target_type="dns_zone",
        target_name=file.filename,
        detail={
            "success_count": result["success_count"],
            "skipped_count": len(result["skipped_rows"]),
            "failed_count": len(result["failed_rows"]),
        },
    )
    return ok(result)


@router.get("/zones/export", summary="导出 Zone 台账与记录集")
async def export_zones(
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:read")),
    keyword: str | None = None,
    provider: DomainProvider | None = None,
    sync_status: DnsSyncStatus | None = None,
) -> Response:
    """导出与 Zone 台账列表一致筛选范围的完整记录快照。"""
    zones, records = await domain_excel.load_zone_export_rows(
        session,
        keyword=keyword,
        provider=provider.value if provider else None,
        sync_status=sync_status.value if sync_status else None,
    )
    audit.log(
        module="domain",
        action="zone.export",
        actor_id=actor.id,
        actor_name=actor.username,
        source_ip=get_client_ip(request),
        target_type="dns_zone",
        detail={"zone_count": len(zones), "record_count": len(records)},
    )
    return _xlsx_response(domain_excel.export_zones(zones, records), "Zone台账.xlsx")


@router.get("/zones", summary="Zone 台账列表")
async def list_zones(
    session: DbSession,
    _: User = Depends(require_perm("domain:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    provider: DomainProvider | None = None,
    sync_status: DnsSyncStatus | None = None,
    sort_by: Literal[
        "zone_name", "provider", "credential_name", "description", "record_count",
        "sync_status", "last_synced_at", "last_sync_error",
    ] | None = None,
    sort_order: Literal["asc", "desc"] | None = None,
) -> dict:
    """查询本地 Zone 台账，不因列表操作访问服务商。"""
    zones, total = await domain_service.list_zones(
        session,
        page=page,
        page_size=page_size,
        keyword=keyword,
        provider=provider,
        sync_status=sync_status.value if sync_status else None,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    credential_names = await _credential_names(session, [zone.credential_id for zone in zones])
    return ok({
        "items": [_zone_brief(zone, credential_names) for zone in zones],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("/zones/{zone_id}/sync", summary="手动同步 Zone 快照")
async def sync_zone(
    zone_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """从服务商完整刷新单个 Zone 的本地快照。"""
    zone = await domain_service.sync_zone(session, zone_id, _audit_context(actor, request))
    credential_names = await _credential_names(session, [zone.credential_id])
    return ok(_zone_brief(zone, credential_names))


@router.get("/zones/{zone_id}/records", summary="Zone 记录集列表")
async def list_record_sets(
    zone_id: int,
    session: DbSession,
    _: User = Depends(require_perm("domain:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    record_type: str | None = None,
    read_only: bool | None = None,
) -> dict:
    """分页读取本地记录快照，支持名称、类型和只读状态筛选。"""
    records, total = await domain_service.list_record_sets(
        session,
        zone_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        record_type=record_type.upper() if record_type else None,
        read_only=read_only,
    )
    return ok({
        "items": [_record_brief(record) for record in records],
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@router.post("/zones/{zone_id}/records", summary="新增记录集")
async def create_record_set(
    zone_id: int,
    req: RecordSetCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """创建已验证的简单路由记录集。"""
    await domain_service.create_record_set(
        session,
        zone_id,
        RecordSetDraft(req.owner_name, req.record_type, req.ttl, req.values),
        _audit_context(actor, request),
    )
    return ok()


@router.put("/zones/{zone_id}/records/{record_id}", summary="修改记录集")
async def update_record_set(
    zone_id: int,
    record_id: int,
    req: RecordSetUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """名称和类型由本地快照确定，客户端只能提交 TTL 与记录值。"""
    record = await domain_service.get_record_set(session, zone_id, record_id)
    await domain_service.update_record_set(
        session,
        zone_id,
        record_id,
        RecordSetDraft(record.record_name, record.record_type, req.ttl, req.values),
        _audit_context(actor, request),
    )
    return ok()


@router.delete("/zones/{zone_id}/records/{record_id}", summary="删除记录集")
async def delete_record_set(
    zone_id: int,
    record_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:delete")),
) -> dict:
    """删除前由服务层校验只读状态与远端漂移。"""
    await domain_service.delete_record_set(session, zone_id, record_id, _audit_context(actor, request))
    return ok()


@router.get("/zones/{zone_id}", summary="Zone 台账详情")
async def get_zone(
    zone_id: int,
    session: DbSession,
    _: User = Depends(require_perm("domain:read")),
) -> dict:
    """返回本地 Zone 快照详情，不发起隐式同步。"""
    zone = await domain_service.get_zone(session, zone_id)
    credential_names = await _credential_names(session, [zone.credential_id])
    return ok(_zone_brief(zone, credential_names))


@router.put("/zones/{zone_id}", summary="更新 Zone 说明")
async def update_zone_description(
    zone_id: int,
    req: ZoneDescriptionUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:write")),
) -> dict:
    """说明是本地台账字段，不会覆盖服务商配置。"""
    await domain_service.update_zone_description(
        session,
        zone_id,
        req.description,
        _audit_context(actor, request),
    )
    return ok()


@router.delete("/zones/{zone_id}", summary="解绑 Zone")
async def unbind_zone(
    zone_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("domain:delete")),
) -> dict:
    """仅删除本地绑定和快照，绝不删除远端 Zone 或记录。"""
    await domain_service.unbind_zone(session, zone_id, _audit_context(actor, request))
    return ok()
