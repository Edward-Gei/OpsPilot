"""用户/角色管理请求模型（04-API设计 §3）。"""
from typing import Literal

from pydantic import BaseModel, Field


class UserCreateRequest(BaseModel):
    """创建本地用户（管理员操作，初始密码首登强制修改）。"""
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    role_ids: list[int] = []


class UserUpdateRequest(BaseModel):
    """更新用户：仅传需要修改的字段。"""
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    status: str | None = Field(default=None, pattern="^(active|disabled)$")
    role_ids: list[int] | None = None


class UserResetPasswordRequest(BaseModel):
    """管理员重置用户密码。"""
    new_password: str = Field(min_length=1, max_length=128)


class UserMfaRequest(BaseModel):
    """管理员管理用户 MFA：开启/关闭（保留密钥）/重置（清除密钥待重新绑定）。"""
    action: Literal["enable", "disable", "reset"]


class RoleCreateRequest(BaseModel):
    """创建自定义角色。"""
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] = []


class RoleUpdateRequest(BaseModel):
    """更新角色（内置角色仅允许改名称/描述）。"""
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None


class ConfigUpdateRequest(BaseModel):
    """系统配置更新：批量键值（仅允许预置键）。"""
    configs: dict = Field(min_length=1)

