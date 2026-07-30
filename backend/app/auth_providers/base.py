"""AuthProvider 抽象基类：认证成功返回本地 User（SSO 首登自动建用户）。"""
from abc import ABC, abstractmethod

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import User


class AuthProvider(ABC):
    """认证源接口：登录流水线按 local → ldap 顺序链式尝试。

    返回约定：
        User  认证成功（含 SSO 首登自动建的本地用户）
        None  本认证源无法认证（如用户不属于该源），交给下一个源
    密码错误等"确定失败"也返回 None，由流水线统一计失败次数。
    """

    #: 认证源标识（对应 user.source 字段）
    source: str

    @abstractmethod
    async def authenticate(self, session: AsyncSession, username: str, password: str) -> User | None:
        """用账号密码认证；子类不抛业务异常，仅返回 User 或 None。"""
        raise NotImplementedError
