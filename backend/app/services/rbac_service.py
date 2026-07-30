"""RBAC 服务：权限集合计算/缓存 + 用户与角色管理（04-API设计 §3/§12）。

权限缓存：auth:perms:{user_id} 存 JSON 权限码数组（TTL 1h），
角色/用户角色变更时主动失效；后端为最终裁决，前端仅控显隐。
"""
import json

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_mod
from app.core.response import Errors
from app.core.security import hash_password
from app.models.auth import Permission, Role, RolePermission, User, UserRole

_PERM_CACHE_TTL = 3600  # 权限缓存 1 小时，变更时主动失效


# ---------- 权限集合计算与缓存 ----------

async def get_user_perms(session: AsyncSession, user_id: int) -> set[str]:
    """取用户权限码集合：优先 Redis 缓存，未命中查库回填。"""
    key = redis_mod.KEY_USER_PERMS.format(user_id=user_id)
    cached = await redis_mod.redis_client.get(key)
    if cached is not None:
        return set(json.loads(cached))
    rows = await session.execute(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
        .distinct()
    )
    perms = {r for r in rows.scalars()}
    await redis_mod.redis_client.set(key, json.dumps(sorted(perms)), ex=_PERM_CACHE_TTL)
    return perms


async def invalidate_user_perms(*user_ids: int) -> None:
    """失效指定用户的权限缓存（用户角色变更时调用）。"""
    if user_ids:
        await redis_mod.redis_client.delete(
            *[redis_mod.KEY_USER_PERMS.format(user_id=uid) for uid in user_ids]
        )


async def invalidate_role_perms(session: AsyncSession, role_id: int) -> None:
    """失效某角色下全部用户的权限缓存（角色权限变更时调用）。"""
    rows = await session.execute(select(UserRole.user_id).where(UserRole.role_id == role_id))
    await invalidate_user_perms(*rows.scalars())


async def get_user_roles(session: AsyncSession, user_id: int) -> list[Role]:
    """取用户的角色列表（/auth/me 与用户列表使用）。"""
    rows = await session.execute(
        select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
    )
    return list(rows.scalars())


# ---------- 用户管理 ----------

async def list_users(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    status: str | None = None,
    source: str | None = None,
) -> tuple[list[User], int]:
    """分页查用户，返回 (列表, 总数)；keyword 模糊匹配用户名/显示名。"""
    query = select(User)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(User.username.like(like) | User.display_name.like(like))
    if status:
        query = query.where(User.status == status)
    if source:
        query = query.where(User.source == source)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(
        query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: str,
    email: str | None,
    role_ids: list[int],
) -> User:
    """创建本地用户（管理员操作）；新用户首登强制改密。"""
    exists = (
        await session.execute(select(User.id).where(User.username == username))
    ).scalar_one_or_none()
    if exists is not None:
        raise Errors.conflict(f"用户名 {username} 已存在")
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=display_name,
        email=email,
        source="local",
        status="active",
        must_change_password=True,  # 管理员代设的初始密码首登必须修改
    )
    session.add(user)
    await session.flush()
    await set_user_roles(session, user.id, role_ids)
    return user


async def update_user(
    session: AsyncSession,
    user_id: int,
    *,
    display_name: str | None = None,
    email: str | None = None,
    status: str | None = None,
    role_ids: list[int] | None = None,
    actor_id: int | None = None,
) -> User:
    """更新用户资料/状态/角色；禁止禁用自己（防止管理员自锁）。"""
    user = await get_user_or_404(session, user_id)
    if status is not None:
        if user_id == actor_id and status == "disabled":
            raise Errors.rejected("不能禁用当前登录账号")
        user.status = status
    if display_name is not None:
        user.display_name = display_name
    if email is not None:
        user.email = email
    if role_ids is not None:
        await set_user_roles(session, user_id, role_ids)
    await session.flush()
    return user


async def reset_user_password(session: AsyncSession, user_id: int, new_password: str) -> User:
    """管理员重置用户密码：写新哈希并置首登改密标记，同时解除锁定。"""
    user = await get_user_or_404(session, user_id)
    if user.source != "local":
        raise Errors.rejected("SSO 用户不支持本地重置密码")
    user.password_hash = hash_password(new_password)
    user.must_change_password = True
    user.locked_until = None
    await session.flush()
    return user


async def set_user_mfa(session: AsyncSession, user_id: int, action: str) -> User:
    """管理员管理用户 MFA（仅 user:mfa 权限，默认只有 admin 角色）。

    enable ：重新启用验证（要求已有密钥，否则无法验证动态码）
    disable：暂停验证但保留密钥，可随时重新开启
    reset  ：清除密钥，用户需重新扫码绑定（换手机/丢失认证器场景）
    """
    user = await get_user_or_404(session, user_id)
    if action == "enable":
        if not user.mfa_secret_enc:
            raise Errors.rejected("该用户尚未绑定认证器，无法开启 MFA")
        user.mfa_enabled = True
    elif action == "disable":
        user.mfa_enabled = False
    else:  # reset
        user.mfa_enabled = False
        user.mfa_secret_enc = None
    await session.flush()
    return user


async def set_user_roles(session: AsyncSession, user_id: int, role_ids: list[int]) -> None:
    """全量重设用户角色（先删后建），并失效该用户权限缓存。"""
    valid_ids = {
        r for r in (await session.execute(select(Role.id).where(Role.id.in_(role_ids or [-1])))).scalars()
    }
    unknown = set(role_ids) - valid_ids
    if unknown:
        raise Errors.param(f"角色不存在: {sorted(unknown)}")
    current = {
        ur.role_id: ur
        for ur in (
            await session.execute(select(UserRole).where(UserRole.user_id == user_id))
        ).scalars()
    }
    for rid, ur in current.items():
        if rid not in valid_ids:
            await session.delete(ur)
    for rid in valid_ids:
        if rid not in current:
            session.add(UserRole(user_id=user_id, role_id=rid))
    await session.flush()
    await invalidate_user_perms(user_id)


async def get_user_or_404(session: AsyncSession, user_id: int) -> User:
    """按 ID 查用户，不存在抛 40401。"""
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise Errors.not_found("用户不存在")
    return user


# ---------- 角色管理 ----------

async def list_roles(session: AsyncSession) -> list[dict]:
    """角色列表（含权限码与成员数，页面一次拉全量，角色数量有限不分页）。"""
    roles = list((await session.execute(select(Role).order_by(Role.id))).scalars())
    perm_rows = await session.execute(
        select(RolePermission.role_id, Permission.code).join(
            Permission, Permission.id == RolePermission.permission_id
        )
    )
    role_perms: dict[int, list[str]] = {}
    for role_id, code in perm_rows:
        role_perms.setdefault(role_id, []).append(code)
    count_rows = await session.execute(
        select(UserRole.role_id, func.count()).group_by(UserRole.role_id)
    )
    member_count = dict(count_rows.all())
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "is_builtin": r.is_builtin,
            "description": r.description,
            "permissions": sorted(role_perms.get(r.id, [])),
            "member_count": member_count.get(r.id, 0),
        }
        for r in roles
    ]


async def create_role(
    session: AsyncSession, *, code: str, name: str, description: str | None, permissions: list[str]
) -> Role:
    """创建自定义角色并绑定权限点。"""
    exists = (await session.execute(select(Role.id).where(Role.code == code))).scalar_one_or_none()
    if exists is not None:
        raise Errors.conflict(f"角色编码 {code} 已存在")
    role = Role(code=code, name=name, is_builtin=False, description=description)
    session.add(role)
    await session.flush()
    await _set_role_permissions(session, role.id, permissions)
    return role


async def update_role(
    session: AsyncSession,
    role_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: list[str] | None = None,
) -> Role:
    """更新角色；内置角色不允许改权限矩阵（可改描述）。"""
    role = await _get_role_or_404(session, role_id)
    if name is not None:
        role.name = name
    if description is not None:
        role.description = description
    if permissions is not None:
        if role.is_builtin:
            raise Errors.rejected("内置角色不允许修改权限")
        await _set_role_permissions(session, role_id, permissions)
        await invalidate_role_perms(session, role_id)
    await session.flush()
    return role


async def delete_role(session: AsyncSession, role_id: int) -> Role:
    """删除自定义角色；内置角色与仍有成员的角色拒绝删除。"""
    role = await _get_role_or_404(session, role_id)
    if role.is_builtin:
        raise Errors.rejected("内置角色不允许删除")
    member = (
        await session.execute(select(UserRole.id).where(UserRole.role_id == role_id).limit(1))
    ).scalar_one_or_none()
    if member is not None:
        raise Errors.rejected("角色下仍有用户，请先移除后再删除")
    for rp in (
        await session.execute(select(RolePermission).where(RolePermission.role_id == role_id))
    ).scalars():
        await session.delete(rp)
    await session.delete(role)
    await session.flush()
    return role


async def _set_role_permissions(session: AsyncSession, role_id: int, permissions: list[str]) -> None:
    """全量重设角色权限点（先比对增删）。"""
    perm_map = {
        p.code: p.id
        for p in (
            await session.execute(select(Permission).where(Permission.code.in_(permissions or ["-"])))
        ).scalars()
    }
    unknown = set(permissions) - set(perm_map)
    if unknown:
        raise Errors.param(f"权限点不存在: {sorted(unknown)}")
    target_ids = set(perm_map.values())
    current = {
        rp.permission_id: rp
        for rp in (
            await session.execute(select(RolePermission).where(RolePermission.role_id == role_id))
        ).scalars()
    }
    for pid, rp in current.items():
        if pid not in target_ids:
            await session.delete(rp)
    for pid in target_ids:
        if pid not in current:
            session.add(RolePermission(role_id=role_id, permission_id=pid))
    await session.flush()


async def _get_role_or_404(session: AsyncSession, role_id: int) -> Role:
    """按 ID 查角色，不存在抛 40401。"""
    role = (await session.execute(select(Role).where(Role.id == role_id))).scalar_one_or_none()
    if role is None:
        raise Errors.not_found("角色不存在")
    return role


async def list_permissions(session: AsyncSession) -> list[dict]:
    """权限点全集（角色编辑页勾选用）。"""
    rows = await session.execute(select(Permission).order_by(Permission.module, Permission.code))
    return [{"code": p.code, "name": p.name, "module": p.module} for p in rows.scalars()]
