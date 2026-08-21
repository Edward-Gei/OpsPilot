"""凭据路由（04-API设计 §5）：credential:read / credential:write 权限点控制。

安全红线（PRD CRED-01）：任何响应不含 secret / passphrase 明文或密文；
审计 detail 同样只落是否变更密文的布尔标记。
"""
from fastapi import APIRouter, Depends, Query, Request

from app import audit
from app.core.deps import CurrentUser, DbSession, get_client_ip
from app.core.response import BizError, Errors, ok
from app.models.auth import User
from app.schemas.job import CredentialCreateRequest, CredentialUpdateRequest
from app.services import credential_service, rbac_service

router = APIRouter(prefix="/credentials", tags=["凭据"])


def _cred_brief(c) -> dict:
    """凭据统一序列化：永不输出密文字段。"""
    return {
        "id": c.id,
        "name": c.name,
        "login_user": c.login_user,
        "auth_type": c.auth_type,
        "has_passphrase": bool(c.passphrase_enc),
        "file_name": c.file_name,
        "description": c.description,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


@router.get("", summary="凭据列表")
async def list_credentials(
    session: DbSession,
    actor: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    auth_type: str | None = None,
) -> dict:
    """分页查凭据（不含密文；ops 可见名称用于工单选择）。"""
    perms = await rbac_service.get_user_perms(session, actor.id)
    allowed_types = set()
    if "credential:read" in perms:
        allowed_types.update(credential_service.SSH_CREDENTIAL_TYPES)
    if "secret:read" in perms:
        allowed_types.update(credential_service.SCRIPT_SECRET_TYPES)
    if not allowed_types:
        raise BizError(Errors.NO_PERM, "缺少凭据查看权限", 403)
    if auth_type and auth_type not in allowed_types:
        raise BizError(Errors.NO_PERM, "无权查看该凭据类型", 403)
    creds, total = await credential_service.list_credentials(
        session, page=page, page_size=page_size, keyword=keyword, auth_type=auth_type,
        auth_types=allowed_types,
    )
    return ok({"items": [_cred_brief(c) for c in creds], "total": total,
               "page": page, "page_size": page_size})


@router.post("", summary="新建凭据")
async def create_credential(
    req: CredentialCreateRequest,
    request: Request,
    session: DbSession,
    actor: CurrentUser,
) -> dict:
    """新建凭据；名称冲突 40901（仅管理员，CRED-02）。"""
    perms = await rbac_service.get_user_perms(session, actor.id)
    required_perm = "secret:write" if req.auth_type in credential_service.SCRIPT_SECRET_TYPES else "credential:write"
    if required_perm not in perms:
        raise BizError(Errors.NO_PERM, f"缺少权限: {required_perm}", 403)
    cred = await credential_service.create_credential(
        session, created_by=actor.id, **req.model_dump()
    )
    audit.log(module="job", action="credential.create", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="credential", target_id=str(cred.id), target_name=cred.name,
              detail={"login_user": cred.login_user, "auth_type": cred.auth_type})
    return ok({"id": cred.id})


@router.put("/{credential_id}", summary="编辑凭据")
async def update_credential(
    credential_id: int,
    req: CredentialUpdateRequest,
    request: Request,
    session: DbSession,
    actor: CurrentUser,
) -> dict:
    """编辑凭据；secret 传空 = 不变更密文。"""
    existing = await credential_service.get_credential_or_404(session, credential_id)
    perms = await rbac_service.get_user_perms(session, actor.id)
    required_perm = "secret:write" if credential_service.is_script_secret(existing) else "credential:write"
    if required_perm not in perms:
        raise BizError(Errors.NO_PERM, f"缺少权限: {required_perm}", 403)
    cred, secret_changed = await credential_service.update_credential(
        session, credential_id, **req.model_dump()
    )
    audit.log(module="job", action="credential.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="credential", target_id=str(cred.id), target_name=cred.name,
              detail={"login_user": cred.login_user, "auth_type": cred.auth_type,
                      "secret_changed": secret_changed})
    return ok()


@router.delete("/{credential_id}", summary="删除凭据")
async def delete_credential(
    credential_id: int,
    request: Request,
    session: DbSession,
    actor: CurrentUser,
) -> dict:
    """删除凭据；被作业主机或模板引用时 42201。"""
    existing = await credential_service.get_credential_or_404(session, credential_id)
    perms = await rbac_service.get_user_perms(session, actor.id)
    required_perm = "secret:delete" if credential_service.is_script_secret(existing) else "credential:delete"
    if required_perm not in perms:
        raise BizError(Errors.NO_PERM, f"缺少权限: {required_perm}", 403)
    cred = await credential_service.delete_credential(session, credential_id)
    audit.log(module="job", action="credential.delete", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="credential", target_id=str(credential_id), target_name=cred.name)
    return ok()
