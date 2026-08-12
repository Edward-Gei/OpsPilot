"""RBAC 接口测试：权限裁决 / 用户角色管理 / 内置角色保护 / 系统配置。"""
from tests.conftest import TEST_PASSWORD, auth_header, login_for_tokens


async def test_me_returns_role_perms(client):
    """ops 用户权限集合：有 cmdb 读写，无用户管理。"""
    tokens = await login_for_tokens(client, "ops1")
    resp = await client.get("/api/v1/auth/me", headers=auth_header(tokens))
    data = resp.json()["data"]
    assert data["username"] == "ops1"
    assert "cmdb:read" in data["permissions"]
    assert "user:write" not in data["permissions"]
    assert [r["code"] for r in data["roles"]] == ["ops"]


async def test_perm_denied(client):
    """无 user:read 的用户访问用户列表 -> 403 + 40301。"""
    tokens = await login_for_tokens(client, "ops1")
    resp = await client.get("/api/v1/users", headers=auth_header(tokens))
    assert resp.status_code == 403
    assert resp.json()["code"] == 40301


async def test_user_crud(client, seed):
    """管理员创建/查询/更新用户；禁用后无法登录。"""
    tokens = await login_for_tokens(client, "admin")
    headers = auth_header(tokens)
    # 创建
    resp = await client.post("/api/v1/users", json={
        "username": "charlie", "password": "Init123456", "display_name": "查理",
        "role_ids": [seed["roles"]["ops"]],
    }, headers=headers)
    body = resp.json()
    assert body["code"] == 0
    uid = body["data"]["id"]
    # 重名冲突
    resp = await client.post("/api/v1/users", json={
        "username": "charlie", "password": "Init123456", "display_name": "查理2",
    }, headers=headers)
    assert resp.json()["code"] == 40901
    # 列表检索
    resp = await client.get("/api/v1/users", params={"keyword": "charlie"}, headers=headers)
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["roles"][0]["code"] == "ops"
    # 禁用后登录被拒
    resp = await client.put(f"/api/v1/users/{uid}", json={"status": "disabled"}, headers=headers)
    assert resp.json()["code"] == 0
    resp = await client.post("/api/v1/auth/login",
                             json={"username": "charlie", "password": "Init123456"})
    assert resp.json()["code"] == 40101


async def test_disable_self_rejected(client, seed):
    """管理员不能禁用自己（防自锁）。"""
    tokens = await login_for_tokens(client, "admin")
    resp = await client.put(
        f"/api/v1/users/{seed['users']['admin']}",
        json={"status": "disabled"}, headers=auth_header(tokens),
    )
    assert resp.json()["code"] == 42201


async def test_admin_reset_password(client, seed):
    """管理员重置密码后：目标用户旧密码失效，新密码登录进入 40105 强制改密。"""
    tokens = await login_for_tokens(client, "admin")
    resp = await client.put(
        f"/api/v1/users/{seed['users']['ops1']}/password",
        json={"new_password": "Reset12345"}, headers=auth_header(tokens),
    )
    assert resp.json()["code"] == 0
    resp = await client.post("/api/v1/auth/login",
                             json={"username": "ops1", "password": TEST_PASSWORD})
    assert resp.json()["code"] == 40101
    resp = await client.post("/api/v1/auth/login",
                             json={"username": "ops1", "password": "Reset12345"})
    assert resp.json()["code"] == 40105


async def test_role_crud_and_perm_effect(client, seed):
    """自定义角色增改删；改角色权限后成员权限即时生效（缓存失效）。"""
    tokens = await login_for_tokens(client, "admin")
    headers = auth_header(tokens)
    # 创建自定义角色
    resp = await client.post("/api/v1/roles", json={
        "code": "viewer", "name": "只读", "permissions": ["cmdb:read"],
    }, headers=headers)
    rid = resp.json()["data"]["id"]
    # 把 ops1 切到 viewer 角色
    resp = await client.put(f"/api/v1/users/{seed['users']['ops1']}",
                            json={"role_ids": [rid]}, headers=headers)
    assert resp.json()["code"] == 0
    ops_tokens = await login_for_tokens(client, "ops1")
    resp = await client.get("/api/v1/auth/me", headers=auth_header(ops_tokens))
    assert resp.json()["data"]["permissions"] == ["cmdb:read"]
    # 更新角色权限 -> 成员权限缓存失效并生效
    resp = await client.put(f"/api/v1/roles/{rid}", json={
        "permissions": ["cmdb:read", "ticket:read"],
    }, headers=headers)
    assert resp.json()["code"] == 0
    resp = await client.get("/api/v1/auth/me", headers=auth_header(ops_tokens))
    assert "ticket:read" in resp.json()["data"]["permissions"]
    # 有成员的角色不可删
    resp = await client.delete(f"/api/v1/roles/{rid}", headers=headers)
    assert resp.json()["code"] == 42201
    # 移除成员后可删
    await client.put(f"/api/v1/users/{seed['users']['ops1']}",
                     json={"role_ids": [seed["roles"]["ops"]]}, headers=headers)
    resp = await client.delete(f"/api/v1/roles/{rid}", headers=headers)
    assert resp.json()["code"] == 0


async def test_builtin_role_protected(client, seed):
    """admin 不可编辑，其他内置角色可编辑但均不可删除。"""
    tokens = await login_for_tokens(client, "admin")
    headers = auth_header(tokens)
    admin_role_id = seed["roles"]["admin"]
    resp = await client.put(f"/api/v1/roles/{admin_role_id}",
                            json={"permissions": ["cmdb:read"]}, headers=headers)
    assert resp.json()["code"] == 42201
    resp = await client.put(f"/api/v1/roles/{seed['roles']['auditor']}",
                            json={"name": "审计员调整", "permissions": ["audit:read"]}, headers=headers)
    assert resp.json()["code"] == 0
    resp = await client.delete(f"/api/v1/roles/{seed['roles']['auditor']}", headers=headers)
    assert resp.json()["code"] == 42201


async def test_system_configs(client):
    """系统配置读写：admin 可读写，ops 无 system:config 被拒。"""
    tokens = await login_for_tokens(client, "admin")
    headers = auth_header(tokens)
    resp = await client.get("/api/v1/system/configs", headers=headers)
    data = resp.json()["data"]
    assert data["mfa.policy"] == "off"
    resp = await client.put("/api/v1/system/configs",
                            json={"configs": {"mfa.policy": "optional"}}, headers=headers)
    assert resp.json()["code"] == 0
    resp = await client.get("/api/v1/system/configs", headers=headers)
    assert resp.json()["data"]["mfa.policy"] == "optional"
    # 未知键拒绝
    resp = await client.put("/api/v1/system/configs",
                            json={"configs": {"evil.key": 1}}, headers=headers)
    assert resp.json()["code"] == 40001
    # 无权限用户
    ops_tokens = await login_for_tokens(client, "ops1")
    resp = await client.get("/api/v1/system/configs", headers=auth_header(ops_tokens))
    assert resp.json()["code"] == 40301


async def test_admin_manage_user_mfa(client, seed):
    """管理员 MFA 管控：关闭后免验证登录 -> 重新开启后 40103 -> 重置后需重新绑定。"""
    import pyotp
    from tests.conftest import TEST_PASSWORD

    # ops1 先自助绑定 MFA
    ops_tokens = await login_for_tokens(client, "ops1")
    ops_headers = auth_header(ops_tokens)
    resp = await client.post("/api/v1/auth/mfa/setup", json={}, headers=ops_headers)
    secret = resp.json()["data"]["secret"]
    resp = await client.post("/api/v1/auth/mfa/bind",
                             json={"code": pyotp.TOTP(secret).now()}, headers=ops_headers)
    assert resp.json()["code"] == 0

    admin_headers = auth_header(await login_for_tokens(client, "admin"))
    ops_id = seed["users"]["ops1"]
    mfa_url = f"/api/v1/users/{ops_id}/mfa"

    # ops 角色无 user:mfa 权限
    resp = await client.put(mfa_url, json={"action": "disable"}, headers=ops_headers)
    assert resp.json()["code"] == 40301

    # 关闭：登录不再要求动态码（保留密钥 mfa_bound=True）
    resp = await client.put(mfa_url, json={"action": "disable"}, headers=admin_headers)
    assert resp.json()["code"] == 0
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["access_token"]

    # 重新开启：登录恢复 40103
    resp = await client.put(mfa_url, json={"action": "enable"}, headers=admin_headers)
    assert resp.json()["code"] == 0
    resp = await client.post("/api/v1/auth/login",
                             json={"username": "ops1", "password": TEST_PASSWORD})
    assert resp.json()["code"] == 40103

    # 重置：密钥清除，登录直接成功且 enable 被拒（无密钥）
    resp = await client.put(mfa_url, json={"action": "reset"}, headers=admin_headers)
    assert resp.json()["code"] == 0
    tokens = await login_for_tokens(client, "ops1")
    assert tokens["access_token"]
    resp = await client.put(mfa_url, json={"action": "enable"}, headers=admin_headers)
    assert resp.json()["code"] == 42201


async def test_sensitive_config_mask_and_unmask(client, db_factory):
    """敏感配置：读取脱敏为 ******；原样回写 ****** 不覆盖真实密钥。"""
    tokens = await login_for_tokens(client, "admin")
    headers = auth_header(tokens)
    ldap_cfg = {"server_url": "ldap://x:389", "bind_dn": "cn=svc", "bind_password": "real-secret",
                "base_dn": "dc=x", "user_filter": "(uid={username})"}
    resp = await client.put("/api/v1/system/configs",
                            json={"configs": {"ldap.config": ldap_cfg}}, headers=headers)
    assert resp.json()["code"] == 0
    # 读取：密钥脱敏
    resp = await client.get("/api/v1/system/configs", headers=headers)
    masked = resp.json()["data"]["ldap.config"]
    assert masked["bind_password"] == "******"
    # 前端原样提交脱敏值（仅改 server_url）：库中真实密钥不应被覆盖
    masked["server_url"] = "ldap://y:389"
    resp = await client.put("/api/v1/system/configs",
                            json={"configs": {"ldap.config": masked}}, headers=headers)
    assert resp.json()["code"] == 0
    # 直查库验证：密钥仍为真实值，非敏感字段已更新
    from app.services import config_service
    async with db_factory() as session:
        stored = await config_service.get_config(session, "ldap.config")
    assert stored["bind_password"] == "real-secret"
    assert stored["server_url"] == "ldap://y:389"
