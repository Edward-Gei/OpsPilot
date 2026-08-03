"""认证服务：登录流水线 / MFA 三态 / 令牌管理 / 强制改密 / SSO 换票。

登录流水线（02-技术架构 §3.1）：
    查锁定 -> AuthProvider 链(local→ldap) -> 失败计数(Redis)超限锁定
    -> 成功清计数 -> _issue_or_challenge 统一三态出口：
         40103 需 MFA 验证（已绑定） | 40104 需绑定 MFA（required 未绑定）
         40105 需修改初始密码       | 0     签发 access + refresh

复用点：login / mfa_verify / mfa_bind / exchange_sso_ticket 全部收敛到
_issue_or_challenge，避免三态判定逻辑重复。
"""
import logging
import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from redis.exceptions import WatchError

from app import audit
from app.auth_providers import LdapAuthProvider, LocalAuthProvider
from app.core import redis as redis_mod
from app.core import security
from app.core.config import settings
from app.core.response import BizError, Errors
from app.models.auth import User
from app.models.notify import NotifyChannel
from app.services import config_service
from app.services import notify_service

logger = logging.getLogger("opspilot.auth")

# 认证源链：按顺序尝试（OIDC 走回调流程不在链内）
_PROVIDERS = [LocalAuthProvider(), LdapAuthProvider()]

_MFA_PENDING_TTL = 600  # 绑定中 secret 的 Redis 保留时间（秒）
_SSO_TICKET_TTL = 60    # SSO 一次性换票有效期（秒）
RESET_MESSAGE = "如果该邮箱已绑定本地账号，系统将发送密码重置邮件，请查收邮箱。"


# ---------- 登录主流程 ----------

async def login(session: AsyncSession, username: str, password: str, ip: str | None) -> dict:
    """账号密码登录：锁定检查 -> 认证源链 -> 失败计数 -> 三态出口。"""
    policy = await config_service.get_config(session, "password.policy") or {}
    await _check_locked(session, username)
    user: User | None = None
    for provider in _PROVIDERS:
        user = await provider.authenticate(session, username, password)
        if user is not None:
            break
    if user is None:
        await _on_login_fail(session, username, ip, policy)
        raise BizError(Errors.UNAUTHORIZED, "用户名或密码错误", 401)
    if user.status != "active":
        audit.log(module="auth", action="login", result="failed", actor_name=username,
                  source_ip=ip, detail={"reason": "disabled"})
        raise BizError(Errors.UNAUTHORIZED, "账号已被禁用", 401)
    # 认证通过：清失败计数，进入三态判定
    await redis_mod.redis_client.delete(redis_mod.KEY_LOGIN_FAIL.format(username=username))
    return await _issue_or_challenge(session, user, ip, mfa_passed=False)


async def _check_locked(session: AsyncSession, username: str) -> None:
    """账号锁定检查：DB locked_until 未到期抛 42901。"""
    user = (
        await session.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if user and user.locked_until and user.locked_until > datetime.now():
        remain = int((user.locked_until - datetime.now()).total_seconds() // 60) + 1
        raise BizError(Errors.LOCKED, f"账号已锁定，请 {remain} 分钟后重试", 429)


async def _on_login_fail(session: AsyncSession, username: str, ip: str | None, policy: dict) -> None:
    """失败计数（Redis）；超限写 DB 锁定字段并审计（02-技术架构 §3.1）。"""
    max_fail = int(policy.get("max_fail", 5))
    lock_minutes = int(policy.get("lock_minutes", 15))
    key = redis_mod.KEY_LOGIN_FAIL.format(username=username)
    count = await redis_mod.redis_client.incr(key)
    # 计数窗口与锁定时长一致：窗口内累计超限才锁
    await redis_mod.redis_client.expire(key, lock_minutes * 60)
    audit.log(module="auth", action="login", result="failed", actor_name=username,
              source_ip=ip, detail={"fail_count": count})
    if count >= max_fail:
        user = (
            await session.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user:
            user.locked_until = datetime.now() + timedelta(minutes=lock_minutes)
            # 必须先提交：随后抛 42901 会触发会话回滚，锁定写入不能丢
            await session.commit()
        await redis_mod.redis_client.delete(key)
        audit.log(module="auth", action="account.lock", result="success", actor_name=username,
                  source_ip=ip, detail={"lock_minutes": lock_minutes, "fail_count": count})
        raise BizError(Errors.LOCKED, f"失败次数过多，账号锁定 {lock_minutes} 分钟", 429)


async def _issue_or_challenge(
    session: AsyncSession, user: User, ip: str | None, *, mfa_passed: bool
) -> dict:
    """三态统一出口：MFA 挑战 -> 强制改密 -> 签发正式令牌。

    mfa_passed=True 表示本次会话已通过 TOTP 校验（mfa/verify、bind 后调用），
    跳过 MFA 判定直接进入改密检查。
    """
    if not mfa_passed:
        if user.mfa_enabled:
            # 已绑定即生效：无论策略如何都要求验证动态码（用户自愿绑定后不应被跳过）
            token, _, _ = security.create_token(user.id, user.username, security.SCOPE_MFA)
            raise BizError(Errors.MFA_REQUIRED, "需要 MFA 动态码验证", 401,
                           data={"mfa_token": token})
        mfa_policy = await config_service.get_config(session, "mfa.policy") or "off"
        if mfa_policy == "required":
            # required 且未绑定：先引导绑定
            token, _, _ = security.create_token(user.id, user.username, security.SCOPE_MFA)
            raise BizError(Errors.MFA_SETUP, "请先绑定 MFA 认证器", 401,
                           data={"mfa_token": token})
    if user.must_change_password:
        token, _, _ = security.create_token(user.id, user.username, security.SCOPE_PWD_CHANGE)
        raise BizError(Errors.PASSWORD_CHANGE, "首次登录须修改初始密码", 401,
                       data={"change_token": token})
    return await _issue_tokens(session, user, ip)


async def _create_token_pair(session: AsyncSession, user: User) -> dict:
    """按 system_config `token.policy` 签发 access/refresh 令牌对。

    系统设置页可运行时调整有效期；未配置时回退环境变量默认值
    （ACCESS_TOKEN_MINUTES / REFRESH_TOKEN_DAYS）。
    """
    policy = await config_service.get_config(session, "token.policy") or {}
    access_ttl = timedelta(minutes=int(policy.get("access_minutes") or settings.access_token_minutes))
    refresh_ttl = timedelta(days=int(policy.get("refresh_days") or settings.refresh_token_days))
    access, _, expires_in = security.create_token(
        user.id, user.username, security.SCOPE_ACCESS, ttl=access_ttl)
    refresh, refresh_jti, _ = security.create_token(
        user.id, user.username, security.SCOPE_REFRESH, ttl=refresh_ttl)
    refresh_key = redis_mod.KEY_USER_REFRESH.format(user_id=user.id)
    await redis_mod.redis_client.sadd(refresh_key, refresh_jti)
    await redis_mod.redis_client.expire(refresh_key, int(refresh_ttl.total_seconds()))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


async def _issue_tokens(session: AsyncSession, user: User, ip: str | None) -> dict:
    """签发正式令牌对，刷新最近登录时间并写登录审计。"""
    tokens = await _create_token_pair(session, user)
    user.last_login_at = datetime.now()
    await session.flush()
    audit.log(module="auth", action="login", result="success",
              actor_id=user.id, actor_name=user.username, source_ip=ip)
    return tokens


# ---------- 令牌刷新 / 登出 ----------

async def refresh_tokens(session: AsyncSession, refresh_token: str) -> dict:
    """刷新令牌（旋转策略）：校验+吊销旧 refresh，签发新令牌对。"""
    payload = security.decode_token(refresh_token, security.SCOPE_REFRESH)
    await _ensure_not_revoked(payload["jti"])
    user = await session.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise BizError(Errors.UNAUTHORIZED, "账号不可用", 401)
    await _revoke_jti(payload)
    return await _create_token_pair(session, user)


async def logout(user: User, refresh_token: str | None, ip: str | None) -> None:
    """登出：吊销 refresh（access 短时效自然过期），写审计。"""
    if refresh_token:
        try:
            payload = security.decode_token(refresh_token, security.SCOPE_REFRESH)
            await _revoke_jti(payload)
        except BizError:
            pass  # refresh 已失效无需吊销，登出仍视为成功
    audit.log(module="auth", action="logout", result="success",
              actor_id=user.id, actor_name=user.username, source_ip=ip)


async def _ensure_not_revoked(jti: str) -> None:
    """吊销表检查：命中即拒绝（登出/改密后旧 refresh 不可再用）。"""
    if await redis_mod.redis_client.exists(redis_mod.KEY_TOKEN_REVOKED.format(jti=jti)):
        raise BizError(Errors.UNAUTHORIZED, "令牌已失效", 401)


async def _revoke_jti(payload: dict) -> None:
    """按 jti 吊销：TTL 取令牌剩余有效期，过期自动清理。"""
    remain = int(payload["exp"] - datetime.now().timestamp())
    if remain > 0:
        await redis_mod.redis_client.set(
            redis_mod.KEY_TOKEN_REVOKED.format(jti=payload["jti"]), "1", ex=remain
        )
    await redis_mod.redis_client.srem(
        redis_mod.KEY_USER_REFRESH.format(user_id=payload["sub"]), payload["jti"]
    )


async def _revoke_all_user_refresh(user: User) -> None:
    key = redis_mod.KEY_USER_REFRESH.format(user_id=user.id)
    jtis = await redis_mod.redis_client.smembers(key)
    for jti in jtis:
        await redis_mod.redis_client.set(
            redis_mod.KEY_TOKEN_REVOKED.format(jti=jti), "1", ex=settings.refresh_token_days * 86400
        )
    await redis_mod.redis_client.delete(key)


def _normalize_email(value: str) -> str:
    return value.strip().lower()


async def password_policy(session: AsyncSession) -> dict:
    policy = await config_service.get_config(session, "password.policy") or {}
    return {
        "min_length": int(policy.get("min_length", 8)),
        "require_complex": bool(policy.get("require_complex", True)),
    }


async def forgot_password(session: AsyncSession, email: str, ip: str | None) -> None:
    """申请重置：所有外部路径统一返回，内部按条件生成邮件。"""
    normalized = _normalize_email(email)
    cooldown = redis_mod.KEY_PWD_RESET_EMAIL_COOLDOWN.format(email=normalized)
    daily = redis_mod.KEY_PWD_RESET_EMAIL_DAILY.format(date=datetime.now().strftime("%Y%m%d"), email=normalized)
    hourly = redis_mod.KEY_PWD_RESET_IP_HOURLY.format(hour=datetime.now().strftime("%Y%m%d%H"), ip=ip or "unknown")
    if await redis_mod.redis_client.exists(cooldown) or int(await redis_mod.redis_client.get(daily) or 0) >= 5 or int(await redis_mod.redis_client.get(hourly) or 0) >= 20:
        audit.log(module="auth", action="pwd_reset_failed", result="failed", source_ip=ip,
                  detail={"reason": "rate_limited"})
        return
    await redis_mod.redis_client.set(cooldown, "1", ex=60)
    for key, ttl in ((daily, 86400), (hourly, 3600)):
        count = await redis_mod.redis_client.incr(key)
        if count == 1:
            await redis_mod.redis_client.expire(key, ttl)
    user = (await session.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
    if user is None or user.source != "local" or not user.email:
        audit.log(module="auth", action="pwd_reset_failed", result="failed", source_ip=ip,
                  detail={"reason": "not_eligible"})
        return
    channel = (await session.execute(select(NotifyChannel).where(NotifyChannel.type == "email"))).scalar_one_or_none()
    cfg = channel.config if channel else None
    if (
        not channel
        or not channel.enabled
        or not cfg
        or not str(cfg.get("host") or "").strip()
        or not str(cfg.get("from_addr") or cfg.get("username") or "").strip()
    ):
        audit.log(module="auth", action="pwd_reset_failed", result="failed", actor_id=user.id,
                  actor_name=user.username, source_ip=ip, detail={"reason": "smtp_unconfigured"})
        return
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    ttl = settings.reset_token_ttl_minutes * 60
    await redis_mod.redis_client.set(redis_mod.KEY_PWD_RESET.format(user_id=user.id), token_hash, ex=ttl)
    await redis_mod.redis_client.set(redis_mod.KEY_PWD_RESET_LOOKUP.format(token_hash=token_hash), str(user.id), ex=ttl + 86400)
    reset_url = f"{settings.public_base_url.rstrip('/')}/reset-password?token={raw_token}"
    content = (
        f"您好，{user.username}：\n\n我们收到了您的密码重置请求。\n\n"
        f"请点击以下链接设置新密码：\n\n{reset_url}\n\n"
        f"该链接将在 {settings.reset_token_ttl_minutes} 分钟后失效，且只能使用一次。\n\n"
        f"请求来源 IP：{ip or 'unknown'}\n\n若非本人操作，请忽略此邮件，不会有任何变更。\n\nOpsPilot"
    )
    await notify_service.emit_security_email(session, receiver=normalized,
                                              source_ip=ip,
                                              title="【OpsPilot】密码重置请求", content=content)
    audit.log(module="auth", action="pwd_reset_requested", result="success", actor_id=user.id,
              actor_name=user.username, source_ip=ip)


async def validate_reset_token(token: str) -> dict:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    used_key = redis_mod.KEY_PWD_RESET_USED.format(token_hash=token_hash)
    if await redis_mod.redis_client.exists(used_key):
        return {"status": "used"}
    user_id = await redis_mod.redis_client.get(redis_mod.KEY_PWD_RESET_LOOKUP.format(token_hash=token_hash))
    if not user_id:
        return {"status": "invalid"}
    key = redis_mod.KEY_PWD_RESET.format(user_id=user_id)
    stored = await redis_mod.redis_client.get(key)
    if not stored:
        return {"status": "expired"}
    if stored != token_hash:
        return {"status": "used"}
    return {"status": "valid", "expires_in_seconds": await redis_mod.redis_client.ttl(key)}


async def reset_password(session: AsyncSession, token: str, new_password: str, ip: str | None) -> None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    lookup_key = redis_mod.KEY_PWD_RESET_LOOKUP.format(token_hash=token_hash)
    user_id = await redis_mod.redis_client.get(lookup_key)
    if not user_id:
        audit.log(module="auth", action="pwd_reset_failed", result="failed", source_ip=ip,
                  detail={"reason": "invalid_token"})
        raise Errors.param("重置链接无效或已过期")
    user = await session.get(User, int(user_id))
    if user is None or user.source != "local" or not user.password_hash:
        audit.log(module="auth", action="pwd_reset_failed", result="failed", source_ip=ip,
                  detail={"reason": "invalid_user"})
        raise Errors.param("重置链接无效或已过期")
    policy = await config_service.get_config(session, "password.policy") or {}
    try:
        security.validate_password_strength(new_password, policy)
    except BizError:
        audit.log(module="auth", action="pwd_reset_failed", result="failed", actor_id=user.id,
                  actor_name=user.username, source_ip=ip, detail={"reason": "password_policy"})
        raise
    if security.verify_password(new_password, user.password_hash):
        audit.log(module="auth", action="pwd_reset_failed", result="failed", actor_id=user.id,
                  actor_name=user.username, source_ip=ip, detail={"reason": "same_as_current"})
        raise Errors.param("新密码不能与当前密码相同")
    # Prepare the password update before consuming the token so transient hashing or
    # persistence failures do not strand the user with an unusable reset link.
    user.password_hash = security.hash_password(new_password)
    user.must_change_password = False
    user.locked_until = None
    await redis_mod.redis_client.delete(redis_mod.KEY_LOGIN_FAIL.format(username=user.username))
    await _revoke_all_user_refresh(user)
    await session.flush()

    user_key = redis_mod.KEY_PWD_RESET.format(user_id=user_id)
    pipe = redis_mod.redis_client.pipeline(transaction=True)
    consumed = 0
    try:
        await pipe.watch(user_key)
        current = await pipe.get(user_key)
        if current == token_hash:
            pipe.multi()
            pipe.delete(user_key, lookup_key)
            await pipe.execute()
            consumed = 1
        elif current:
            consumed = -1
    except WatchError:
        consumed = -1
    finally:
        await pipe.reset()
    if consumed != 1:
        audit.log(module="auth", action="pwd_reset_failed", result="failed", actor_id=user.id,
                  actor_name=user.username, source_ip=ip, detail={"reason": "used_token"})
        raise Errors.param("重置链接已使用")
    await redis_mod.redis_client.set(
        redis_mod.KEY_PWD_RESET_USED.format(token_hash=token_hash), "1", ex=86400
    )
    audit.log(module="auth", action="pwd_reset_success", result="success", actor_id=user.id,
              actor_name=user.username, source_ip=ip)


# ---------- MFA ----------

async def mfa_setup(user: User) -> dict:
    """生成待绑定 TOTP secret（暂存 Redis），返回 otpauth URI 供前端出码。"""
    secret = security.generate_totp_secret()
    await redis_mod.redis_client.set(
        redis_mod.KEY_MFA_PENDING.format(user_id=user.id), secret, ex=_MFA_PENDING_TTL
    )
    return {
        "secret": secret,
        "otpauth_uri": security.build_otpauth_uri(secret, user.username),
    }


async def mfa_bind(
    session: AsyncSession, user: User, code: str, ip: str | None, *, issue_tokens: bool
) -> dict | None:
    """确认绑定：校验动态码后加密落库；登录流程内绑定成功直接续走三态出口。

    issue_tokens=True（登录 40104 流程）返回令牌；False（个人中心自助绑定）返回 None。
    """
    key = redis_mod.KEY_MFA_PENDING.format(user_id=user.id)
    secret = await redis_mod.redis_client.get(key)
    if not secret:
        raise Errors.param("绑定会话已过期，请重新获取二维码")
    if not security.verify_totp(secret, code):
        raise Errors.param("动态码错误")
    user.mfa_secret_enc = security.encrypt_text(secret)  # AES-256-GCM 字段级加密
    user.mfa_enabled = True
    await session.flush()
    await redis_mod.redis_client.delete(key)
    audit.log(module="auth", action="mfa.bind", result="success",
              actor_id=user.id, actor_name=user.username, source_ip=ip)
    if issue_tokens:
        return await _issue_or_challenge(session, user, ip, mfa_passed=True)
    return None


async def mfa_verify(session: AsyncSession, user: User, code: str, ip: str | None) -> dict:
    """登录第二步：校验 TOTP 动态码，通过后续走三态出口（改密检查）。"""
    if not user.mfa_enabled or not user.mfa_secret_enc:
        raise Errors.rejected("当前账号未绑定 MFA")
    secret = security.decrypt_text(user.mfa_secret_enc)
    if not security.verify_totp(secret, code):
        audit.log(module="auth", action="mfa.verify", result="failed",
                  actor_id=user.id, actor_name=user.username, source_ip=ip)
        raise Errors.param("动态码错误")
    return await _issue_or_challenge(session, user, ip, mfa_passed=True)


# ---------- 密码修改 ----------

async def change_password(
    session: AsyncSession, user: User, old_password: str, new_password: str, ip: str | None,
    *, from_change_token: bool
) -> dict | None:
    """修改密码：旧密码校验 + 强度校验；强制改密流程改完直接发令牌。

    from_change_token=True（40105 流程）返回令牌对；False（个人中心）返回 None。
    """
    if user.source != "local":
        raise Errors.rejected("SSO 用户请在源系统修改密码")
    if not security.verify_password(old_password, user.password_hash):
        raise Errors.param("原密码错误")
    policy = await config_service.get_config(session, "password.policy") or {}
    security.validate_password_strength(new_password, policy)
    if old_password == new_password:
        raise Errors.param("新密码不能与原密码相同")
    user.password_hash = security.hash_password(new_password)
    user.must_change_password = False
    await session.flush()
    audit.log(module="auth", action="password.change", result="success",
              actor_id=user.id, actor_name=user.username, source_ip=ip)
    if from_change_token:
        # 强制改密完成后无需重新输密码，直接进入系统
        return await _issue_tokens(session, user, ip)
    return None


# ---------- SSO 一次性换票（冲突决策⑤） ----------

async def create_sso_ticket(user: User) -> str:
    """OIDC 回调认证成功后生成一次性 ticket，前端用其换取正式令牌。"""
    ticket = secrets.token_urlsafe(32)
    await redis_mod.redis_client.set(
        redis_mod.KEY_SSO_TICKET.format(ticket=ticket), str(user.id), ex=_SSO_TICKET_TTL
    )
    return ticket


async def exchange_sso_ticket(session: AsyncSession, ticket: str, ip: str | None) -> dict:
    """ticket 换令牌：一次性消费后复用三态出口（SSO 用户同样受 MFA 策略约束）。"""
    key = redis_mod.KEY_SSO_TICKET.format(ticket=ticket)
    user_id = await redis_mod.redis_client.get(key)
    if not user_id:
        raise Errors.param("ticket 无效或已过期")
    await redis_mod.redis_client.delete(key)
    user = await session.get(User, int(user_id))
    if user is None or user.status != "active":
        raise BizError(Errors.UNAUTHORIZED, "账号不可用", 401)
    return await _issue_or_challenge(session, user, ip, mfa_passed=False)


# ---------- 令牌辅助（mfa_token / change_token 解析） ----------

async def load_user_by_token(session: AsyncSession, token: str, scope: str) -> User:
    """按指定 scope 解析令牌并加载用户（mfa/verify、改密接口用）。"""
    payload = security.decode_token(token, scope)
    user = await session.get(User, int(payload["sub"]))
    if user is None or user.status != "active":
        raise BizError(Errors.UNAUTHORIZED, "账号不可用", 401)
    return user
