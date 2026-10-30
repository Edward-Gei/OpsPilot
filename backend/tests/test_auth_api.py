"""认证接口测试：登录三态 / 锁定 / MFA 全流程 / 令牌旋转与吊销。"""
import hashlib
import time
from types import SimpleNamespace

import fakeredis._basefakesocket as fake_socket
import pyotp
from jose import jwt

from app.core import redis as redis_mod
from app.core import security
from app.core.config import settings

from app.models.audit import SystemConfig
from tests.conftest import TEST_PASSWORD, auth_header, login_for_tokens

LOGIN = "/api/v1/auth/login"


async def set_config(db_factory, key: str, value) -> None:
    """测试辅助：直接写 system_config（模拟管理员改配置）。"""
    async with db_factory() as session:
        row = await session.get(SystemConfig, key)
        if row is None:
            row = SystemConfig(cfg_key=key)
            session.add(row)
        row.cfg_value = {"value": value}
        await session.commit()


async def test_login_success(client):
    """默认策略直接签发令牌对，登录会话最多 8 小时。"""
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == 15 * 60
    refresh = security.decode_token(tokens["refresh_token"], security.SCOPE_REFRESH)
    assert refresh["session_exp"] - refresh["iat"] == 8 * 3600
    assert refresh["exp"] <= refresh["session_exp"]


async def test_login_wrong_password(client):
    """密码错误：HTTP 401 + 业务码 40101。"""
    resp = await client.post(LOGIN, json={"username": "ops1", "password": "bad"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


async def test_login_lockout(client):
    """连续失败 5 次锁定（42901），锁定期内正确密码也被拒。"""
    for i in range(4):
        resp = await client.post(LOGIN, json={"username": "ops1", "password": "bad"})
        assert resp.json()["code"] == 40101, f"第{i + 1}次应为 40101"
    resp = await client.post(LOGIN, json={"username": "ops1", "password": "bad"})
    assert resp.status_code == 429
    assert resp.json()["code"] == 42901
    # 锁定期内即使密码正确也 42901
    resp = await client.post(LOGIN, json={"username": "ops1", "password": TEST_PASSWORD})
    assert resp.json()["code"] == 42901


async def test_first_login_force_change_password(client):
    """40105 流程：初始密码登录 -> change_token 改密 -> 直接发令牌 -> 新密码可登录。"""
    resp = await client.post(LOGIN, json={"username": "newbie", "password": TEST_PASSWORD})
    body = resp.json()
    assert body["code"] == 40105
    change_token = body["data"]["change_token"]
    # 用 change_token 修改密码，成功直接返回令牌对
    resp = await client.put("/api/v1/auth/password", json={
        "old_password": TEST_PASSWORD,
        "new_password": "NewPass456",
        "change_token": change_token,
    })
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["access_token"]
    # 新密码再登录：不再 40105
    tokens = await login_for_tokens(client, "newbie", "NewPass456")
    assert tokens["access_token"]


async def test_weak_new_password_rejected(client):
    """改密强度校验：过短/无数字 -> 40001。"""
    resp = await client.post(LOGIN, json={"username": "newbie", "password": TEST_PASSWORD})
    change_token = resp.json()["data"]["change_token"]
    resp = await client.put("/api/v1/auth/password", json={
        "old_password": TEST_PASSWORD, "new_password": "short", "change_token": change_token,
    })
    assert resp.json()["code"] == 40001


async def test_mfa_required_bind_and_verify_flow(client, db_factory):
    """MFA 全流程：required 未绑定 40104 -> 扫码绑定发令牌；再登录 40103 -> 验证码通过。"""
    await set_config(db_factory, "mfa.policy", "required")
    # 第一步：登录返回 40104（需绑定）
    resp = await client.post(LOGIN, json={"username": "ops1", "password": TEST_PASSWORD})
    body = resp.json()
    assert body["code"] == 40104
    mfa_token = body["data"]["mfa_token"]
    # 第二步：获取绑定二维码
    resp = await client.post("/api/v1/auth/mfa/setup", json={"mfa_token": mfa_token})
    data = resp.json()["data"]
    assert data["otpauth_uri"].startswith("otpauth://")
    secret = data["secret"]
    # 第三步：回填动态码完成绑定，直接发令牌
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/bind", json={"mfa_token": mfa_token, "code": code})
    body = resp.json()
    assert body["code"] == 0 and body["data"]["access_token"]
    # 第四步：再次登录 -> 已绑定 -> 40103 -> 动态码验证通过
    resp = await client.post(LOGIN, json={"username": "ops1", "password": TEST_PASSWORD})
    body = resp.json()
    assert body["code"] == 40103
    mfa_token2 = body["data"]["mfa_token"]
    code2 = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token2, "code": code2})
    body = resp.json()
    assert body["code"] == 0 and body["data"]["access_token"]


async def test_mfa_binding_persists_before_first_login_password_change(client, db_factory):
    """首次绑定后进入改密流程，MFA 绑定仍应保留。"""
    await set_config(db_factory, "mfa.policy", "required")
    resp = await client.post(LOGIN, json={"username": "newbie", "password": TEST_PASSWORD})
    mfa_token = resp.json()["data"]["mfa_token"]

    resp = await client.post("/api/v1/auth/mfa/setup", json={"mfa_token": mfa_token})
    secret = resp.json()["data"]["secret"]
    resp = await client.post("/api/v1/auth/mfa/bind", json={
        "mfa_token": mfa_token, "code": pyotp.TOTP(secret).now(),
    })
    assert resp.json()["code"] == 40105
    change_token = resp.json()["data"]["change_token"]

    resp = await client.put("/api/v1/auth/password", json={
        "old_password": TEST_PASSWORD,
        "new_password": "NewPass456",
        "change_token": change_token,
    })
    tokens = resp.json()["data"]
    resp = await client.get("/api/v1/auth/me", headers=auth_header(tokens))
    assert resp.json()["data"]["mfa_enabled"] is True


async def test_mfa_optional_only_challenges_bound_user(client, db_factory):
    """optional 策略：未绑定用户直接登录成功（不强制绑定）。"""
    await set_config(db_factory, "mfa.policy", "optional")
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["access_token"]


async def test_mfa_bound_user_challenged_even_policy_off(client, db_factory):
    """绑定即生效：policy=off 下已绑定用户登录仍须 40103 验证动态码。"""
    # 先用 access_token 走个人中心自助绑定通道（决策④）
    tokens = await login_for_tokens(client, "ops1")
    headers = auth_header(tokens)
    resp = await client.post("/api/v1/auth/mfa/setup", json={}, headers=headers)
    secret = resp.json()["data"]["secret"]
    code = pyotp.TOTP(secret).now()
    resp = await client.post("/api/v1/auth/mfa/bind", json={"code": code}, headers=headers)
    assert resp.json()["code"] == 0
    # 再次登录：策略仍为 off，但已绑定用户必须验证
    resp = await client.post(LOGIN, json={"username": "ops1", "password": TEST_PASSWORD})
    body = resp.json()
    assert body["code"] == 40103
    code2 = pyotp.TOTP(secret).now()
    resp = await client.post(
        "/api/v1/auth/mfa/verify", json={"mfa_token": body["data"]["mfa_token"], "code": code2}
    )
    assert resp.json()["code"] == 0


async def test_legacy_token_policy_overrides_are_ignored(client, db_factory):
    """旧配置仅保留会话时长，Access 固定 15 分钟。"""
    await set_config(db_factory, "token.policy", {"access_minutes": 30, "refresh_days": 1, "session_hours": 48})
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["expires_in"] == 15 * 60
    refresh = security.decode_token(tokens["refresh_token"], security.SCOPE_REFRESH)
    assert refresh["exp"] == refresh["session_exp"]
    assert refresh["session_exp"] - refresh["iat"] == 48 * 3600
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.json()["data"]["expires_in"] == 15 * 60


async def test_old_token_policy_is_read_as_session_hours_only(client, db_factory):
    await set_config(db_factory, "token.policy", {"access_minutes": 30, "refresh_days": 1})
    headers = auth_header(await login_for_tokens(client, "admin"))
    resp = await client.get("/api/v1/system/configs", headers=headers)
    assert resp.json()["data"]["token.policy"] == {"session_hours": 8}


async def test_session_deadline_is_fixed_and_caps_access_and_refresh(client, db_factory):
    await set_config(db_factory, "token.policy", {
        "access_minutes": 1440, "refresh_days": 7, "session_hours": 2,
    })
    tokens = await login_for_tokens(client, "ops1")
    access = security.decode_token(tokens["access_token"], security.SCOPE_ACCESS)
    refresh = security.decode_token(tokens["refresh_token"], security.SCOPE_REFRESH)
    assert refresh["session_exp"] - refresh["iat"] == 2 * 3600
    assert access["session_exp"] == refresh["session_exp"]
    assert tokens["expires_in"] == 15 * 60
    assert access["exp"] <= refresh["session_exp"]
    assert refresh["exp"] <= refresh["session_exp"]
    assert tokens["expires_in"] <= 2 * 3600

    await set_config(db_factory, "token.policy", {"session_hours": 1})
    rotated = (await client.post("/api/v1/auth/refresh", json={
        "refresh_token": tokens["refresh_token"],
    })).json()["data"]
    rotated_access = security.decode_token(rotated["access_token"], security.SCOPE_ACCESS)
    rotated_refresh = security.decode_token(rotated["refresh_token"], security.SCOPE_REFRESH)
    assert rotated_refresh["session_exp"] == refresh["session_exp"]
    assert rotated_access["exp"] <= refresh["session_exp"]
    assert rotated_refresh["exp"] <= refresh["session_exp"]


async def test_refresh_rejects_elapsed_session_even_when_refresh_jwt_is_valid(client):
    tokens = await login_for_tokens(client, "ops1")
    payload = security.decode_token(tokens["refresh_token"], security.SCOPE_REFRESH)
    payload["session_exp"] = payload["iat"] - 1
    signed = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": signed})
    assert response.status_code == 401
    assert response.json()["code"] == 40102


async def test_legacy_tokens_without_session_deadline_require_new_login(client):
    tokens = await login_for_tokens(client, "ops1")
    access_payload = security.decode_token(tokens["access_token"], security.SCOPE_ACCESS)
    access_payload.pop("session_exp", None)
    old_access = jwt.encode(access_payload, settings.jwt_secret_key, algorithm="HS256")
    access_response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_access}"})
    assert access_response.status_code == 401
    assert access_response.json()["code"] == 40102

    payload = security.decode_token(tokens["refresh_token"], security.SCOPE_REFRESH)
    payload.pop("session_exp", None)
    legacy = jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")
    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": legacy})
    assert response.status_code == 401
    assert response.json()["code"] == 40102


async def test_short_session_does_not_drop_older_refresh_from_password_reset(client, db_factory, seed,
                                                                               fake_redis, monkeypatch):
    older = await login_for_tokens(client, "ops1")
    await set_config(db_factory, "token.policy", {"session_hours": 1})
    await login_for_tokens(client, "ops1")

    # fakeredis 在每条命令前更新数据库时钟；仅推进它，不改变 JWT 的真实签发时刻。
    after_one_hour = time.time() + 3601
    monkeypatch.setattr(fake_socket, "time", SimpleNamespace(time=lambda: after_one_hour))
    raw = "reset-after-short-session"
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    user_id = seed["users"]["ops1"]
    await fake_redis.set(redis_mod.KEY_PWD_RESET.format(user_id=user_id), token_hash, ex=1800)
    await fake_redis.set(redis_mod.KEY_PWD_RESET_LOOKUP.format(token_hash=token_hash), str(user_id), ex=1800)
    reset = await client.post("/api/v1/auth/reset-password", json={
        "token": raw, "new_password": "Renewed789xyz",
    })
    assert reset.json()["code"] == 0

    response = await client.post("/api/v1/auth/refresh", json={"refresh_token": older["refresh_token"]})
    assert response.status_code == 401
    assert response.json()["code"] == 40101


async def test_refresh_rotation(client):
    """refresh 旋转：旧 refresh 一次性使用，重放返回 40101。"""
    tokens = await login_for_tokens(client, "ops1")
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    body = resp.json()
    assert body["code"] == 0 and body["data"]["access_token"]
    # 旧 refresh 已吊销
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.json()["code"] == 40101


async def test_logout_revokes_refresh(client):
    """登出吊销 refresh：登出后无法再刷新。"""
    tokens = await login_for_tokens(client, "ops1")
    resp = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=auth_header(tokens),
    )
    assert resp.json()["code"] == 0
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.json()["code"] == 40101


async def test_me_requires_token(client):
    """无令牌访问 /auth/me -> 40101。"""
    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["code"] == 40101


async def test_profile_change_password(client):
    """个人中心改密（access token 通道）：改后旧密码失效、新密码可登录。"""
    tokens = await login_for_tokens(client, "ops1")
    resp = await client.put("/api/v1/auth/password", json={
        "old_password": TEST_PASSWORD, "new_password": "Fresh789xyz",
    }, headers=auth_header(tokens))
    assert resp.json()["code"] == 0
    resp = await client.post(LOGIN, json={"username": "ops1", "password": TEST_PASSWORD})
    assert resp.json()["code"] == 40101
    await login_for_tokens(client, "ops1", "Fresh789xyz")
