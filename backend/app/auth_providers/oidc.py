"""OIDC 认证源：授权码模式（02-技术架构 §3.1，冲突决策⑤）。

流程：
    GET /auth/sso/oidc/login   -> build_authorize_url() 生成跳转 + state(Redis 防伪)
    GET /auth/sso/oidc/callback -> handle_callback() 校验 state、换 token、拉 userinfo、
                                   按 sub 映射/首登建用户，返回 User
配置来自 system_config `oidc.config`：
    {
      "authorize_endpoint": "...", "token_endpoint": "...", "userinfo_endpoint": "...",
      "client_id": "...", "client_secret": "...",
      "redirect_uri": "http://host/api/v1/auth/sso/oidc/callback",
      "scope": "openid profile email"
    }
"""
import logging
import secrets
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_mod
from app.core.response import Errors
from app.models.auth import Role, User, UserRole
from app.services import config_service

logger = logging.getLogger("opspilot.auth.oidc")

_STATE_TTL = 600  # state 有效期（秒）


async def get_oidc_config(session: AsyncSession) -> dict:
    """读取 OIDC 配置，未配置抛业务拒绝（前端据此隐藏 SSO 按钮）。"""
    cfg = await config_service.get_config(session, "oidc.config")
    if not cfg or not cfg.get("authorize_endpoint"):
        raise Errors.rejected("OIDC 未配置")
    return cfg


async def build_authorize_url(session: AsyncSession) -> str:
    """生成授权跳转 URL；state 写 Redis 防 CSRF/重放。"""
    cfg = await get_oidc_config(session)
    state = secrets.token_urlsafe(24)
    await redis_mod.redis_client.set(
        redis_mod.KEY_OIDC_STATE.format(state=state), "1", ex=_STATE_TTL
    )
    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": cfg.get("scope", "openid profile email"),
        "state": state,
    }
    return f"{cfg['authorize_endpoint']}?{urlencode(params)}"


async def handle_callback(session: AsyncSession, code: str, state: str) -> User:
    """回调处理：state 一次性校验 -> 换 token -> userinfo -> 映射/建用户。"""
    key = redis_mod.KEY_OIDC_STATE.format(state=state)
    if not await redis_mod.redis_client.delete(key):  # delete 返回 0 即 state 无效/已用
        raise Errors.param("OIDC state 无效或已过期")
    cfg = await get_oidc_config(session)
    userinfo = await _fetch_userinfo(cfg, code)
    sub = userinfo.get("sub")
    if not sub:
        raise Errors.rejected("OIDC userinfo 缺少 sub 字段")
    return await _get_or_create_user(session, sub, userinfo)


async def _fetch_userinfo(cfg: dict, code: str) -> dict:
    """授权码换 access_token 后请求 userinfo 端点。"""
    async with httpx.AsyncClient(timeout=10) as client:
        token_resp = await client.post(
            cfg["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": cfg["redirect_uri"],
                "client_id": cfg["client_id"],
                "client_secret": cfg.get("client_secret", ""),
            },
        )
        if token_resp.status_code != 200:
            logger.warning("OIDC 换取 token 失败: %s %s", token_resp.status_code, token_resp.text[:200])
            raise Errors.rejected("OIDC 换取令牌失败")
        access_token = token_resp.json().get("access_token")
        info_resp = await client.get(
            cfg["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"}
        )
        if info_resp.status_code != 200:
            raise Errors.rejected("OIDC 获取用户信息失败")
        return info_resp.json()


async def _get_or_create_user(session: AsyncSession, sub: str, userinfo: dict) -> User:
    """按 sub 匹配已有 OIDC 用户；首登自动建用户 + 默认角色（oidc.default_role）。"""
    user = (
        await session.execute(
            select(User).where(User.source == "oidc", User.external_id == sub)
        )
    ).scalar_one_or_none()
    if user is not None:
        return user
    # 用户名优先 preferred_username / email 前缀，冲突时追加 sub 前缀保证唯一
    username = userinfo.get("preferred_username") or (userinfo.get("email") or f"oidc_{sub}").split("@")[0]
    exists = (
        await session.execute(select(User.id).where(User.username == username))
    ).scalar_one_or_none()
    if exists is not None:
        username = f"{username}_{sub[:8]}"
    user = User(
        username=username,
        password_hash=None,
        display_name=userinfo.get("name") or username,
        email=userinfo.get("email"),
        source="oidc",
        external_id=sub,
        status="active",
    )
    session.add(user)
    await session.flush()
    role_code = await config_service.get_config(session, "oidc.default_role")
    role = (
        await session.execute(select(Role).where(Role.code == (role_code or "ops")))
    ).scalar_one_or_none()
    if role is not None:
        session.add(UserRole(user_id=user.id, role_id=role.id))
    logger.info("OIDC 首次登录自动创建用户: %s（默认角色 %s）", username, role_code)
    return user
