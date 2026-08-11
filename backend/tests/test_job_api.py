"""凭据与作业主机凭据绑定接口测试。"""
from app.core.security import decrypt_text
from app.models.job import Credential
from tests.conftest import auth_header, login_for_tokens


CRED_PAYLOAD = {
    "name": "prod-root",
    "login_user": "root",
    "auth_type": "password",
    "secret": "S3cret!pass",
    "description": "生产 root 密码",
}

JOB_HOST_PAYLOAD = {
    "name": "job-agent-01", "ip": "10.8.0.1", "ssh_port": 22,
    "workdir": "/opt/opspilot/workspace",
}


async def _create_credential(client, headers, **override) -> int:
    """测试辅助：创建凭据并返回 id。"""
    resp = await client.post("/api/v1/credentials", json={**CRED_PAYLOAD, **override}, headers=headers)
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["id"]


async def _base_env(client) -> dict:
    """公共前置：admin/ops 登录 + 1 凭据 + 1 作业主机（主机关联凭据）。"""
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_h = auth_header(await login_for_tokens(client, "ops1"))
    cred_id = await _create_credential(client, admin_h)
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "credential_id": cred_id},
                             headers=admin_h)
    job_host_id = resp.json()["data"]["id"]
    return {"admin_h": admin_h, "ops_h": ops_h, "job_host_id": job_host_id,
            "credential_id": cred_id}


class TestCredentialApi:
    """凭据 CRUD / 密文安全 / RBAC。"""

    async def test_crud_and_secret_never_echo(self, client, db_factory):
        """创建→列表：响应无任何密文字段；库内为密文且可逆解回原文。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        cred_id = await _create_credential(client, headers)

        resp = await client.get("/api/v1/credentials", headers=headers)
        body = resp.json()
        assert body["data"]["total"] == 1
        item = body["data"]["items"][0]
        assert item["name"] == "prod-root"
        assert item["auth_type"] == "password"
        assert item["has_passphrase"] is False
        # 密文红线：响应不得出现 secret / secret_enc / passphrase 任何形态
        assert "secret" not in item and "secret_enc" not in item and "passphrase" not in item

        # 库内为 AES-GCM 密文（非明文），且可解密还原
        async with db_factory() as session:
            cred = await session.get(Credential, cred_id)
            assert cred.secret_enc != CRED_PAYLOAD["secret"]
            assert decrypt_text(cred.secret_enc) == CRED_PAYLOAD["secret"]

    async def test_update_empty_secret_keeps_cipher(self, client, db_factory):
        """secret 传空 = 不变更密文；传新值则密文更新。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        cred_id = await _create_credential(client, headers)
        async with db_factory() as session:
            old_enc = (await session.get(Credential, cred_id)).secret_enc

        # 传空 secret：改说明不改密文
        resp = await client.put(f"/api/v1/credentials/{cred_id}", headers=headers,
                                json={**CRED_PAYLOAD, "secret": "", "description": "改说明"})
        assert resp.json()["code"] == 0
        async with db_factory() as session:
            cred = await session.get(Credential, cred_id)
            assert cred.secret_enc == old_enc
            assert cred.description == "改说明"

        # 传新 secret：密文更新且可解出新明文
        resp = await client.put(f"/api/v1/credentials/{cred_id}", headers=headers,
                                json={**CRED_PAYLOAD, "secret": "NewPass#2026"})
        assert resp.json()["code"] == 0
        async with db_factory() as session:
            cred = await session.get(Credential, cred_id)
            assert cred.secret_enc != old_enc
            assert decrypt_text(cred.secret_enc) == "NewPass#2026"

    async def test_change_auth_type_requires_secret(self, client):
        """认证方式变更但未提供新密文 → 40001。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        cred_id = await _create_credential(client, headers)
        resp = await client.put(f"/api/v1/credentials/{cred_id}", headers=headers,
                                json={**CRED_PAYLOAD, "auth_type": "private_key", "secret": ""})
        assert resp.json()["code"] == 40001

    async def test_name_conflict(self, client):
        """凭据名重复 → 40901。"""
        tokens = await login_for_tokens(client, "admin")
        headers = auth_header(tokens)
        await _create_credential(client, headers)
        resp = await client.post("/api/v1/credentials", json=CRED_PAYLOAD, headers=headers)
        assert resp.json()["code"] == 40901

    async def test_ops_cannot_write_but_can_read(self, client):
        """CRED-02：ops 仅可读（模板配置选择用），写操作 40301。"""
        admin_headers = auth_header(await login_for_tokens(client, "admin"))
        await _create_credential(client, admin_headers)

        ops_headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/credentials", headers=ops_headers)
        assert resp.json()["code"] == 0
        resp = await client.post("/api/v1/credentials", json={**CRED_PAYLOAD, "name": "x"},
                                 headers=ops_headers)
        assert resp.json()["code"] == 40301

    async def test_delete_credential(self, client):
        """V2 起凭据独立管理无引用关系：删除直接成功，再删 40401。"""
        headers = auth_header(await login_for_tokens(client, "admin"))
        cred_id = await _create_credential(client, headers, name="idle-cred")

        resp = await client.delete(f"/api/v1/credentials/{cred_id}", headers=headers)
        assert resp.json()["code"] == 0
        resp = await client.delete(f"/api/v1/credentials/{cred_id}", headers=headers)
        assert resp.json()["code"] == 40401


async def test_job_host_credential_binding(client):
    """作业主机凭据关联：必填校验 / 不存在 40401 / 响应带 credential_name / 删除保护。"""
    env = await _base_env(client)
    admin_h = env["admin_h"]
    # credential_id 缺失 → 参数校验失败（pydantic 必填）
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "name": "no-cred", "ip": "10.8.0.9"},
                             headers=admin_h)
    assert resp.json()["code"] != 0
    # 凭据不存在 → 40401
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "name": "bad-cred", "ip": "10.8.0.8",
                                   "credential_id": 99999}, headers=admin_h)
    assert resp.json()["code"] == 40401
    # 列表响应带 credential_name 且无密文字段
    resp = await client.get("/api/v1/job-hosts", headers=admin_h)
    row = next(r for r in resp.json()["data"]["items"] if r["id"] == env["job_host_id"])
    assert row["credential_name"] == CRED_PAYLOAD["name"]
    assert "secret_enc" not in row and "auth_type" not in row
    # 被作业主机引用的凭据不可删除 → 42201
    resp = await client.delete(f"/api/v1/credentials/{env['credential_id']}", headers=admin_h)
    assert resp.json()["code"] == 42201
