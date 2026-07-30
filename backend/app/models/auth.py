"""登录鉴权域模型：用户 / 角色 / 权限（03-数据库设计 §3）。"""
from datetime import datetime

from sqlalchemy import Boolean, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column, updated_at_column


class User(Base):
    """用户表：本地与 SSO（LDAP/OIDC）账号统一存储。"""

    __tablename__ = "user"
    __table_args__ = (
        Index("idx_user_source_external", "source", "external_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="登录名")
    password_hash: Mapped[str | None] = mapped_column(String(255), comment="bcrypt 哈希，SSO 用户为空")
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名")
    email: Mapped[str | None] = mapped_column(String(128), comment="邮箱（通知收件地址）")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="local", comment="local/ldap/oidc")
    external_id: Mapped[str | None] = mapped_column(String(128), comment="SSO 外部唯一标识")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="active/disabled")
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否已绑定 MFA")
    mfa_secret_enc: Mapped[str | None] = mapped_column(String(512), comment="TOTP 密钥（AES-GCM 加密）")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="首登强制改密")
    locked_until: Mapped[datetime | None] = mapped_column(DT3, comment="锁定截止时间")
    last_login_at: Mapped[datetime | None] = mapped_column(DT3, comment="最近登录时间")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Role(Base):
    """角色表：4 个内置角色 + 自定义角色。"""

    __tablename__ = "role"

    id: Mapped[int] = pk_column()
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="角色编码")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="角色名称")
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="内置角色不可删/不可改权限")
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Permission(Base):
    """权限点表：代码常量启动时幂等同步（constants.PERMISSIONS）。"""

    __tablename__ = "permission"

    id: Mapped[int] = pk_column()
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="权限点编码，如 cmdb:write")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="显示名")
    module: Mapped[str] = mapped_column(String(32), nullable=False, comment="所属模块")


class UserRole(Base):
    """用户-角色关联（多对多）。"""

    __tablename__ = "user_role"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uk_user_role"),
        Index("idx_user_role_role_id", "role_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    user_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    role_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ApiToken(Base):
    """个人访问令牌：`Bearer opsp_xxx` 直接调用平台 API，库中仅存 SHA-256 哈希。"""

    __tablename__ = "api_token"
    __table_args__ = (
        Index("idx_api_token_user_id", "user_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    user_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="所属用户")
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="密钥名称")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="令牌 SHA-256 哈希")
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, comment="展示用前缀（opsp_xxxx）")
    expires_at: Mapped[datetime | None] = mapped_column(DT3, comment="过期时间（空=永不过期）")
    last_used_at: Mapped[datetime | None] = mapped_column(DT3, comment="最后使用时间（60 秒节流更新）")
    created_at: Mapped[datetime] = created_at_column()


class RolePermission(Base):
    """角色-权限点关联（多对多）。"""

    __tablename__ = "role_permission"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uk_role_permission"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    role_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    permission_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
