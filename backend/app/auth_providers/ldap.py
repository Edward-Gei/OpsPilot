"""LDAP 认证源：ldap3 绑定校验，首登自动建本地用户（PRD AUTH-07）。

配置来自 system_config `ldap.config`（未配置则本源直接跳过）：
    {
      "server_url": "ldap://host:389",
      "bind_dn": "cn=svc,dc=corp,dc=com",     # 服务账号（搜索用户 DN 用）
      "bind_password": "***",
      "base_dn": "ou=people,dc=corp,dc=com",
      "user_filter": "(uid={username})",       # {username} 占位
      "attr_display_name": "cn",
      "attr_email": "mail"
    }
"""
import asyncio
import logging

from ldap3 import ALL, Connection, Server
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_providers.base import AuthProvider
from app.models.auth import Role, User, UserRole
from app.services import config_service

logger = logging.getLogger("opspilot.auth.ldap")


class LdapAuthProvider(AuthProvider):
    """LDAP 账号密码认证（user.source = ldap）。"""

    source = "ldap"

    async def authenticate(self, session: AsyncSession, username: str, password: str) -> User | None:
        """服务账号搜索用户 DN -> 用户凭据重绑定校验 -> 首登建用户。"""
        cfg = await config_service.get_config(session, "ldap.config")
        if not cfg or not cfg.get("server_url"):
            return None  # 未启用 LDAP，跳过
        # ldap3 为同步库，放线程池执行避免阻塞事件循环
        try:
            entry = await asyncio.to_thread(self._bind_and_search, cfg, username, password)
        except Exception as exc:  # noqa: BLE001 LDAP 服务器异常按认证失败处理
            logger.warning("LDAP 认证异常: %s", exc)
            return None
        if entry is None:
            return None
        return await self._get_or_create_user(session, username, entry, cfg)

    @staticmethod
    def _bind_and_search(cfg: dict, username: str, password: str) -> dict | None:
        """同步部分：搜索用户条目并用其 DN + 密码重绑定验证。返回属性字典。"""
        server = Server(cfg["server_url"], get_info=ALL, connect_timeout=5)
        attrs = [cfg.get("attr_display_name", "cn"), cfg.get("attr_email", "mail")]
        with Connection(
            server, user=cfg.get("bind_dn"), password=cfg.get("bind_password"),
            auto_bind=True, receive_timeout=5,
        ) as conn:
            conn.search(
                cfg["base_dn"],
                cfg.get("user_filter", "(uid={username})").format(username=username),
                attributes=attrs,
            )
            if not conn.entries:
                return None
            entry = conn.entries[0]
            user_dn = entry.entry_dn
        # 用户凭据重绑定：成功即密码正确
        with Connection(server, user=user_dn, password=password, receive_timeout=5) as user_conn:
            if not user_conn.bind():
                return None
        return {
            "dn": user_dn,
            "display_name": str(entry[cfg.get("attr_display_name", "cn")].value or username),
            "email": (str(entry[cfg.get("attr_email", "mail")].value)
                      if entry[cfg.get("attr_email", "mail")].value else None),
        }

    async def _get_or_create_user(
        self, session: AsyncSession, username: str, entry: dict, cfg: dict
    ) -> User:
        """LDAP 首登自动建用户：source=ldap + 默认角色（ldap.default_role）。"""
        user = (
            await session.execute(
                select(User).where(User.username == username, User.source == "ldap")
            )
        ).scalar_one_or_none()
        if user is not None:
            return user
        user = User(
            username=username,
            password_hash=None,  # SSO 用户无本地密码
            display_name=entry["display_name"],
            email=entry["email"],
            source="ldap",
            external_id=entry["dn"],
            status="active",
        )
        session.add(user)
        await session.flush()
        role_code = await config_service.get_config(session, "ldap.default_role")
        role = (
            await session.execute(select(Role).where(Role.code == (role_code or "ops")))
        ).scalar_one_or_none()
        if role is not None:
            session.add(UserRole(user_id=user.id, role_id=role.id))
        logger.info("LDAP 首次登录自动创建用户: %s（默认角色 %s）", username, role_code)
        return user
