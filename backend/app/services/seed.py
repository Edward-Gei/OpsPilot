"""种子数据服务：api 启动时幂等执行（冲突声明 C1 的落地方案）。

内容（03-数据库设计 §10）：
    1. 权限点同步（constants.PERMISSIONS -> permission 表）
    2. 内置角色 + 角色权限矩阵
    3. admin 初始账号（随机密码打印日志，首登强制改密）
    4. system_config 预置键
    5. 通知渠道占位行 + 事件默认渠道映射

V2：审批规则配置在每个模板内部，无全局审批流种子。

并发安全：多副本/多进程同时启动时，用 MySQL GET_LOCK 串行化。
"""
import logging
import secrets

from passlib.hash import bcrypt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import (
    BUILTIN_ROLES,
    DEFAULT_EVENT_CHANNELS,
    NotifyChannelType,
    NotifyEvent,
    PERMISSIONS,
    SYSTEM_CONFIG_DEFAULTS,
)
from app.core.redis import KEY_USER_PERMS, redis_client
from app.models import (
    NotifyChannel,
    NotifyChannelEvent,
    Permission,
    Role,
    RolePermission,
    SystemConfig,
    User,
    UserRole,
)

logger = logging.getLogger("opspilot.seed")

_SEED_LOCK = "opspilot:seed"


async def run_seed(session: AsyncSession) -> None:
    """种子入口：GET_LOCK 串行化后依次执行各幂等步骤。"""
    got = (await session.execute(text("SELECT GET_LOCK(:name, 30)"), {"name": _SEED_LOCK})).scalar()
    if not got:
        logger.warning("获取种子锁超时，跳过本进程种子执行（其他进程正在初始化）")
        return
    try:
        await _sync_permissions(session)
        await _sync_builtin_roles(session)
        await _ensure_admin(session)
        await _ensure_system_config(session)
        await _ensure_notify_defaults(session)
        await session.commit()
        logger.info("种子数据初始化完成")
    finally:
        await session.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _SEED_LOCK})


async def _sync_permissions(session: AsyncSession) -> None:
    """权限点幂等同步：新增补齐、名称/模块变更覆盖；不删除（防止误删自定义引用）。"""
    existing = {p.code: p for p in (await session.execute(select(Permission))).scalars()}
    for code, name, module in PERMISSIONS:
        if code in existing:
            existing[code].name, existing[code].module = name, module
        else:
            session.add(Permission(code=code, name=name, module=module))
    await session.flush()


async def _sync_builtin_roles(session: AsyncSession) -> None:
    """内置角色幂等同步，并按矩阵补齐角色-权限关联（只增不减，权限调整以代码为准新增）。"""
    perm_ids = {
        p.code: p.id for p in (await session.execute(select(Permission))).scalars()
    }
    roles = {r.code: r for r in (await session.execute(select(Role))).scalars()}
    matrix_changed = False
    for code, meta in BUILTIN_ROLES.items():
        role = roles.get(code)
        if role is None:
            role = Role(code=code, name=meta["name"], is_builtin=True, description=meta["description"])
            session.add(role)
            await session.flush()
        # 补齐缺失的权限关联
        bound = {
            rp.permission_id
            for rp in (
                await session.execute(select(RolePermission).where(RolePermission.role_id == role.id))
            ).scalars()
        }
        for perm_code in meta["permissions"]:
            pid = perm_ids[perm_code]
            if pid not in bound:
                session.add(RolePermission(role_id=role.id, permission_id=pid))
                matrix_changed = True
    await session.flush()
    if matrix_changed:
        # 矩阵有变更（如升级新增权限点）：清空权限缓存，避免老缓存最长 1h 内遮蔽新权限
        try:
            keys = await redis_client.keys(KEY_USER_PERMS.format(user_id="*"))
            if keys:
                await redis_client.delete(*keys)
        except Exception:  # noqa: BLE001
            logger.warning("权限缓存清理失败（不影响启动，缓存将随 TTL 自然过期）")


async def _ensure_admin(session: AsyncSession) -> None:
    """创建 admin 初始账号：密码取 ADMIN_INITIAL_PASSWORD，未配置则随机生成并打印日志。"""
    exists = (await session.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
    if exists:
        return
    password = settings.admin_initial_password or secrets.token_urlsafe(12)
    admin = User(
        username="admin",
        password_hash=bcrypt.hash(password),
        display_name="系统管理员",
        source="local",
        status="active",
        must_change_password=True,  # 首次登录强制改密（PRD AUTH-06）
    )
    session.add(admin)
    await session.flush()
    admin_role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one()
    session.add(UserRole(user_id=admin.id, role_id=admin_role.id))
    # 初始密码只输出到容器日志，不落库明文
    logger.warning("已创建初始管理员账号 admin，初始密码: %s （首次登录须修改）", password)


async def _ensure_system_config(session: AsyncSession) -> None:
    """system_config 预置键：只补缺失键，不覆盖已有值。"""
    existing_keys = {
        row for row in (await session.execute(select(SystemConfig.cfg_key))).scalars()
    }
    for key, value in SYSTEM_CONFIG_DEFAULTS.items():
        if key not in existing_keys:
            session.add(SystemConfig(cfg_key=key, cfg_value=value))


async def _ensure_notify_defaults(session: AsyncSession) -> None:
    """通知渠道占位行（6 类型全建、默认禁用）+ 事件默认映射（仅落地渠道）。"""
    existing_channels = {
        c.type for c in (await session.execute(select(NotifyChannel))).scalars()
    }
    for channel_type in NotifyChannelType:
        if channel_type.value not in existing_channels:
            session.add(NotifyChannel(type=channel_type.value, enabled=False))
    existing_events = {
        (e.event, e.channel_type)
        for e in (await session.execute(select(NotifyChannelEvent))).scalars()
    }
    for event in NotifyEvent:
        for channel_type in DEFAULT_EVENT_CHANNELS:
            if (event.value, channel_type) not in existing_events:
                session.add(NotifyChannelEvent(event=event.value, channel_type=channel_type))
