"""CMDB 路由（04-API设计 §4）：cmdb:read / cmdb:write 权限点控制。

路由顺序约束：suggest / import-template / import / export 等静态路径
必须声明在 /{host_id} 之前，否则会被路径参数吞掉。
"""
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import Response

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import Errors, ok
from app.models.auth import User
from app.schemas.cmdb import AppUpsertRequest, HostUpsertRequest
from app.services import app_excel, cmdb_service, host_excel

router = APIRouter(prefix="/cmdb", tags=["CMDB"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(content: bytes, filename: str) -> Response:
    """xlsx 下载响应：中文文件名按 RFC 5987 编码。"""
    return Response(
        content=content,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


def _host_brief(h) -> dict:
    """主机列表/详情统一序列化。"""
    return {
        "id": h.id,
        "hostname": h.hostname,
        "ip": h.ip,
        "project": h.project,
        "public_ip": h.public_ip,
        "ri": h.ri,
        "host_series": h.host_series,
        "platform": h.platform,
        "region": h.region,
        "os": h.os,
        "cpu_cores": h.cpu_cores,
        "memory_gb": h.memory_gb,
        "disk_gb": h.disk_gb,
        "environment": h.environment,
        "status": h.status,
        "ssh_port": h.ssh_port,
        "description": h.description,
        "created_at": h.created_at.isoformat() if h.created_at else None,
        "updated_at": h.updated_at.isoformat() if h.updated_at else None,
    }


def _app_brief(a, stats: dict | None = None) -> dict:
    """应用列表/详情统一序列化；stats 为关联主机数和 IP 清单。"""
    stats = stats or {}
    return {
        "id": a.id,
        "name": a.name,
        "description": a.description,
        "language": a.language,
        "deploy_type": a.deploy_type,
        "project_type": a.project_type,
        "business_line": a.business_line,
        "system_name": a.system_name,
        "service_level": a.service_level,
        "ops_owner": a.ops_owner,
        "dev_owner": a.dev_owner,
        "repo_url": a.repo_url,
        "service_port": a.service_port,
        "cpu_quota": a.cpu_quota,
        "mem_quota": a.mem_quota,
        "host_count": stats.get("host_count", 0),
        "host_ips": stats.get("host_ips", []),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _hosts_stats(hosts: list) -> dict:
    """由主机清单现算关联数与 IP 清单（详情接口复用）。"""
    return {
        "host_count": len(hosts),
        "host_ips": [h.ip for h in hosts],
    }


# ---------- 主机（静态路径在前） ----------

@router.get("/hosts/suggest", summary="平台/区域/主机系列自动补全")
async def suggest_hosts(
    session: DbSession,
    _: User = Depends(require_perm("cmdb:read")),
    field: str = Query(..., description="platform | region | host_series"),
    q: str | None = None,
) -> dict:
    """自由文本字段补全：返回已有去重值。"""
    return ok({"items": await cmdb_service.suggest_host_field(session, field, q)})


@router.get("/hosts/import-template", summary="下载导入模板")
async def download_import_template(
    _: User = Depends(require_perm("cmdb:import")),
) -> Response:
    """xlsx 模板：含表头样式与环境/状态下拉校验。"""
    return _xlsx_response(host_excel.build_import_template(), "主机导入模板.xlsx")


@router.post("/hosts/import", summary="Excel 批量导入")
async def import_hosts(
    file: UploadFile,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:import")),
    upsert: bool = Query(False, description="存在（按 IP）即更新"),
) -> dict:
    """逐行校验 + 入库，返回成功数与失败行明细（HOST-06）。"""
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise Errors.param("仅支持 .xlsx 文件")
    result = await host_excel.import_hosts(
        session, await file.read(), upsert=upsert, created_by=actor.id
    )
    audit.log(module="cmdb", action="host.import", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="host", target_name=file.filename,
              detail={"upsert": upsert, "success_count": result["success_count"],
                      "failed_count": len(result["failed_rows"])})
    return ok(result)


@router.get("/hosts/export", summary="导出主机（按当前筛选）")
async def export_hosts(
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:read")),
    keyword: str | None = None,
    platform: str | None = None,
    region: str | None = None,
    environment: str | None = None,
    status: str | None = None,
) -> Response:
    """导出与列表相同筛选语义的全量结果（HOST-07）。"""
    hosts = await cmdb_service.iter_hosts_filtered(
        session, keyword=keyword, platform=platform, region=region,
        environment=environment, status=status,
    )
    audit.log(module="cmdb", action="host.export", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="host",
              detail={"count": len(hosts)})
    return _xlsx_response(host_excel.export_hosts(hosts), "主机列表.xlsx")


@router.get("/hosts", summary="主机列表")
async def list_hosts(
    session: DbSession,
    _: User = Depends(require_perm("cmdb:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    platform: str | None = None,
    region: str | None = None,
    environment: str | None = None,
    status: str | None = None,
    sort_by: Literal["created_at"] | None = None,
    sort_order: Literal["asc", "desc"] | None = None,
) -> dict:
    """分页查主机（keyword 模糊匹配主机名/IP）。"""
    hosts, total = await cmdb_service.list_hosts(
        session, page=page, page_size=page_size, keyword=keyword,
        platform=platform, region=region, environment=environment, status=status,
        sort_by=sort_by, sort_order=sort_order,
    )
    return ok({"items": [_host_brief(h) for h in hosts], "total": total,
               "page": page, "page_size": page_size})


@router.post("/hosts", summary="新增主机")
async def create_host(
    req: HostUpsertRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:write")),
) -> dict:
    """单条录入；IP 冲突返回 40901（HOST-02）。"""
    host = await cmdb_service.create_host(session, created_by=actor.id, **req.model_dump())
    audit.log(module="cmdb", action="host.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="host",
              target_id=str(host.id), target_name=f"{host.hostname}({host.ip})")
    return ok({"id": host.id})


@router.get("/hosts/{host_id}", summary="主机详情")
async def get_host(
    host_id: int,
    session: DbSession,
    _: User = Depends(require_perm("cmdb:read")),
) -> dict:
    """详情 + 反查关联应用（APP-05）。"""
    host = await cmdb_service.get_host_or_404(session, host_id)
    apps = await cmdb_service.get_host_apps(session, host_id)
    data = _host_brief(host)
    data["apps"] = [_app_brief(a) for a in apps]
    return ok(data)


@router.put("/hosts/{host_id}", summary="编辑主机")
async def update_host(
    host_id: int,
    req: HostUpsertRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:write")),
) -> dict:
    """全量字段更新；IP 变更时重新校验唯一。"""
    host = await cmdb_service.update_host(session, host_id, **req.model_dump())
    audit.log(module="cmdb", action="host.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="host",
              target_id=str(host.id), target_name=f"{host.hostname}({host.ip})",
              detail=req.model_dump(exclude_none=True))
    return ok()


@router.delete("/hosts/{host_id}", summary="删除主机")
async def delete_host(
    host_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:delete")),
) -> dict:
    """删除保护：被应用/进行中工单引用时 42201（HOST-10）。"""
    host = await cmdb_service.delete_host(session, host_id)
    audit.log(module="cmdb", action="host.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="host",
              target_id=str(host_id), target_name=f"{host.hostname}({host.ip})")
    return ok()


# ---------- 应用 ----------

@router.get("/apps/export", summary="导出应用（按当前筛选）")
async def export_apps(
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:read")),
    keyword: str | None = None,
    language: str | None = None,
    deploy_type: str | None = None,
    project_type: Literal["frontend", "backend"] | None = None,
    business_line: Literal["mitrade", "tradingkey"] | None = None,
    service_level: Literal["核心服务", "一般服务"] | None = None,
) -> Response:
    """导出与列表相同筛选语义的全量应用。"""
    apps, host_stats = await cmdb_service.iter_apps_filtered(
        session, keyword=keyword, language=language, deploy_type=deploy_type, project_type=project_type,
        business_line=business_line, service_level=service_level,
    )
    audit.log(module="cmdb", action="app.export", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="app", detail={"count": len(apps)})
    return _xlsx_response(app_excel.export_apps(apps, host_stats), "应用列表.xlsx")

@router.get("/apps", summary="应用列表")
async def list_apps(
    session: DbSession,
    _: User = Depends(require_perm("cmdb:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    language: str | None = None,
    deploy_type: str | None = None,
    project_type: Literal["frontend", "backend"] | None = None,
    business_line: Literal["mitrade", "tradingkey"] | None = None,
    service_level: Literal["核心服务", "一般服务"] | None = None,
    sort_by: Literal["language", "created_at"] | None = None,
    sort_order: Literal["asc", "desc"] | None = None,
) -> dict:
    """分页查应用；items 含关联主机数与 IP 清单。"""
    apps, total, host_stats = await cmdb_service.list_apps(
        session, page=page, page_size=page_size, keyword=keyword,
        language=language, deploy_type=deploy_type, project_type=project_type,
        business_line=business_line, service_level=service_level,
        sort_by=sort_by, sort_order=sort_order,
    )
    return ok({
        "items": [_app_brief(a, host_stats.get(a.id)) for a in apps],
        "total": total, "page": page, "page_size": page_size,
    })


@router.post("/apps", summary="新增应用")
async def create_app(
    req: AppUpsertRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:write")),
) -> dict:
    """新增应用并关联主机；应用名唯一（APP-02）。"""
    app = await cmdb_service.create_app(
        session, created_by=actor.id, host_ids=req.host_ids,
        **req.model_dump(exclude={"host_ids"}),
    )
    audit.log(module="cmdb", action="app.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="app",
              target_id=str(app.id), target_name=app.name,
              detail={"host_ids": req.host_ids})
    return ok({"id": app.id})


@router.get("/apps/{app_id}", summary="应用详情")
async def get_app(
    app_id: int,
    session: DbSession,
    _: User = Depends(require_perm("cmdb:read")),
) -> dict:
    """详情 + 关联主机列表。"""
    app = await cmdb_service.get_app_or_404(session, app_id)
    hosts = await cmdb_service.get_app_hosts(session, app_id)
    data = _app_brief(app, _hosts_stats(hosts))
    data["hosts"] = [_host_brief(h) for h in hosts]
    return ok(data)


@router.put("/apps/{app_id}", summary="编辑应用")
async def update_app(
    app_id: int,
    req: AppUpsertRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:write")),
) -> dict:
    """编辑基本信息；host_ids 全量替换关联。"""
    app = await cmdb_service.update_app(
        session, app_id, host_ids=req.host_ids, **req.model_dump(exclude={"host_ids"})
    )
    audit.log(module="cmdb", action="app.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="app",
              target_id=str(app.id), target_name=app.name,
              detail={"host_ids": req.host_ids})
    return ok()


@router.delete("/apps/{app_id}", summary="删除应用")
async def delete_app(
    app_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("cmdb:delete")),
) -> dict:
    """删除保护：被进行中工单引用时 42201。"""
    app = await cmdb_service.delete_app(session, app_id)
    audit.log(module="cmdb", action="app.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="app",
              target_id=str(app_id), target_name=app.name)
    return ok()
