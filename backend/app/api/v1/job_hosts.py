"""作业主机路由（执行范式改造）：job_host:read / job_host:write 权限点控制。

安全红线（与凭据模块一致）：任何响应不含 secret / passphrase 明文或密文；
审计 detail 同样只落是否变更密文的布尔标记。
"""
from fastapi import APIRouter, Depends, Query, Request

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.models.auth import User
from app.schemas.job import (
    JobHostCreateRequest,
    JobHostDetailResponse,
    JobHostStatusRequest,
    JobHostUpdateRequest,
)
from app.services import job_host_service

router = APIRouter(prefix="/job-hosts", tags=["作业主机"])


def _detail(jh, cred_names: dict[int, str] | None = None) -> dict:
    """作业主机统一序列化：认证信息只暴露关联凭据 id/名称，永无密文。"""
    d = JobHostDetailResponse.model_validate(jh).model_dump(mode="json")
    d["credential_name"] = (cred_names or {}).get(jh.credential_id)
    return d


@router.get("", summary="作业主机列表")
async def list_job_hosts(
    session: DbSession,
    _: User = Depends(require_perm("job_host:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    enabled: bool | None = None,
) -> dict:
    """分页查作业主机；keyword 模糊匹配名称/IP，enabled 筛选启用状态。"""
    hosts, total = await job_host_service.list_job_hosts(
        session, keyword=keyword, enabled=enabled, page=page, page_size=page_size
    )
    cred_names = await job_host_service.credential_names(
        session, [jh.credential_id for jh in hosts]
    )
    return ok({"items": [_detail(jh, cred_names) for jh in hosts], "total": total,
               "page": page, "page_size": page_size})


@router.post("", summary="新建作业主机")
async def create_job_host(
    req: JobHostCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("job_host:write")),
) -> dict:
    """新建作业主机；IP+端口重复 42201，登录认证引用凭据（credential_id）。"""
    jh = await job_host_service.create_job_host(
        session, data=req.model_dump(), created_by=actor.id
    )
    audit.log(module="job", action="job_host.create", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="job_host", target_id=str(jh.id), target_name=jh.name,
              detail={"ip": jh.ip, "ssh_port": jh.ssh_port,
                      "credential_id": jh.credential_id})
    return ok({"id": jh.id})


@router.get("/{job_host_id}", summary="作业主机详情")
async def get_job_host(
    job_host_id: int,
    session: DbSession,
    _: User = Depends(require_perm("job_host:read")),
) -> dict:
    """按 ID 取作业主机详情（不含密文），不存在 40401。"""
    jh = await job_host_service.get_job_host(session, job_host_id)
    return ok(_detail(jh, await job_host_service.credential_names(session, [jh.credential_id])))


@router.put("/{job_host_id}", summary="编辑作业主机")
async def update_job_host(
    job_host_id: int,
    req: JobHostUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("job_host:write")),
) -> dict:
    """编辑作业主机；credential_id 传值即切换关联凭据。"""
    payload = req.model_dump(exclude_none=True)
    jh = await job_host_service.update_job_host(session, job_host_id, data=payload)
    audit.log(module="job", action="job_host.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="job_host", target_id=str(jh.id), target_name=jh.name,
              detail={"ip": jh.ip, "ssh_port": jh.ssh_port,
                      "credential_id": jh.credential_id})
    return ok(_detail(jh, await job_host_service.credential_names(session, [jh.credential_id])))


@router.delete("/{job_host_id}", summary="删除作业主机")
async def delete_job_host(
    job_host_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("job_host:write")),
) -> dict:
    """删除作业主机；被工单模板引用时 42201。"""
    jh = await job_host_service.delete_job_host(session, job_host_id)
    audit.log(module="job", action="job_host.delete", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="job_host", target_id=str(job_host_id), target_name=jh.name)
    return ok()


@router.post("/{job_host_id}/test", summary="连通性测试")
async def test_connectivity(
    job_host_id: int,
    session: DbSession,
    _: User = Depends(require_perm("job_host:write")),
) -> dict:
    """SSH 连接作业主机执行 echo ok，结果落 last_check_* 字段。"""
    result = await job_host_service.test_connectivity(session, job_host_id)
    return ok({"ok": result["ok"], "message": result["message"],
               "checked_at": result["checked_at"].isoformat()})


@router.put("/{job_host_id}/status", summary="启用/禁用作业主机")
async def set_enabled(
    job_host_id: int,
    req: JobHostStatusRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("job_host:write")),
) -> dict:
    """启用/禁用作业主机；禁用后不可被新模板选用。"""
    jh = await job_host_service.set_enabled(session, job_host_id, req.enabled)
    audit.log(module="job", action="job_host.status", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="job_host", target_id=str(jh.id), target_name=jh.name,
              detail={"enabled": jh.enabled})
    return ok(_detail(jh, await job_host_service.credential_names(session, [jh.credential_id])))
