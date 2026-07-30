"""core/security 纯逻辑单测：JWT / 密码策略 / AES-GCM / TOTP。"""
import pyotp
import pytest

from app.core import security
from app.core.config import settings
from app.core.response import BizError, Errors


def test_token_roundtrip():
    """签发与解码往返：claims 完整、scope 匹配。"""
    token, jti, expires_in = security.create_token(1, "admin", security.SCOPE_ACCESS)
    payload = security.decode_token(token, security.SCOPE_ACCESS)
    assert payload["sub"] == "1"
    assert payload["username"] == "admin"
    assert payload["jti"] == jti
    assert expires_in == settings.access_token_minutes * 60


def test_token_scope_mismatch():
    """refresh 令牌不能当 access 用（40101）。"""
    token, _, _ = security.create_token(1, "admin", security.SCOPE_REFRESH)
    with pytest.raises(BizError) as exc:
        security.decode_token(token, security.SCOPE_ACCESS)
    assert exc.value.code == Errors.UNAUTHORIZED


def test_token_expired(monkeypatch):
    """过期令牌抛 40102（前端据此触发刷新）。"""
    monkeypatch.setattr(settings, "access_token_minutes", -1)
    token, _, _ = security.create_token(1, "admin", security.SCOPE_ACCESS)
    with pytest.raises(BizError) as exc:
        security.decode_token(token, security.SCOPE_ACCESS)
    assert exc.value.code == Errors.TOKEN_EXPIRED


def test_password_verify():
    """bcrypt 校验；SSO 用户（hash 为空）恒失败。"""
    hashed = security.hash_password("Abc12345")
    assert security.verify_password("Abc12345", hashed)
    assert not security.verify_password("wrong", hashed)
    assert not security.verify_password("Abc12345", None)


def test_password_strength_policy():
    """长度与复杂度策略校验。"""
    policy = {"min_length": 8, "require_complex": True}
    with pytest.raises(BizError):
        security.validate_password_strength("Ab1", policy)  # 太短
    with pytest.raises(BizError):
        security.validate_password_strength("abcdefgh", policy)  # 无数字
    security.validate_password_strength("Abc12345", policy)  # 合规不抛


def test_encrypt_roundtrip():
    """AES-256-GCM 加解密往返；密文不等于明文。"""
    secret = "JBSWY3DPEHPK3PXP"
    enc = security.encrypt_text(secret)
    assert enc != secret
    assert security.decrypt_text(enc) == secret


def test_totp_verify():
    """TOTP 动态码：当前码通过、错误码/非数字拒绝。"""
    secret = security.generate_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert security.verify_totp(secret, code)
    assert not security.verify_totp(secret, "000000") or code == "000000"
    assert not security.verify_totp(secret, "abcdef")
