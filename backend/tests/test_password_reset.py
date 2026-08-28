import re
import pytest
from sqlalchemy import select

from app.core import security
from app.models.auth import User
from app.models.notify import NotificationRecord, NotifyChannel


@pytest.mark.asyncio
async def test_forgot_password_returns_uniform_response_for_unknown_email(client):
    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": "missing@example.com"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["data"]["message"] == "如果该邮箱已绑定本地账号，系统将发送密码重置邮件，请查收邮箱。"


@pytest.mark.asyncio
async def test_public_password_policy_endpoint(client):
    response = await client.get("/api/v1/auth/password-policy")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["min_length"] == 8
    assert data["require_complex"] is True


@pytest.mark.asyncio
async def test_sso_options_include_forgot_password_capability(client):
    response = await client.get("/api/v1/auth/sso/options")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["forgot_password_enabled"] is False


@pytest.mark.asyncio
async def test_disabled_smtp_is_not_a_password_reset_capability(client, db_factory):
    async with db_factory() as session:
        session.add(NotifyChannel(
            type="email",
            enabled=False,
            config={"host": "smtp.test", "from_addr": "noreply@test"},
        ))
        await session.commit()

    response = await client.get("/api/v1/auth/sso/options")
    assert response.status_code == 200
    assert response.json()["data"]["forgot_password_enabled"] is False


@pytest.mark.asyncio
async def test_local_email_reset_token_is_valid_until_atomic_submit(client, db_factory):
    async with db_factory() as session:
        user = (await session.execute(select(User).where(User.username == "ops1"))).scalar_one()
        user.email = "ops1@example.com"
        session.add(NotifyChannel(type="email", enabled=True, config={"host": "smtp.test", "from_addr": "noreply@test"}))
        await session.commit()

    response = await client.post("/api/v1/auth/forgot-password", json={"email": "OPS1@example.com"})
    assert response.status_code == 200
    async with db_factory() as session:
        record = (await session.execute(select(NotificationRecord))).scalar_one()
        token = re.search(r"token=([^\n]+)", record.content).group(1)

    assert (await client.get("/api/v1/auth/reset-password/validate", params={"token": token})).json()["data"]["status"] == "valid"
    weak = await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "weak"})
    assert weak.status_code == 400
    assert (await client.get("/api/v1/auth/reset-password/validate", params={"token": token})).json()["data"]["status"] == "valid"
    success = await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "Reset789xyz"})
    assert success.status_code == 200
    assert (await client.get("/api/v1/auth/reset-password/validate", params={"token": token})).json()["data"]["status"] == "used"


@pytest.mark.asyncio
async def test_reset_token_survives_password_hash_failure(client, db_factory, monkeypatch):
    async with db_factory() as session:
        user = (await session.execute(select(User).where(User.username == "ops1"))).scalar_one()
        user.email = "ops1-hash-failure@example.com"
        session.add(NotifyChannel(type="email", enabled=True, config={"host": "smtp.test", "from_addr": "noreply@test"}))
        await session.commit()

    await client.post("/api/v1/auth/forgot-password", json={"email": "ops1-hash-failure@example.com"})
    async with db_factory() as session:
        record = (await session.execute(select(NotificationRecord))).scalar_one()
        token = re.search(r"token=([^\n]+)", record.content).group(1)

    def fail_hash(_: str) -> str:
        raise RuntimeError("hash unavailable")

    monkeypatch.setattr(security, "hash_password", fail_hash)
    with pytest.raises(RuntimeError, match="hash unavailable"):
        await client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "Reset789xyz"})
    status = (await client.get("/api/v1/auth/reset-password/validate", params={"token": token})).json()["data"]["status"]
    assert status == "valid"
