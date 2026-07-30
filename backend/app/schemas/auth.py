"""认证模块请求/响应模型（04-API设计 §2）。"""
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """账号密码登录。"""
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """刷新令牌。"""
    refresh_token: str


class LogoutRequest(BaseModel):
    """登出（吊销 refresh；可缺省仅登出前端会话）。"""
    refresh_token: str | None = None


class MfaTokenRequest(BaseModel):
    """携带 mfa_token 的请求基类（40103/40104 流程）。"""
    mfa_token: str | None = None  # 登录流程必传；个人中心自助操作走 access token 可省略


class MfaVerifyRequest(MfaTokenRequest):
    """MFA 动态码验证（登录第二步）。"""
    code: str = Field(min_length=6, max_length=6)


class MfaBindRequest(MfaTokenRequest):
    """MFA 绑定确认（扫码后回填动态码）。"""
    code: str = Field(min_length=6, max_length=6)


class ChangePasswordRequest(BaseModel):
    """修改密码：40105 流程携带 change_token，个人中心走 access token。"""
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)
    change_token: str | None = None


class SsoExchangeRequest(BaseModel):
    """SSO 一次性 ticket 换令牌（冲突决策⑤）。"""
    ticket: str


class UpdateProfileRequest(BaseModel):
    """个人资料自助修改（仅开放显示名/邮箱）。"""
    display_name: str = Field(min_length=1, max_length=64)
    email: str | None = Field(default=None, max_length=128)


class TokenCreateRequest(BaseModel):
    """创建个人访问密钥；有效期缺省为永不过期。"""
    name: str = Field(min_length=1, max_length=64)
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
