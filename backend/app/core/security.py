"""安全基础设施：JWT 签发/校验、bcrypt 密码哈希、TOTP、AES-256-GCM 字段加密。

设计要点（02-技术架构 §3）：
- JWT HS256，claims: sub/username/scope/jti/exp；scope 四类：
    access      正式访问令牌（15min）
    refresh     刷新令牌（7d，登出/改密时按 jti 吊销）
    mfa         MFA 挑战令牌（5min，仅限 /auth/mfa/*）
    pwd_change  强制改密令牌（10min，仅限 PUT /auth/password）
- MFA secret 用 AES-256-GCM 字段级加密；SECRET_ENCRYPT_KEY 留空时从
  JWT_SECRET_KEY SHA-256 派生并打警告日志（冲突决策①）。
"""
import base64
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError
from passlib.context import CryptContext

from app.core.config import settings
from app.core.response import BizError, Errors

logger = logging.getLogger("opspilot.security")

# ---- 令牌 scope 常量 ----
SCOPE_ACCESS = "access"
SCOPE_REFRESH = "refresh"
SCOPE_MFA = "mfa"
SCOPE_PWD_CHANGE = "pwd_change"

# bcrypt cost 12（02-技术架构 §3.3）
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


# ---------- 密码哈希与策略 ----------

def hash_password(plain: str) -> str:
    """bcrypt 哈希明文密码。"""
    return _pwd_ctx.hash(plain)


def verify_password(plain: str, hashed: str | None) -> bool:
    """校验明文密码与哈希是否匹配；SSO 用户 hash 为空直接失败。"""
    if not hashed:
        return False
    return _pwd_ctx.verify(plain, hashed)


def validate_password_strength(password: str, policy: dict) -> None:
    """按 system_config `password.policy` 校验新密码强度，不合规抛 40001。

    policy: {min_length, require_complex, ...}；require_complex 要求
    同时包含字母与数字（PRD AUTH-04 精简复杂度定义）。
    """
    min_length = int(policy.get("min_length", 8))
    if len(password) < min_length:
        raise Errors.param(f"密码长度不得少于 {min_length} 位")
    if policy.get("require_complex", True):
        if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
            raise Errors.param("密码必须同时包含字母和数字")


# ---------- JWT 签发与校验 ----------

def _scope_ttl(scope: str) -> timedelta:
    """各 scope 的有效期（集中配置于 Settings）。"""
    mapping = {
        SCOPE_ACCESS: timedelta(minutes=settings.access_token_minutes),
        SCOPE_REFRESH: timedelta(days=settings.refresh_token_days),
        SCOPE_MFA: timedelta(minutes=settings.mfa_token_minutes),
        SCOPE_PWD_CHANGE: timedelta(minutes=settings.pwd_change_token_minutes),
    }
    return mapping[scope]


def create_token(
    user_id: int, username: str, scope: str, ttl: timedelta | None = None
) -> tuple[str, str, int]:
    """签发指定 scope 的 JWT，返回 (token, jti, 有效秒数)。

    ttl 可覆盖默认有效期（access/refresh 支持 system_config `token.policy` 运行时调整）；
    jti 用于 refresh 吊销（登出/改密时写 Redis auth:revoked:{jti}）。
    """
    ttl = ttl or _scope_ttl(scope)
    jti = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "scope": scope,
        "jti": jti,
        "iat": now,
        "exp": now + ttl,
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")
    return token, jti, int(ttl.total_seconds())


def decode_token(token: str, expect_scope: str) -> dict[str, Any]:
    """校验 JWT 并断言 scope，返回 payload。

    过期抛 40102（前端触发刷新），其余非法/scope 不符抛 40101。
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except ExpiredSignatureError:
        raise BizError(Errors.TOKEN_EXPIRED, "登录已过期", 401) from None
    except JWTError:
        raise BizError(Errors.UNAUTHORIZED, "无效的令牌", 401) from None
    if payload.get("scope") != expect_scope:
        raise BizError(Errors.UNAUTHORIZED, "令牌类型不匹配", 401)
    return payload


# ---------- AES-256-GCM 字段级加密（MFA secret 等敏感字段） ----------

@lru_cache
def _encrypt_key() -> bytes:
    """解析主加密密钥；未配置时从 JWT_SECRET_KEY 派生（冲突决策①）。"""
    raw = settings.secret_encrypt_key.strip()
    if raw:
        try:
            key = base64.b64decode(raw)
        except Exception:  # noqa: BLE001 base64 非法按配置错误处理
            raise RuntimeError("SECRET_ENCRYPT_KEY 不是合法的 base64 字符串") from None
        if len(key) != 32:
            raise RuntimeError("SECRET_ENCRYPT_KEY 解码后必须为 32 字节")
        return key
    logger.warning(
        "SECRET_ENCRYPT_KEY 未配置，已从 JWT_SECRET_KEY 派生加密密钥；"
        "生产环境请显式配置独立密钥"
    )
    return hashlib.sha256(settings.jwt_secret_key.encode()).digest()


def encrypt_text(plain: str) -> str:
    """AES-256-GCM 加密：密文格式 base64(nonce(12) + ciphertext + tag)。"""
    nonce = os.urandom(12)
    cipher = AESGCM(_encrypt_key()).encrypt(nonce, plain.encode(), None)
    return base64.b64encode(nonce + cipher).decode()


def decrypt_text(enc: str) -> str:
    """AES-256-GCM 解密（encrypt_text 的逆操作）；对损坏密文给出明确错误。

    数据库密文可能被手动修改/迁移损坏，base64 或 AES 解密失败时统一抛
    RuntimeError（附原始异常），调用方按业务口径捕获处理，避免裸异常击穿流程。
    """
    try:
        raw = base64.b64decode(enc)
        if len(raw) < 12:
            raise ValueError("密文长度不足（缺少 12 字节 nonce）")
        return AESGCM(_encrypt_key()).decrypt(raw[:12], raw[12:], None).decode()
    except Exception as exc:  # noqa: BLE001 统一转译为业务可识别的解密失败
        logger.error("凭据解密失败，数据可能已损坏: %s", exc)
        raise RuntimeError("凭据解密失败，数据可能已损坏") from exc


# ---------- TOTP（MFA） ----------

def generate_totp_secret() -> str:
    """生成新的 TOTP secret（base32）。"""
    return pyotp.random_base32()


def build_otpauth_uri(secret: str, username: str) -> str:
    """构造 otpauth:// URI，前端渲染为二维码供认证器 App 扫描。"""
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=settings.app_name)


def verify_totp(secret: str, code: str) -> bool:
    """校验 6 位 TOTP 动态码；valid_window=1 容忍前后各 30s 时钟偏移。"""
    if not code or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)
