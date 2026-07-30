"""FastAPI 认证鉴权依赖：get_current_user / require_perm / get_client_ip。

鉴权流水线（02-技术架构 §3.2）：
    Authorization: Bearer <access_token | opsp_个人访问密钥>
    -> JWT: decode_token(scope=access) -> 查用户（状态校验）
    -> opsp_ 前缀: token_service.authenticate（哈希查表 + 过期/状态校验）
    -> require_perm 再查 Redis 权限缓存做权限点裁决
"""
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.response import BizError, Errors
from app.core.security import SCOPE_ACCESS, decode_token
from app.models.auth import User
from app.services import rbac_service, token_service


def get_client_ip(request: Request) -> str | None:
    """取真实客户端 IP：优先 nginx 注入的 X-Forwarded-For 首个地址。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _extract_bearer(request: Request) -> str:
    """从 Authorization 头提取 Bearer token，缺失抛 40101。"""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise BizError(Errors.UNAUTHORIZED, "未登录或凭证缺失", 401)
    return auth[7:].strip()


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """解析 Bearer 凭证并加载用户；禁用/不存在一律 40101。"""
    token = _extract_bearer(request)
    if token.startswith(token_service.TOKEN_PREFIX):
        # 个人访问密钥通道；标记供密钥管理接口拒绝（防密钥自我繁殖）
        request.state.api_token_auth = True
        return await token_service.authenticate(session, token)
    payload = decode_token(token, SCOPE_ACCESS)
    user = await session.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise BizError(Errors.UNAUTHORIZED, "账号不可用", 401)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


def require_perm(perm_code: str):
    """权限点依赖工厂：`Depends(require_perm("user:write"))`，无权限抛 40301。"""

    async def _checker(
        user: CurrentUser,
        session: DbSession,
    ) -> User:
        perms = await rbac_service.get_user_perms(session, user.id)
        if perm_code not in perms:
            raise BizError(Errors.NO_PERM, f"缺少权限: {perm_code}", 403)
        return user

    return _checker
