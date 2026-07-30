"""本地认证源：查库 + bcrypt 校验。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_providers.base import AuthProvider
from app.core.security import verify_password
from app.models.auth import User


class LocalAuthProvider(AuthProvider):
    """本地账号密码认证（user.source = local）。"""

    source = "local"

    async def authenticate(self, session: AsyncSession, username: str, password: str) -> User | None:
        """按用户名查本地用户并校验 bcrypt；非本地用户交给下一个认证源。"""
        user = (
            await session.execute(
                select(User).where(User.username == username, User.source == "local")
            )
        ).scalar_one_or_none()
        if user is None:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user
