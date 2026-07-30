"""个人资料修改 + 访问密钥（API Token）接口测试。

覆盖：资料自助修改回显、Token 创建/列表/删除、opsp_ Bearer 鉴权、
过期失效、密钥不可自我管理、每人数量上限。
"""
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.models.auth import ApiToken
from tests.conftest import auth_header, login_for_tokens

ME = "/api/v1/auth/me"
TOKENS = "/api/v1/user/tokens"


def token_header(plaintext: str) -> dict:
    """API Token 通道 Bearer 头。"""
    return {"Authorization": f"Bearer {plaintext}"}


async def _create(client, headers, name="ci-token", days=None) -> dict:
    """测试辅助：创建密钥并断言成功，返回 data（含明文 token）。"""
    resp = await client.post(TOKENS, json={"name": name, "expires_in_days": days}, headers=headers)
    body = resp.json()
    assert body["code"] == 0, f"创建密钥失败: {body}"
    return body["data"]


# ---------- 任务1：个人资料自助修改 ----------

async def test_update_profile(client):
    """修改显示名/邮箱后 /auth/me 回显新值；邮箱可清空。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    resp = await client.put(ME, json={"display_name": "运维一号", "email": "ops1@corp.com"},
                            headers=headers)
    assert resp.json()["code"] == 0
    me = (await client.get(ME, headers=headers)).json()["data"]
    assert me["display_name"] == "运维一号" and me["email"] == "ops1@corp.com"
    # 邮箱留空 -> 置 NULL
    await client.put(ME, json={"display_name": "运维一号", "email": ""}, headers=headers)
    me = (await client.get(ME, headers=headers)).json()["data"]
    assert me["email"] is None


async def test_update_profile_validation(client):
    """显示名为空 -> 40001 参数校验失败。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    resp = await client.put(ME, json={"display_name": "", "email": None}, headers=headers)
    assert resp.json()["code"] == 40001


# ---------- 任务2：访问密钥 CRUD 与鉴权 ----------

async def test_token_create_list_and_call_api(client):
    """明文仅创建时返回一次；列表只见前缀；opsp_ Bearer 可直调 API 且身份一致。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    data = await _create(client, headers, name="发布脚本", days=30)
    assert data["token"].startswith("opsp_") and data["prefix"] == data["token"][:12]
    assert data["expires_at"] is not None
    # 列表不含明文
    items = (await client.get(TOKENS, headers=headers)).json()["data"]["items"]
    assert len(items) == 1 and "token" not in items[0]
    # 用密钥调用 /auth/me：身份与本人一致
    me = (await client.get(ME, headers=token_header(data["token"]))).json()["data"]
    assert me["username"] == "ops1"


async def test_token_delete_revokes_immediately(client):
    """删除密钥后立即失效（40101）。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    data = await _create(client, headers)
    resp = await client.delete(f"{TOKENS}/{data['id']}", headers=headers)
    assert resp.json()["code"] == 0
    resp = await client.get(ME, headers=token_header(data["token"]))
    assert resp.status_code == 401 and resp.json()["code"] == 40101


async def test_token_expired_rejected(client, db_factory):
    """过期密钥拒绝访问；列表 expired 标记为真。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    data = await _create(client, headers, days=1)
    async with db_factory() as session:
        await session.execute(
            update(ApiToken).where(ApiToken.id == data["id"])
            .values(expires_at=datetime.now() - timedelta(minutes=1))
        )
        await session.commit()
    resp = await client.get(ME, headers=token_header(data["token"]))
    assert resp.status_code == 401
    items = (await client.get(TOKENS, headers=headers)).json()["data"]["items"]
    assert items[0]["expired"] is True


async def test_token_cannot_manage_tokens(client):
    """密钥不可自我繁殖：用 API Token 管理密钥一律 40301。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    data = await _create(client, headers)
    via_token = token_header(data["token"])
    for resp in [
        await client.get(TOKENS, headers=via_token),
        await client.post(TOKENS, json={"name": "evil"}, headers=via_token),
        await client.delete(f"{TOKENS}/{data['id']}", headers=via_token),
    ]:
        assert resp.status_code == 403 and resp.json()["code"] == 40301


async def test_token_isolation_and_limit(client):
    """只能删本人的密钥（他人 40401）；每人上限 10 个（42201）。"""
    ops_h = auth_header(await login_for_tokens(client, "ops1"))
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_token = await _create(client, ops_h)
    resp = await client.delete(f"{TOKENS}/{ops_token['id']}", headers=admin_h)
    assert resp.json()["code"] == 40401
    for i in range(9):  # ops1 已有 1 个，补满 10
        await _create(client, ops_h, name=f"t{i}")
    resp = await client.post(TOKENS, json={"name": "第11个"}, headers=ops_h)
    assert resp.json()["code"] == 42201


async def test_token_updates_last_used(client, db_factory):
    """调用一次后 last_used_at 落库。"""
    headers = auth_header(await login_for_tokens(client, "ops1"))
    data = await _create(client, headers)
    await client.get(ME, headers=token_header(data["token"]))
    async with db_factory() as session:
        record = (
            await session.execute(select(ApiToken).where(ApiToken.id == data["id"]))
        ).scalar_one()
        assert record.last_used_at is not None
