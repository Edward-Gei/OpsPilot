"""个人访问密钥服务：创建 / 列表 / 删除 + `Bearer opsp_xxx` 鉴权。

安全约束（04-API设计 §2.9）：
- 库中仅存 SHA-256 哈希，明文只在创建成功时返回一次；
- 权限与所属用户实时一致（禁用账号 / 删除密钥立即失效）；
- 每人最多 10 个，last_used_at 60 秒节流更新减少写库。
"""
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BizError, Errors
from app.models.auth import ApiToken, User

TOKEN_PREFIX = "opsp_"
MAX_TOKENS_PER_USER = 10
_LAST_USED_THROTTLE = timedelta(seconds=60)


def _hash(token: str) -> str:
    """令牌明文 -> SHA-256 十六进制（定长 64，查表用）。"""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_token(
    session: AsyncSession, user: User, *, name: str, expires_in_days: int | None
) -> tuple[ApiToken, str]:
    """创建密钥，返回 (记录, 明文)；明文仅此一次，之后无法找回。"""
    count = (
        await session.execute(
            select(func.count()).select_from(ApiToken).where(ApiToken.user_id == user.id)
        )
    ).scalar() or 0
    if count >= MAX_TOKENS_PER_USER:
        raise Errors.rejected(f"每人最多创建 {MAX_TOKENS_PER_USER} 个访问密钥")
    plaintext = TOKEN_PREFIX + secrets.token_urlsafe(32)
    record = ApiToken(
        user_id=user.id,
        name=name,
        token_hash=_hash(plaintext),
        prefix=plaintext[:12],
        expires_at=datetime.now() + timedelta(days=expires_in_days) if expires_in_days else None,
    )
    session.add(record)
    await session.flush()
    # MySQL 无 INSERT RETURNING，显式回读 server_default 的 created_at，避免序列化时惰性 IO
    await session.refresh(record)
    return record, plaintext


async def list_tokens(session: AsyncSession, user_id: int) -> list[ApiToken]:
    """本人密钥列表（新建在前）。"""
    rows = await session.execute(
        select(ApiToken).where(ApiToken.user_id == user_id).order_by(ApiToken.id.desc())
    )
    return list(rows.scalars())


async def delete_token(session: AsyncSession, user_id: int, token_id: int) -> ApiToken:
    """删除本人密钥；不存在或非本人一律 404（不泄露他人密钥存在性）。"""
    record = await session.get(ApiToken, token_id)
    if record is None or record.user_id != user_id:
        raise Errors.not_found("访问密钥不存在")
    await session.delete(record)
    await session.flush()
    return record


async def authenticate(session: AsyncSession, token: str) -> User:
    """按明文令牌鉴权：哈希查表 -> 校验过期 -> 加载用户（状态校验）。"""
    record = (
        await session.execute(select(ApiToken).where(ApiToken.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if record is None:
        raise BizError(Errors.UNAUTHORIZED, "访问密钥无效", 401)
    now = datetime.now()
    if record.expires_at is not None and record.expires_at <= now:
        raise BizError(Errors.UNAUTHORIZED, "访问密钥已过期", 401)
    user = await session.get(User, record.user_id)
    if user is None or user.status != "active":
        raise BizError(Errors.UNAUTHORIZED, "账号不可用", 401)
    if record.last_used_at is None or now - record.last_used_at >= _LAST_USED_THROTTLE:
        record.last_used_at = now
        await session.flush()
    return user
