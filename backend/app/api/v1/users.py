"""用户管理路由（04-API设计 §3）：user:read / user:write 权限点控制。"""
from fastapi import APIRouter, Depends, Query, Request

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.models.auth import User
from app.schemas.rbac import (
    UserCreateRequest,
    UserMfaRequest,
    UserResetPasswordRequest,
    UserUpdateRequest,
)
from app.services import rbac_service

router = APIRouter(prefix="/users", tags=["用户管理"])


def _user_brief(u: User, roles: list) -> dict:
    """用户列表/详情的统一序列化。"""
    return {
        "id": u.id,
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "source": u.source,
        "status": u.status,
        "mfa_enabled": u.mfa_enabled,
        "mfa_bound": bool(u.mfa_secret_enc),  # 已有密钥（即使验证被管理员暂停）
        "must_change_password": u.must_change_password,
        "locked_until": u.locked_until.isoformat() if u.locked_until else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "roles": [{"id": r.id, "code": r.code, "name": r.name} for r in roles],
    }


@router.get("", summary="用户列表")
async def list_users(
    session: DbSession,
    _: User = Depends(require_perm("user:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> dict:
    """分页查用户（keyword 模糊匹配用户名/显示名）。"""
    users, total = await rbac_service.list_users(
        session, page=page, page_size=page_size, keyword=keyword, status=status, source=source
    )
    items = [_user_brief(u, await rbac_service.get_user_roles(session, u.id)) for u in users]
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.post("", summary="创建用户")
async def create_user(
    req: UserCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("user:write")),
) -> dict:
    """创建本地用户；初始密码首登强制修改。"""
    user = await rbac_service.create_user(
        session,
        username=req.username,
        password=req.password,
        display_name=req.display_name,
        email=req.email,
        role_ids=req.role_ids,
    )
    audit.log(module="user", action="user.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="user",
              target_id=str(user.id), target_name=user.username)
    return ok({"id": user.id})


@router.put("/{user_id}", summary="更新用户")
async def update_user(
    user_id: int,
    req: UserUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("user:write")),
) -> dict:
    """更新资料/状态/角色；禁止禁用自己。"""
    user = await rbac_service.update_user(
        session, user_id,
        display_name=req.display_name, email=req.email,
        status=req.status, role_ids=req.role_ids, actor_id=actor.id,
    )
    audit.log(module="user", action="user.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="user",
              target_id=str(user.id), target_name=user.username,
              detail=req.model_dump(exclude_none=True))
    return ok()


@router.put("/{user_id}/password", summary="重置用户密码")
async def reset_password(
    user_id: int,
    req: UserResetPasswordRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("user:write")),
) -> dict:
    """管理员重置密码：写新哈希 + 首登改密标记 + 解锁。"""
    user = await rbac_service.reset_user_password(session, user_id, req.new_password)
    audit.log(module="user", action="user.reset_password", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="user", target_id=str(user.id), target_name=user.username)
    return ok()


@router.put("/{user_id}/mfa", summary="管理用户MFA")
async def manage_user_mfa(
    user_id: int,
    req: UserMfaRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("user:mfa")),
) -> dict:
    """管理员开启/关闭/重置用户 MFA（仅 user:mfa 权限，默认仅 admin 角色）。"""
    user = await rbac_service.set_user_mfa(session, user_id, req.action)
    audit.log(module="user", action=f"user.mfa.{req.action}", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="user", target_id=str(user.id), target_name=user.username)
    return ok()
