"""数据库管理 API：只读工作台全部通过持久任务交给独立执行器。"""
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from app import audit
from app.core.deps import CurrentUser, DbSession, get_client_ip
from app.core.response import Errors, ok
from app.models.job import Credential
from app.schemas.database_management import ActorContext, DatabaseTaskRequest, InstanceWrite, RoleGrantRequest
from app.services import database_audit_service as audits
from app.services import database_instance_service as instances
from app.services import database_task_service as tasks
from app.services import database_excel
from app.services.database_authorization_service import require_permission
from app.services.database_quota_service import policy
from app.services.database_result_service import read_result_page

router = APIRouter(prefix="/database", tags=["数据库管理"])


def local_time_bound(value: datetime | None) -> datetime | None:
    """前端 ISO 时间带时区，转为与现有 datetime.now() 一致的本地存储时钟。"""
    return value.astimezone().replace(tzinfo=None) if value and value.tzinfo is not None else value


def actor(user, request):
    return ActorContext(user.id, user.username, get_client_ip(request))


def management_audit(user, request, action, instance_id):
    audit.log(module="database", action=action, actor_id=user.id, actor_name=user.username,
              source_ip=get_client_ip(request), target_type="database_instance", target_id=str(instance_id))


def xlsx_response(content, filename):
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# 文件静态路径必须早于 /instances/{instance_id}，避免被动态 ID 路由捕获。
@router.get("/instances/import-template")
async def instance_import_template(session: DbSession, user: CurrentUser):
    await require_permission(session, user.id, "database:import")
    await require_permission(session, user.id, "database:write")
    return xlsx_response(database_excel.build_import_template(), "数据库实例导入模板.xlsx")


@router.post("/instances/import")
async def instance_import(file: UploadFile, request: Request, session: DbSession, user: CurrentUser,
                          upsert: bool = False):
    await require_permission(session, user.id, "database:import")
    await require_permission(session, user.id, "database:write")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise Errors.param("仅支持 .xlsx 文件")
    # 有界读取避免一次将任意上传体积加载到内存；文件内容不进入管理审计。
    actor_id, actor_name = user.id, user.username
    content = await file.read(database_excel.MAX_IMPORT_BYTES+1)
    result = await database_excel.import_instances(session, actor_id, content, upsert=upsert)
    audit.log(module="database", action="instance.import", actor_id=actor_id, actor_name=actor_name,
        source_ip=get_client_ip(request), target_type="database_instance", detail={
            "upsert": upsert, **{key: result[key] for key in ("success_count", "created_count", "updated_count")},
            "failed_count": len(result["failed_rows"])})
    return ok(result)


@router.get("/instances/export")
async def instance_export(request: Request, session: DbSession, user: CurrentUser,
    scope: Literal["management", "query"] = "management", keyword: str | None = Query(None, max_length=255),
    engine: str | None = None, business_line: str | None = None, enabled: bool | None = None,
    cluster_name: str | None = Query(None, max_length=128), probe_status: str | None = None,
    instance_id: int | None = None, app_id: int | None = None, host_id: int | None = None,
    sort_by: Literal["name", "description", "engine", "business_line", "cluster_name", "cluster_role",
                     "version", "address", "apps", "hosts", "status", "created_at"] | None = None,
    sort_order: Literal["asc", "desc"] = "asc"):
    content, count = await database_excel.export_instances(session, user.id, scope=scope,
        keyword=keyword, engine=engine, business_line=business_line, enabled=enabled, cluster_name=cluster_name,
        probe_status=probe_status, instance_id=instance_id, app_id=app_id, host_id=host_id,
        sort_by=sort_by, sort_order=sort_order)
    audit.log(module="database", action="instance.export", actor_id=user.id, actor_name=user.username,
        source_ip=get_client_ip(request), target_type="database_instance", detail={"count": count})
    return xlsx_response(content, "数据库实例列表.xlsx")


@router.get("/instances")
async def list_instances(session: DbSession, user: CurrentUser,
    scope: Literal["management", "query"] = "management", page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100), keyword: str | None = Query(None, max_length=255),
    engine: str | None = None, business_line: str | None = None, enabled: bool | None = None,
    cluster_name: str | None = Query(None, max_length=128),
    probe_status: str | None = None, instance_id: int | None = None, app_id: int | None = None, host_id: int | None = None,
    sort_by: Literal["name", "description", "engine", "business_line", "cluster_name", "cluster_role",
                     "version", "address", "apps", "hosts", "status", "created_at"] | None = None,
    sort_order: Literal["asc", "desc"] = "asc"):
    return ok(await instances.list_instances(session, user.id, scope=scope, page=page, page_size=page_size,
        keyword=keyword, engine=engine, business_line=business_line, enabled=enabled, probe_status=probe_status,
        cluster_name=cluster_name, instance_id=instance_id, app_id=app_id, host_id=host_id,
        sort_by=sort_by, sort_order=sort_order))


@router.get("/compatible-credentials")
async def compatible_credentials(session: DbSession, user: CurrentUser):
    await require_permission(session, user.id, "database:write")
    rows = (await session.execute(select(Credential).where(Credential.auth_type == "username_password").order_by(Credential.name))).scalars()
    return ok({"items": [instances.credential_item(c) for c in rows]})


@router.get("/role-options")
async def role_options(session: DbSession, user: CurrentUser):
    await require_permission(session, user.id, "database:grant")
    return ok({"items": await instances.role_items(session)})


@router.get("/client-policy")
async def client_policy(session: DbSession, user: CurrentUser):
    await require_permission(session, user.id, "database:query")
    limits = await policy(session)
    return ok({key: limits[key] for key in ("execution_timeout_seconds", "max_result_rows", "max_result_bytes", "result_ttl_minutes")})


@router.post("/instances")
async def create_instance(req: InstanceWrite, request: Request, session: DbSession, user: CurrentUser):
    row = await instances.write_instance(session, user.id, req)
    management_audit(user, request, "instance.create", row.id)
    return ok({"id": row.id})


@router.get("/instances/{instance_id}")
async def instance_detail(instance_id: int, session: DbSession, user: CurrentUser):
    return ok(await instances.read_instance(session, user.id, instance_id))


@router.put("/instances/{instance_id}")
async def update_instance(instance_id: int, req: InstanceWrite, request: Request, session: DbSession, user: CurrentUser):
    await instances.write_instance(session, user.id, req, instance_id)
    management_audit(user, request, "instance.update", instance_id)
    return ok()


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: int, request: Request, session: DbSession, user: CurrentUser):
    await instances.delete_instance(session, user.id, instance_id)
    management_audit(user, request, "instance.delete", instance_id)
    return ok()


@router.get("/instances/{instance_id}/roles")
async def get_roles(instance_id: int, session: DbSession, user: CurrentUser):
    await require_permission(session, user.id, "database:grant")
    await instances.get_instance(session, instance_id)
    return ok({"items": await instances.role_items(session, instance_id)})


@router.put("/instances/{instance_id}/roles")
async def put_roles(instance_id: int, req: RoleGrantRequest, request: Request, session: DbSession, user: CurrentUser):
    await instances.replace_roles(session, user.id, instance_id, req.role_ids)
    management_audit(user, request, "instance.grant", instance_id)
    return ok()


@router.post("/tasks")
async def submit(req: DatabaseTaskRequest, request: Request, session: DbSession, user: CurrentUser):
    submission = await tasks.submit_task(session, actor(user, request), req)
    data = {key: getattr(submission, key) for key in ("task_id", "status", "executed_statement", "operation_summary")}
    if submission.reason:
        data["reason"] = submission.reason
    return JSONResponse(status_code=submission.http_status, content=jsonable_encoder({
        "code": submission.error_code, "message": submission.reason or "ok", "data": data}))


@router.get("/tasks")
async def task_history(request: Request, session: DbSession, user: CurrentUser,
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, max_length=255),
    instance_id: int | None = None, engine: str | None = None, status: str | None = None, kind: str | None = None,
    started_from: datetime | None = None, started_to: datetime | None = None):
    return ok(await tasks.list_tasks(session, actor(user, request), page=page, page_size=page_size,
        instance_id=instance_id, engine=engine, status=status, kind=kind, keyword=keyword,
        started_from=local_time_bound(started_from), started_to=local_time_bound(started_to)))


@router.get("/tasks/{task_id}")
async def task_detail(task_id: int, request: Request, session: DbSession, user: CurrentUser):
    task = await tasks.own_task(session, actor(user, request), task_id)
    return ok(await tasks.task_view(session, task))


@router.get("/tasks/{task_id}/result")
async def result(task_id: int, request: Request, session: DbSession, user: CurrentUser,
                 page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
    identity = actor(user, request)
    task = await tasks.own_task(session, identity, task_id, include_expired=True)
    return ok(await read_result_page(session, identity, task, page, page_size))


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: int, request: Request, session: DbSession, user: CurrentUser):
    return ok(await tasks.request_cancel(session, actor(user, request), task_id))


@router.get("/audit/logs")
async def audit_list(session: DbSession, user: CurrentUser, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    actor_id: int | None = None, keyword: str | None = Query(None, max_length=255), source_ip: str | None = None,
    instance_id: int | None = None, engine: str | None = None, database: str | None = None,
    kind: str | None = None, status: str | None = None, started_from: datetime | None = None, started_to: datetime | None = None):
    await require_permission(session, user.id, "database:audit")
    return ok(await audits.list_audit(session, page=page, page_size=page_size, actor_id=actor_id, keyword=keyword,
        source_ip=source_ip, instance_id=instance_id, engine=engine, database=database, kind=kind, status=status,
        started_from=local_time_bound(started_from), started_to=local_time_bound(started_to)))


@router.get("/audit/logs/{task_id}")
async def audit_detail(task_id: int, session: DbSession, user: CurrentUser):
    await require_permission(session, user.id, "database:audit")
    return ok(await audits.audit_detail(session, task_id))
