"""认证接口测试：登录三态 / 锁定 / MFA 全流程 / 令牌旋转与吊销。"""
import pyotp

from app.models.audit import SystemConfig
from tests.conftest import TEST_PASSWORD, auth_header, login_for_tokens

LOGIN = "/api/v1/auth/login"


async def set_config(db_factory, key: str, value) -> None:
    """测试辅助：直接写 system_config（模拟管理员改配置）。"""
    async with db_factory() as session:
        session.add(SystemConfig(cfg_key=key, cfg_value={"value": value}))
        await session.commit()


async def test_login_success(client):
    """mfa.policy 默认 off：普通用户直接拿到令牌对。"""
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["token_type"] == "bearer"
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] == 15 * 60


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


async def test_token_policy_configurable_ttl(client, db_factory):
    """token.policy 配置生效：access 有效期改 30 分钟后 expires_in 随之变化。"""
    await set_config(db_factory, "token.policy", {"access_minutes": 30, "refresh_days": 3})
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["expires_in"] == 30 * 60
    # 刷新同样按新配置签发
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.json()["data"]["expires_in"] == 30 * 60


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
