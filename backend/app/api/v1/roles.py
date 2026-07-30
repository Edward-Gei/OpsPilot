"""角色管理路由（04-API设计 §3）：role:read / role:write 权限点控制。"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.models.auth import Role, User
from app.schemas.rbac import RoleCreateRequest, RoleUpdateRequest
from app.services import rbac_service

router = APIRouter(prefix="/roles", tags=["角色管理"])


@router.get("", summary="角色列表")
async def list_roles(
    session: DbSession,
    _: User = Depends(require_perm("role:read")),
) -> dict:
    """全量角色（含权限码与成员数，角色数量有限不分页）。"""
    return ok({"items": await rbac_service.list_roles(session)})


@router.get("/permissions", summary="权限点全集")
async def list_permissions(
    session: DbSession,
    _: User = Depends(require_perm("role:read")),
) -> dict:
    """权限点清单（角色编辑页按模块分组勾选）。"""
    return ok({"items": await rbac_service.list_permissions(session)})


@router.get("/options", summary="角色选项")
async def list_role_options(
    session: DbSession,
    _: User = Depends(require_perm("template:read")),
) -> dict:
    """仅 id/name 的轻量角色选项：模板编辑器审批节点/可见范围/收件人下拉用，
    不含权限矩阵与成员数（无需 role:read）。"""
    roles = (await session.execute(select(Role).order_by(Role.id))).scalars().all()
    return ok({"items": [{"id": r.id, "name": r.name} for r in roles]})


@router.post("", summary="创建角色")
async def create_role(
    req: RoleCreateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("role:write")),
) -> dict:
    """创建自定义角色并绑定权限点。"""
    role = await rbac_service.create_role(
        session, code=req.code, name=req.name, description=req.description,
        permissions=req.permissions,
    )
    audit.log(module="user", action="role.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="role",
              target_id=str(role.id), target_name=role.name)
    return ok({"id": role.id})


@router.put("/{role_id}", summary="更新角色")
async def update_role(
    role_id: int,
    req: RoleUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("role:write")),
) -> dict:
    """更新角色；内置角色不允许改权限矩阵。"""
    role = await rbac_service.update_role(
        session, role_id, name=req.name, description=req.description, permissions=req.permissions
    )
    audit.log(module="user", action="role.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="role",
              target_id=str(role.id), target_name=role.name,
              detail=req.model_dump(exclude_none=True))
    return ok()


@router.delete("/{role_id}", summary="删除角色")
async def delete_role(
    role_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("role:write")),
) -> dict:
    """删除自定义角色（内置角色/有成员的角色拒绝）。"""
    role = await rbac_service.delete_role(session, role_id)
    audit.log(module="user", action="role.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="role",
              target_id=str(role.id), target_name=role.name)
    return ok()
