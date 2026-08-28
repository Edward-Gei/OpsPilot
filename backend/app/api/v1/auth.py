"""认证路由（04-API设计 §2）：登录三态 / MFA / 令牌 / 改密 / SSO。"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.auth_providers import oidc
from app import audit
from app.core.deps import CurrentUser, DbSession, get_client_ip, get_current_user
from app.core.response import BizError, Errors, ok
from app.core.security import SCOPE_MFA, SCOPE_PWD_CHANGE
from app.models.notify import NotifyChannel
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    MfaBindRequest,
    MfaTokenRequest,
    MfaVerifyRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SsoExchangeRequest,
    UpdateProfileRequest,
)
from app.services import auth_service, config_service, rbac_service

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", summary="账号密码登录")
async def login(req: LoginRequest, request: Request, session: DbSession) -> dict:
    """登录入口：成功返回令牌对；40103/40104/40105 三态经全局异常处理器返回。"""
    tokens = await auth_service.login(session, req.username, req.password, get_client_ip(request))
    return ok(tokens)


@router.post("/forgot-password", summary="申请密码重置")
async def forgot_password(req: ForgotPasswordRequest, request: Request, session: DbSession) -> dict:
    """公开申请接口：所有分支统一返回，防止账号枚举。"""
    try:
        await auth_service.forgot_password(session, req.email, get_client_ip(request))
    except Exception:  # noqa: BLE001 申请接口不得因内部细节暴露分支
        audit.log(module="auth", action="pwd_reset_failed", result="failed",
                  source_ip=get_client_ip(request), detail={"reason": "internal_error"})
    return ok({"message": auth_service.RESET_MESSAGE})


@router.get("/reset-password/validate", summary="预校验密码重置令牌")
async def validate_reset_password(token: str) -> dict:
    return ok(await auth_service.validate_reset_token(token))


@router.post("/reset-password", summary="提交密码重置")
async def reset_password(req: ResetPasswordRequest, request: Request, session: DbSession) -> dict:
    await auth_service.reset_password(session, req.token, req.new_password, get_client_ip(request))
    return ok(message="密码重置成功，请使用新密码登录。")


@router.get("/password-policy", summary="公开密码策略")
async def password_policy(session: DbSession) -> dict:
    return ok(await auth_service.password_policy(session))


@router.post("/refresh", summary="刷新令牌")
async def refresh(req: RefreshRequest, session: DbSession) -> dict:
    """refresh 旋转：旧 refresh 吊销并签发新令牌对。"""
    return ok(await auth_service.refresh_tokens(session, req.refresh_token))


@router.post("/logout", summary="登出")
async def logout(req: LogoutRequest, request: Request, user: CurrentUser) -> dict:
    """吊销 refresh 并审计；access 短时效自然过期。"""
    await auth_service.logout(user, req.refresh_token, get_client_ip(request))
    return ok()


@router.get("/me", summary="当前用户信息")
async def me(user: CurrentUser, session: DbSession) -> dict:
    """返回用户资料 + 角色 + 权限码集合（前端菜单/按钮显隐依据）。"""
    roles = await rbac_service.get_user_roles(session, user.id)
    perms = await rbac_service.get_user_perms(session, user.id)
    return ok({
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "source": user.source,
        "mfa_enabled": user.mfa_enabled,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "roles": [{"id": r.id, "code": r.code, "name": r.name} for r in roles],
        "permissions": sorted(perms),
    })


@router.put("/me", summary="修改个人资料")
async def update_me(
    req: UpdateProfileRequest, request: Request, user: CurrentUser, session: DbSession
) -> dict:
    """个人中心自助修改显示名/邮箱（其余字段不开放，只能改自己）。"""
    user.display_name = req.display_name
    user.email = req.email or None
    await session.flush()
    audit.log(module="user", action="user.profile_update", actor_id=user.id,
              actor_name=user.username, source_ip=get_client_ip(request),
              target_type="user", target_id=str(user.id), target_name=user.username,
              detail={"display_name": req.display_name, "email": req.email})
    return ok()


# ---------- MFA（40103/40104 流程 + 个人中心自助绑定，冲突决策④） ----------

async def _resolve_mfa_user(request: Request, session: DbSession, mfa_token: str | None):
    """MFA 接口双通道取用户：优先 mfa_token（登录流程），否则回退 access token。"""
    if mfa_token:
        return await auth_service.load_user_by_token(session, mfa_token, SCOPE_MFA)
    return await get_current_user(request, session)


@router.post("/mfa/setup", summary="获取 MFA 绑定二维码")
async def mfa_setup(
    req: MfaTokenRequest,
    request: Request,
    session: DbSession,
) -> dict:
    """生成 TOTP secret 与 otpauth URI（前端渲染二维码，10 分钟内完成绑定）。"""
    user = await _resolve_mfa_user(request, session, req.mfa_token)
    return ok(await auth_service.mfa_setup(user))


@router.post("/mfa/bind", summary="确认绑定 MFA")
async def mfa_bind(req: MfaBindRequest, request: Request, session: DbSession) -> dict:
    """校验动态码完成绑定；登录流程内绑定成功直接续走三态出口发令牌。"""
    ip = get_client_ip(request)
    user = await _resolve_mfa_user(request, session, req.mfa_token)
    tokens = await auth_service.mfa_bind(session, user, req.code, ip, issue_tokens=bool(req.mfa_token))
    return ok(tokens)


@router.post("/mfa/verify", summary="MFA 动态码验证（登录第二步）")
async def mfa_verify(req: MfaVerifyRequest, request: Request, session: DbSession) -> dict:
    """40103 后携 mfa_token + 动态码验证，通过后返回令牌（或 40105 继续改密）。"""
    if not req.mfa_token:
        raise BizError(Errors.UNAUTHORIZED, "缺少 mfa_token", 401)
    user = await auth_service.load_user_by_token(session, req.mfa_token, SCOPE_MFA)
    return ok(await auth_service.mfa_verify(session, user, req.code, get_client_ip(request)))


# ---------- 密码修改（40105 流程 + 个人中心，冲突决策③） ----------

@router.put("/password", summary="修改密码")
async def change_password(req: ChangePasswordRequest, request: Request, session: DbSession) -> dict:
    """双通道：change_token（强制改密，成功直接发令牌）或 access token（个人中心）。"""
    ip = get_client_ip(request)
    if req.change_token:
        user = await auth_service.load_user_by_token(session, req.change_token, SCOPE_PWD_CHANGE)
        tokens = await auth_service.change_password(
            session, user, req.old_password, req.new_password, ip, from_change_token=True
        )
        return ok(tokens)
    user = await get_current_user(request, session)
    await auth_service.change_password(
        session, user, req.old_password, req.new_password, ip, from_change_token=False
    )
    return ok()


# ---------- SSO（OIDC 授权码 + 一次性换票，冲突决策⑤） ----------

@router.get("/sso/oidc/login", summary="OIDC 登录跳转")
async def oidc_login(session: DbSession) -> RedirectResponse:
    """302 跳转到 IdP 授权页（浏览器直接访问，非 XHR）。"""
    return RedirectResponse(await oidc.build_authorize_url(session), status_code=302)


@router.get("/sso/oidc/callback", summary="OIDC 回调")
async def oidc_callback(code: str, state: str, session: DbSession) -> RedirectResponse:
    """IdP 回调：认证/建用户后生成一次性 ticket，重定向回登录页换令牌。"""
    user = await oidc.handle_callback(session, code, state)
    ticket = await auth_service.create_sso_ticket(user)
    return RedirectResponse(f"/login?ticket={ticket}", status_code=302)


@router.post("/sso/exchange", summary="SSO ticket 换令牌")
async def sso_exchange(req: SsoExchangeRequest, request: Request, session: DbSession) -> dict:
    """前端用回调携带的 ticket 换正式令牌（复用三态出口，SSO 也受 MFA 策略约束）。"""
    return ok(await auth_service.exchange_sso_ticket(session, req.ticket, get_client_ip(request)))


@router.get("/sso/options", summary="登录页 SSO 选项")
async def sso_options(session: DbSession) -> dict:
    """返回已启用的 SSO 方式（登录页据此显示/隐藏 SSO 按钮）。"""
    oidc_cfg = await config_service.get_config(session, "oidc.config")
    email_channel = (await session.execute(
        select(NotifyChannel).where(NotifyChannel.type == "email")
    )).scalar_one_or_none()
    email_cfg = email_channel.config if email_channel else None
    smtp_configured = bool(
        email_channel
        and email_channel.enabled
        and email_cfg
        and email_cfg.get("host")
        and (email_cfg.get("from_addr") or email_cfg.get("username"))
    )
    oidc_enabled = bool(oidc_cfg and oidc_cfg.get("authorize_endpoint"))
    return ok({"oidc_enabled": oidc_enabled, "sso_enabled": oidc_enabled,
               "forgot_password_enabled": smtp_configured})
