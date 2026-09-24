"""应用配置 REST 入口：平台实例和配置文件本地管理。"""
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app import audit
from app.core.deps import CurrentUser, DbSession, get_client_ip, require_perm
from app.core.constants import ConfigTaskKind
from app.core.response import ok
from app.models.auth import User
from app.models.auth import Role
from app.models.cmdb import Application
from app.models.job import Credential
from app.models.application_config import ConfigPlatformInstance, ConfigTask, ConfigVersion
from app.schemas.application_config import (
    ConfigFileCreateRequest,
    ConfigDraftUpdateRequest,
    ConfigCandidateRejectRequest,
    ConfigPublishRequest,
    ConfigDiscoverRequest,
    ConfigImportTaskCreateRequest,
    ConfigFileMetadataRequest,
    PlatformInstanceCreateRequest,
    PlatformInstanceUpdateRequest,
)
from app.services import application_config_service
from app.services import rbac_service
from app.services import application_config_task_service
from app.core.security import decrypt_text
from app.services.config_content_service import redact_content, validate_and_normalize_content


router = APIRouter(prefix="/application-configs", tags=["应用配置"])


def _instance_data(instance) -> dict:
    return {
        "id": instance.id,
        "name": instance.name,
        "provider": instance.provider,
        "base_url": instance.base_url,
        "credential_id": instance.credential_id,
        "description": instance.description,
        "enabled": instance.enabled,
        "first_bound_at": instance.first_bound_at.isoformat() if instance.first_bound_at else None,
        "compatibility_version": instance.compatibility_version,
        "last_probed_at": instance.last_probed_at.isoformat() if instance.last_probed_at else None,
        "last_probe_result": instance.last_probe_result,
        "last_probe_error": instance.last_probe_error,
    }


async def _file_data(session: DbSession, config_file) -> dict:
    instance = await session.get(ConfigPlatformInstance, config_file.platform_instance_id)
    role = await session.get(Role, config_file.approval_role_id)
    current_version = await session.get(ConfigVersion, config_file.current_version_id) if config_file.current_version_id else None
    application_ids = await application_config_service.config_file_application_ids(session, config_file.id)
    return {
        "id": config_file.id,
        "name": config_file.name,
        "description": config_file.description,
        "platform_instance_id": config_file.platform_instance_id,
        "platform_instance_name": instance.name if instance else None,
        "provider": instance.provider if instance else None,
        "locator": config_file.locator,
        "content_format": config_file.content_format,
        "approval_role_id": config_file.approval_role_id,
        "approval_role_name": role.name if role else None,
        "application_ids": application_ids,
        "application_count": len(application_ids),
        "current_version_id": config_file.current_version_id,
        "current_version_no": current_version.version_no if current_version else None,
        "current_snapshot_id": config_file.current_snapshot_id,
        "latest_snapshot_id": config_file.latest_snapshot_id,
        "status": config_file.status,
        "drift_status": config_file.drift_status,
        "last_synced_at": config_file.last_synced_at.isoformat() if config_file.last_synced_at else None,
        "last_sync_error": config_file.last_sync_error,
        "created_at": config_file.created_at.isoformat() if config_file.created_at else None,
        "updated_at": config_file.updated_at.isoformat() if config_file.updated_at else None,
    }


@router.get("/platform-instances", summary="配置平台实例列表")
async def list_platform_instances(
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
) -> dict:
    return ok({"items": [_instance_data(item) for item in await application_config_service.list_platform_instances(session)]})


@router.get("/approval-roles", summary="配置文件可选审批角色")
async def list_approval_roles(
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
) -> dict:
    rows = await session.execute(select(Role.id, Role.name).order_by(Role.id))
    return ok({"items": [{"id": role_id, "name": name} for role_id, name in rows]})


@router.get("/approval-todo", summary="当前角色的配置审批待办")
async def list_config_approval_todo(
    session: DbSession,
    actor: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    items, total = await application_config_service.list_approval_todo(session, actor.id, page, page_size)
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/approval-todo/{version_id}/content", summary="按当前审批角色预览脱敏候选内容")
async def get_config_approval_content(version_id: int, session: DbSession, actor: CurrentUser) -> dict:
    perms = await rbac_service.get_user_perms(session, actor.id)
    return ok(await application_config_service.approval_todo_content(
        session, version_id, actor.id, "secret:read" in perms,
    ))


@router.get("/cmdb-applications", summary="可关联的 CMDB 应用选项")
async def list_cmdb_applications(
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
    keyword: str | None = None,
) -> dict:
    query = select(Application.id, Application.name)
    if keyword:
        query = query.where(Application.name.like(f"%{keyword}%"))
    rows = await session.execute(query.order_by(Application.name).limit(100))
    return ok({"items": [{"id": app_id, "name": name} for app_id, name in rows]})


@router.get("/compatible-credentials", summary="配置平台兼容凭据选项")
async def list_compatible_credentials(
    provider: str,
    session: DbSession,
    _: User = Depends(require_perm("config:instance")),
) -> dict:
    allowed = application_config_service._COMPATIBLE_AUTH_TYPES.get(provider)
    if not allowed:
        return ok({"items": []})
    rows = await session.execute(
        select(Credential.id, Credential.name, Credential.auth_type)
        .where(Credential.auth_type.in_(allowed)).order_by(Credential.name)
    )
    return ok({"items": [{"id": item_id, "name": name, "auth_type": auth_type}
                         for item_id, name, auth_type in rows]})


@router.post("/platform-instances", summary="创建配置平台实例")
async def create_platform_instance(
    req: PlatformInstanceCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:instance")),
) -> dict:
    instance = await application_config_service.create_platform_instance(
        session, actor_id=actor.id, **{**req.model_dump(exclude={"base_url"}), "base_url": str(req.base_url)},
    )
    audit.log(module="config", action="instance.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_platform_instance", target_id=str(instance.id),
              target_name=instance.name, detail={"provider": instance.provider})
    return ok({"id": instance.id})


@router.put("/platform-instances/{instance_id}", summary="维护配置平台实例")
async def update_platform_instance(
    instance_id: int,
    req: PlatformInstanceUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:instance")),
) -> dict:
    fields = req.model_dump(exclude_unset=True, exclude={"base_url"})
    if "base_url" in req.model_fields_set:
        fields["base_url"] = str(req.base_url) if req.base_url else None
    instance = await application_config_service.update_platform_instance(session, instance_id, **fields)
    audit.log(module="config", action="instance.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_platform_instance", target_id=str(instance.id),
              target_name=instance.name, detail={"provider": instance.provider, "enabled": instance.enabled})
    return ok(_instance_data(instance))


@router.post("/platform-instances/{instance_id}/probe", summary="测试配置平台连通性")
async def probe_platform_instance(
    instance_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:instance")),
) -> dict:
    instance = await application_config_service.probe_platform_instance(session, instance_id)
    audit.log(module="config", action="instance.probe", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_platform_instance", target_id=str(instance.id),
              target_name=instance.name, detail={"result": instance.last_probe_result})
    return ok(_instance_data(instance))


@router.delete("/platform-instances/{instance_id}", summary="删除停用且未引用的平台实例")
async def delete_platform_instance(
    instance_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:instance")),
) -> dict:
    instance = await application_config_service.delete_platform_instance(session, instance_id)
    audit.log(module="config", action="instance.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_platform_instance", target_id=str(instance.id),
              target_name=instance.name, detail={"provider": instance.provider})
    return ok()


@router.get("/files", summary="配置文件列表")
async def list_config_files(
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
) -> dict:
    rows, total = await application_config_service.list_config_files(
        session, page=page, page_size=page_size, keyword=keyword,
    )
    return ok({"items": [await _file_data(session, item) for item in rows], "total": total,
               "page": page, "page_size": page_size})


@router.get("/platform-instances/{instance_id}/namespaces", summary="获取 Nacos 命名空间选项")
async def list_nacos_namespaces(
    instance_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    namespaces = await application_config_task_service.list_nacos_namespaces(session, instance_id)
    audit.log(module="config", action="namespaces.list", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_platform_instance",
              target_id=str(instance_id), detail={"count": len(namespaces)})
    return ok({"items": namespaces})


@router.post("/discover", summary="在新建流程中发现远端资源")
async def discover_resources(
    req: ConfigDiscoverRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    resources = await application_config_task_service.discover_resources(
        session, req.platform_instance_id, req.query,
    )
    audit.log(module="config", action="discover", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_platform_instance",
              target_id=str(req.platform_instance_id), detail={"count": len(resources)})
    return ok({"items": [{"locator": item.locator, "display_name": item.display_name,
                          "revision": item.revision} for item in resources]})


@router.post("/import-tasks", summary="批量接入已发现配置", status_code=202)
async def create_import_task(
    req: ConfigImportTaskCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    task = await application_config_task_service.create_import_task(
        session, req.platform_instance_id, req.selections,
        application_config_service.ConfigActor(actor.id, actor.username, get_client_ip(request)),
    )
    return ok(application_config_task_service.task_to_dict(task))


@router.get("/import-tasks", summary="最近批量接入任务")
async def list_import_tasks(
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
) -> dict:
    rows = await session.execute(
        select(ConfigTask).where(ConfigTask.kind == ConfigTaskKind.IMPORT.value)
        .order_by(ConfigTask.id.desc()).limit(20)
    )
    items = []
    for task in rows.scalars():
        _, details = await application_config_task_service.get_config_task(session, task.id)
        items.append(application_config_task_service.task_to_dict(task, details))
    return ok({"items": items})


@router.get("/tasks/{task_id}", summary="查询配置任务及逐项进度")
async def get_config_task(
    task_id: int,
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
) -> dict:
    task, items = await application_config_task_service.get_config_task(session, task_id)
    return ok(application_config_task_service.task_to_dict(task, items))


@router.post("/files", summary="新建尚不存在的外部配置文件")
async def create_config_file(
    req: ConfigFileCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    file = await application_config_service.create_config_file(
        session, platform_instance_id=req.platform_instance_id, actor_id=actor.id, **req.selection.model_dump(),
    )
    audit.log(module="config", action="file.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file.id),
              target_name=file.name, detail={"provider_instance_id": file.platform_instance_id,
                                             "format": file.content_format})
    return ok({"id": file.id})


@router.post("/files/{file_id}/archive", summary="归档配置文件")
async def archive_config_file(
    file_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:delete")),
) -> dict:
    file = await application_config_service.archive_config_file(session, file_id)
    audit.log(module="config", action="file.archive", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file.id), target_name=file.name)
    return ok()


@router.get("/files/{file_id}", summary="配置文件详情")
async def get_config_file(
    file_id: int,
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
) -> dict:
    return ok(await _file_data(session, await application_config_service.get_config_file(session, file_id)))


@router.get("/files/{file_id}/tasks", summary="配置文件最近任务")
async def list_file_tasks(
    file_id: int,
    session: DbSession,
    _: User = Depends(require_perm("config:read")),
) -> dict:
    await application_config_service.get_config_file(session, file_id)
    rows = await session.execute(
        select(ConfigTask).where(ConfigTask.config_file_id == file_id).order_by(ConfigTask.id.desc()).limit(50)
    )
    return ok({"items": [application_config_task_service.task_to_dict(task) for task in rows.scalars()]})


@router.put("/files/{file_id}", summary="维护配置文件元数据和关联应用")
async def update_config_file(
    file_id: int,
    req: ConfigFileMetadataRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    file = await application_config_service.update_config_file_metadata(
        session, file_id, **req.model_dump(exclude_unset=True),
    )
    audit.log(module="config", action="file.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file.id), target_name=file.name)
    return ok(await _file_data(session, file))


@router.delete("/files/{file_id}", summary="删除已归档的本地配置记录")
async def delete_config_file(
    file_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:delete")),
) -> dict:
    file = await application_config_service.delete_archived_config_file(session, file_id)
    audit.log(module="config", action="file.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file.id), target_name=file.name)
    return ok()


@router.get("/files/{file_id}/draft", summary="读取或建立草稿")
async def get_draft(
    file_id: int,
    session: DbSession,
    actor: User = Depends(require_perm("config:read")),
) -> dict:
    perms = await rbac_service.get_user_perms(session, actor.id)
    return ok(await application_config_service.draft_to_dict(session, file_id, actor.id, "secret:read" in perms))


@router.put("/files/{file_id}/draft", summary="保存草稿")
async def save_draft(
    file_id: int,
    req: ConfigDraftUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    perms = await rbac_service.get_user_perms(session, actor.id)
    await application_config_service.save_draft(
        session, file_id, req.content, actor_id=actor.id, can_read_secret="secret:read" in perms,
    )
    audit.log(module="config", action="draft.save", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file_id))
    return ok()


@router.delete("/files/{file_id}/draft", summary="丢弃草稿")
async def discard_draft(
    file_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    await application_config_service.discard_draft(session, file_id)
    audit.log(module="config", action="draft.discard", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file_id))
    return ok()


@router.get("/files/{file_id}/versions", summary="配置版本列表")
async def list_versions(
    file_id: int,
    session: DbSession,
    actor: User = Depends(require_perm("config:read")),
) -> dict:
    versions = await application_config_service.list_versions(session, file_id)
    items = []
    for version in versions:
        item = application_config_service.version_to_dict(version)
        item["can_approve"] = (
            version.status == "pending_approval" and version.approval_role_id is not None
            and await application_config_service.can_approve_candidate(session, actor.id, version.approval_role_id)
        )
        items.append(item)
    return ok({"items": items})


@router.get("/files/{file_id}/versions/{version_id}/content", summary="读取按权限脱敏的历史版本正文")
async def get_version_content(
    file_id: int,
    version_id: int,
    session: DbSession,
    actor: User = Depends(require_perm("config:read")),
) -> dict:
    config_file = await application_config_service.get_config_file(session, file_id)
    version = await application_config_service.get_candidate(session, file_id, version_id)
    perms = await rbac_service.get_user_perms(session, actor.id)
    normalized = validate_and_normalize_content(config_file.content_format, decrypt_text(version.content_enc))
    return ok({"version_id": version.id, "content": redact_content(normalized, "secret:read" in perms)})


@router.post("/files/{file_id}/candidates", summary="提交候选版本")
async def submit_candidate(
    file_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    version = await application_config_service.submit_candidate(session, file_id, actor.id)
    audit.log(module="config", action="candidate.submit", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file_id),
              detail={"version_no": version.version_no, "approval_role_id": version.approval_role_id})
    return ok(application_config_service.version_to_dict(version))


@router.post("/files/{file_id}/candidates/{version_id}/approve", summary="按当前角色成员身份审批")
async def approve_candidate(file_id: int, version_id: int, request: Request, session: DbSession, actor: CurrentUser) -> dict:
    version = await application_config_service.decide_candidate(
        session, file_id, version_id, actor.id, approved=True,
    )
    # 审批与任务入队共用请求事务，避免出现已批准却没有发布任务的版本。
    await application_config_task_service.create_publish_task(
        session, file_id, version_id,
        application_config_service.ConfigActor(actor.id, actor.username, get_client_ip(request)),
    )
    audit.log(module="config", action="candidate.approve", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file_id),
              detail={"version_no": version.version_no, "approval_role_id": version.approval_role_id})
    return ok(application_config_service.version_to_dict(version))


@router.post("/files/{file_id}/candidates/{version_id}/reject", summary="驳回候选版本")
async def reject_candidate(
    file_id: int,
    version_id: int,
    req: ConfigCandidateRejectRequest,
    request: Request,
    session: DbSession,
    actor: CurrentUser,
) -> dict:
    version = await application_config_service.decide_candidate(
        session, file_id, version_id, actor.id, approved=False, reason=req.reason,
    )
    audit.log(module="config", action="candidate.reject", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="config_file", target_id=str(file_id),
              detail={"version_no": version.version_no, "approval_role_id": version.approval_role_id})
    return ok(application_config_service.version_to_dict(version))


@router.post("/files/{file_id}/sync", summary="手动同步远端配置", status_code=202)
async def sync_config_file(
    file_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    task = await application_config_task_service.create_sync_task(
        session, file_id, application_config_service.ConfigActor(actor.id, actor.username, get_client_ip(request)),
    )
    return ok(application_config_task_service.task_to_dict(task))


@router.post("/files/{file_id}/versions/{version_id}/publish", summary="发布指定已批准或历史版本", status_code=202)
async def publish_config_version(
    file_id: int,
    version_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
    req: ConfigPublishRequest | None = None,
) -> dict:
    task = await application_config_task_service.create_publish_task(
        session, file_id, version_id,
        application_config_service.ConfigActor(actor.id, actor.username, get_client_ip(request)),
        confirmed_snapshot_id=req.confirmed_snapshot_id if req else None,
    )
    return ok(application_config_task_service.task_to_dict(task))


@router.get("/files/{file_id}/drift", summary="读取脱敏双栏漂移视图")
async def get_drift_view(
    file_id: int,
    session: DbSession,
    actor: User = Depends(require_perm("config:read")),
    version_id: int | None = None,
) -> dict:
    perms = await rbac_service.get_user_perms(session, actor.id)
    return ok(await application_config_task_service.get_drift_view(
        session, file_id, "secret:read" in perms, version_id=version_id,
    ))


@router.post("/files/{file_id}/drift/import", summary="导入外部内容为内部正式版本")
async def import_external_drift(
    file_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("config:write")),
) -> dict:
    version = await application_config_task_service.import_latest_external_snapshot(
        session, file_id, application_config_service.ConfigActor(actor.id, actor.username, get_client_ip(request)),
    )
    return ok(application_config_service.version_to_dict(version))
