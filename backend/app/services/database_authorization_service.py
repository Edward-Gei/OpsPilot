"""数据库操作使用实时数据库授权，撤权不受 Redis 权限缓存延迟影响。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BizError, Errors
from app.models.auth import Permission, RolePermission, User, UserRole
from app.models.database_management import DatabaseInstance, DatabaseRoleInstance

MANAGEMENT_PERMS = {"database:read", "database:write", "database:delete", "database:grant"}


async def current_permissions(session: AsyncSession, user_id: int) -> set[str]:
    """每次执行/读结果重新确认账号状态与功能权限。"""
    user = await session.get(User, user_id, populate_existing=True)
    if user is None or user.status != "active":
        raise BizError(Errors.NO_PERM, "账号不可用", 403)
    return set((await session.execute(
        select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id).where(UserRole.user_id == user_id)
    )).scalars())


def granted_instance_ids(user_id: int):
    """多角色取并集，不依赖管理员角色或应用关系。"""
    return select(DatabaseRoleInstance.instance_id).join(
        UserRole, UserRole.role_id == DatabaseRoleInstance.role_id
    ).where(UserRole.user_id == user_id)


async def require_permission(session: AsyncSession, user_id: int, permission: str):
    if permission not in await current_permissions(session, user_id):
        raise BizError(Errors.NO_PERM, f"缺少权限: {permission}", 403)


async def require_query_instance(session: AsyncSession, user_id: int, instance_id: int,
                                 *, require_enabled: bool = True) -> DatabaseInstance:
    """先裁剪对象范围再取详情，错误不披露未授权对象。"""
    await require_permission(session, user_id, "database:query")
    instance = (await session.execute(select(DatabaseInstance).where(
        DatabaseInstance.id == instance_id, DatabaseInstance.id.in_(granted_instance_ids(user_id))
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if instance is None:
        raise BizError(Errors.OBJECT_DENIED, "实例不存在或未授权", 403)
    if require_enabled and not instance.enabled:
        raise Errors.rejected("实例已停用")
    return instance
