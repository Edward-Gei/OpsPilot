"""配置审批与发布站内信：接收人、事务和最终结果边界。"""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.config_providers.base import ProviderRejectedError, ProviderUnavailableError, RemoteContent
from app.core.security import encrypt_text
from app.models.application_config import ConfigDraft, ConfigFile, ConfigPlatformInstance, ConfigRemoteSnapshot, ConfigTask, ConfigVersion
from app.models.auth import User, UserRole
from app.models.job import Credential
from app.models.notify import NotificationRecord, UserNotification
from app.services import application_config_service, application_config_task_service, notify_service
from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


@pytest.fixture
async def config_file_id(db_factory, seed):
    async with db_factory() as session:
        credential = Credential(name="notify-token", auth_type="api_token", secret_enc=encrypt_text("private-token"))
        session.add(credential)
        await session.flush()
        instance = ConfigPlatformInstance(name="notify-platform", provider="nacos", base_url="https://nacos.example",
                                          credential_id=credential.id, enabled=True)
        session.add(instance)
        await session.flush()
        config_file = ConfigFile(name="payment", platform_instance_id=instance.id, locator_key="notify-file",
                                 locator={"namespace": "prod", "group": "PAY", "data_id": "payment.yaml"},
                                 content_format="yaml", approval_role_id=seed["roles"]["ops"])
        session.add(config_file)
        await session.flush()
        baseline = ConfigRemoteSnapshot(config_file_id=config_file.id, exists=False, observed_at=datetime.now())
        session.add(baseline)
        await session.flush()
        config_file.current_snapshot_id = baseline.id
        config_file.latest_snapshot_id = baseline.id
        session.add(ConfigDraft(config_file_id=config_file.id,
                                base_snapshot_id=baseline.id,
                                content_enc=encrypt_text("password: private-config-value\n")))
        await session.commit()
        return config_file.id


async def _submit(client, file_id):
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=headers)
    assert response.json()["code"] == 0
    return response.json()["data"]["id"]


async def _approve(client, file_id, version_id, actor="ops1"):
    headers = auth_header(await login_for_tokens(client, actor))
    response = await client.post(
        f"/api/v1/application-configs/files/{file_id}/candidates/{version_id}/approve", headers=headers,
    )
    assert response.json()["code"] == 0


async def _inbox(session, event):
    return list((await session.execute(select(UserNotification).where(UserNotification.event == event))).scalars())


class MemoryAdapter:
    """只替换远端配置平台 I/O，业务服务与数据库均走真实实现。"""
    content = None

    async def read(self, connection, locator):
        return RemoteContent(locator, self.content) if self.content is not None else None

    async def write(self, connection, locator, content, *, expect):
        self.content = content.text


async def test_pending_approval_notifies_only_active_role_members(client, db_factory, seed, config_file_id):
    async with db_factory() as session:
        inactive = await session.get(User, seed["users"]["newbie"])
        inactive.status = "disabled"
        await session.commit()
    version_id = await _submit(client, config_file_id)
    async with db_factory() as session:
        rows = await _inbox(session, "config.pending_approval")
        assert [row.user_id for row in rows] == [seed["users"]["ops1"]]
        assert rows[0].ref_type == "config_approval" and rows[0].ref_id == version_id
        assert "payment" in rows[0].title and not rows[0].is_read
        assert "private-config-value" not in (rows[0].content or "")
        assert list((await session.execute(select(NotificationRecord))).scalars()) == []


@pytest.mark.parametrize(("decision", "event"), [("approve", "config.approved"), ("reject", "config.rejected")])
async def test_approval_result_notifies_submitter_only(client, db_factory, seed, config_file_id, decision, event):
    version_id = await _submit(client, config_file_id)
    headers = auth_header(await login_for_tokens(client, "ops1"))
    response = await client.post(
        f"/api/v1/application-configs/files/{config_file_id}/candidates/{version_id}/{decision}",
        headers=headers, json={"reason": "请补充参数"} if decision == "reject" else None,
    )
    assert response.json()["code"] == 0
    async with db_factory() as session:
        rows = await _inbox(session, event)
        assert [row.user_id for row in rows] == [seed["users"]["admin"]]
        assert rows[0].ref_type == "config_file" and rows[0].ref_id == config_file_id
        assert "private-config-value" not in (rows[0].content or "")
        assert await _inbox(session, "config.publish_success") == []
        assert list((await session.execute(select(NotificationRecord))).scalars()) == []


async def test_failed_auto_enqueue_rolls_back_approval_notice(client, db_factory, seed, config_file_id):
    version_id = await _submit(client, config_file_id)
    async with db_factory() as session:
        config_file = await session.get(ConfigFile, config_file_id)
        instance = await session.get(ConfigPlatformInstance, config_file.platform_instance_id)
        instance.enabled = False
        await session.commit()
    headers = auth_header(await login_for_tokens(client, "ops1"))
    response = await client.post(
        f"/api/v1/application-configs/files/{config_file_id}/candidates/{version_id}/approve", headers=headers,
    )
    assert response.json()["code"] != 0
    async with db_factory() as session:
        assert (await session.get(ConfigVersion, version_id)).status == "pending_approval"
        assert await _inbox(session, "config.approved") == []


async def test_submit_rollback_discards_pending_notice(db_factory, seed, config_file_id):
    async with db_factory() as session:
        await application_config_service.submit_candidate(session, config_file_id, seed["users"]["admin"])
        assert len(await _inbox(session, "config.pending_approval")) == 2
        await session.rollback()
    async with db_factory() as session:
        assert await _inbox(session, "config.pending_approval") == []
        assert list((await session.execute(select(ConfigVersion))).scalars()) == []


@pytest.mark.parametrize("actor", ["admin", "ops1"])
async def test_publish_success_notifies_submitter_and_actor_once(
    client, db_factory, seed, config_file_id, monkeypatch, actor,
):
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: MemoryAdapter())
    version_id = await _submit(client, config_file_id)
    await _approve(client, config_file_id, version_id, actor)
    async with db_factory() as session:
        assert await _inbox(session, "config.publish_success") == []
        assert await application_config_task_service.process_next_config_task(session)
        rows = await _inbox(session, "config.publish_success")
        assert sorted(row.user_id for row in rows) == sorted({seed["users"]["admin"], seed["users"][actor]})
        assert all(row.ref_type == "config_file" and row.ref_id == config_file_id for row in rows)
        assert all("private-config-value" not in (row.content or "") for row in rows)
        assert (await session.get(ConfigVersion, version_id)).status == "published"
        assert not await application_config_task_service.process_next_config_task(session)
        assert len(await _inbox(session, "config.publish_success")) == len(rows)
        assert list((await session.execute(select(NotificationRecord))).scalars()) == []


async def test_transient_retries_only_notify_final_failure(client, db_factory, seed, config_file_id, monkeypatch):
    class UnavailableAdapter(MemoryAdapter):
        async def read(self, connection, locator):
            raise ProviderUnavailableError("read", "timeout")

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: UnavailableAdapter())
    version_id = await _submit(client, config_file_id)
    await _approve(client, config_file_id, version_id)
    async with db_factory() as session:
        task_id = (await session.execute(select(ConfigTask.id))).scalar_one()
        for attempt in range(4):
            assert await application_config_task_service.process_next_config_task(session)
            task = await session.get(ConfigTask, task_id)
            rows = await _inbox(session, "config.publish_failed")
            if attempt < 3:
                assert task.status == "queued" and rows == []
                task.next_attempt_at = datetime.now()
                await session.commit()
            else:
                assert task.status == "failed"
                assert sorted(row.user_id for row in rows) == sorted([seed["users"]["admin"], seed["users"]["ops1"]])
                assert all("private-token" not in (row.content or "") for row in rows)
                assert all(task.last_error in row.content for row in rows)
        assert await _inbox(session, "config.publish_success") == []


async def test_publish_notice_db_failure_preserves_success(client, db_factory, seed, config_file_id, monkeypatch, caplog):
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: MemoryAdapter())
    version_id = await _submit(client, config_file_id)
    await _approve(client, config_file_id, version_id)

    async def invalid_notice(session, *args, **kwargs):
        # 模拟写入数据库时约束失败，验证保存点能恢复会话和业务结果。
        session.add(UserNotification(user_id=None, event="config.publish_success", title="invalid"))
        await session.flush()

    monkeypatch.setattr(notify_service, "emit_inapp", invalid_notice)
    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session)
    async with db_factory() as session:
        task = (await session.execute(select(ConfigTask))).scalar_one()
        assert task.status == "success"
        assert (await session.get(ConfigVersion, version_id)).status == "published"
        assert (await session.get(ConfigFile, config_file_id)).operation_token is None
        assert await _inbox(session, "config.publish_failed") == []
        assert await _inbox(session, "config.publish_success") == []
        assert "站内信写入失败" in caplog.text


async def test_sync_does_not_emit_publish_notice(client, db_factory, seed, config_file_id, monkeypatch):
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: MemoryAdapter())
    async with db_factory() as session:
        await application_config_task_service.create_sync_task(
            session, config_file_id, application_config_service.ConfigActor(seed["users"]["admin"], "admin"),
        )
        await session.commit()
        assert await application_config_task_service.process_next_config_task(session)
        assert list((await session.execute(select(UserNotification))).scalars()) == []


async def test_removed_approver_cannot_emit_result(client, db_factory, seed, config_file_id):
    version_id = await _submit(client, config_file_id)
    headers = auth_header(await login_for_tokens(client, "ops1"))
    async with db_factory() as session:
        membership = (await session.execute(select(UserRole).where(
            UserRole.user_id == seed["users"]["ops1"], UserRole.role_id == seed["roles"]["ops"],
        ))).scalar_one()
        await session.delete(membership)
        await session.commit()
    response = await client.post(
        f"/api/v1/application-configs/files/{config_file_id}/candidates/{version_id}/approve", headers=headers,
    )
    assert response.status_code == 403
    async with db_factory() as session:
        assert await _inbox(session, "config.approved") == []
        assert (await session.get(ConfigVersion, version_id)).status == "pending_approval"


async def test_manual_retry_notifies_original_submitter_and_new_actor(client, db_factory, seed, config_file_id, monkeypatch):
    class RejectedAdapter(MemoryAdapter):
        async def read(self, connection, locator):
            raise ProviderRejectedError("read", "http_403")

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: RejectedAdapter())
    version_id = await _submit(client, config_file_id)
    await _approve(client, config_file_id, version_id)
    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session)
        assert len(await _inbox(session, "config.publish_failed")) == 2
        monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: MemoryAdapter())
        task = await application_config_task_service.create_publish_task(
            session, config_file_id, version_id,
            application_config_service.ConfigActor(seed["users"]["admin"], "admin"),
        )
        await session.commit()
        assert await application_config_task_service.process_next_config_task(session)
        new_rows = await _inbox(session, "config.publish_success")
        assert [row.user_id for row in new_rows] == [seed["users"]["admin"]]
        assert f"#{task.id}" in new_rows[0].content
