"""手动同步、发布前漂移和不确定写回读测试。"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.config_providers.base import ProviderRejectedError, ProviderUnavailableError, ProviderWriteUncertainError, RemoteContent
from app.core.security import encrypt_text
from app.models.application_config import ConfigFile, ConfigRemoteSnapshot, ConfigTask, ConfigVersion
from app.services import application_config_task_service
from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


async def _approved_missing_file(client, seed):
    admin = auth_header(await login_for_tokens(client, "admin"))
    cred = await client.post("/api/v1/credentials", headers=admin, json={
        "name": "nacos-token", "auth_type": "api_token", "secret": "test-token",
    })
    instance = await client.post("/api/v1/application-configs/platform-instances", headers=admin, json={
        "name": "prod", "provider": "nacos", "base_url": "https://nacos.example",
        "credential_id": cred.json()["data"]["id"],
    })
    created = await client.post("/api/v1/application-configs/files", headers=admin, json={
        "platform_instance_id": instance.json()["data"]["id"],
        "selection": {
            "name": "payment", "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
            "locator": {"namespace": "prod", "group": "PAY", "data_id": "payment.yaml"},
            "initial_content": "enabled: true\n",
        },
    })
    file_id = created.json()["data"]["id"]
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=admin)
    version_id = candidate.json()["data"]["id"]
    approved = await client.post(
        f"/api/v1/application-configs/files/{file_id}/candidates/{version_id}/approve", headers=admin,
    )
    assert approved.json()["data"]["status"] == "approved"
    return admin, file_id, version_id


async def _queued_publish_id(db_factory, version_id):
    async with db_factory() as session:
        return (await session.execute(select(ConfigTask.id).where(
            ConfigTask.config_version_id == version_id, ConfigTask.kind == "publish",
        ))).scalar_one()


async def test_approval_queues_publish_without_second_action(client, db_factory, seed, monkeypatch):
    """审批通过后立即排队，Worker 完成远端写入与回读才成为正式版本。"""
    class FakeAdapter:
        content = None

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.content else None

        async def write(self, connection, locator, content, *, expect):
            self.content = content.canonical

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    async with db_factory() as session:
        task = (await session.execute(select(ConfigTask).where(
            ConfigTask.config_file_id == file_id, ConfigTask.config_version_id == version_id,
        ))).scalar_one()
        assert task.status == "queued"
        assert await application_config_task_service.process_next_config_task(session)
        assert (await session.get(ConfigVersion, version_id)).status == "published"
    assert fake.content is not None


async def test_auto_publish_stops_after_three_transient_retries(client, db_factory, seed, monkeypatch):
    """首次失败后最多再试三次，达到上限仍失败可人工恢复。"""
    class FakeAdapter:
        reads = 0

        async def read(self, connection, locator):
            self.reads += 1
            raise ProviderUnavailableError("read", "timeout")

        async def write(self, *args, **kwargs):
            return None

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    async with db_factory() as session:
        task = (await session.execute(select(ConfigTask).where(ConfigTask.config_version_id == version_id))).scalar_one()
        for retry in range(1, 4):
            assert await application_config_task_service.process_next_config_task(session)
            await session.refresh(task)
            assert task.status == "queued"
            assert task.retry_count == retry
            task.next_attempt_at = datetime.now()
            await session.commit()
        assert await application_config_task_service.process_next_config_task(session)
        await session.refresh(task)
        assert task.status == "failed"
        assert task.retry_count == 3
        assert (await session.get(ConfigVersion, version_id)).status == "approved"
        assert fake.reads == 4

    retried = await client.post(
        f"/api/v1/application-configs/files/{file_id}/versions/{version_id}/publish", headers=admin,
    )
    assert retried.json()["code"] == 0
    assert retried.json()["data"]["id"] != task.id


async def test_transient_read_failure_recovers_without_second_approval(client, db_factory, seed, monkeypatch):
    class FakeAdapter:
        reads = 0
        content = None

        async def read(self, connection, locator):
            self.reads += 1
            if self.reads == 1:
                raise ProviderUnavailableError("read", "timeout")
            return RemoteContent(locator, self.content) if self.content else None

        async def write(self, connection, locator, content, *, expect):
            self.content = content.canonical

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)
    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, task_id)
        assert task.status == "queued"
        assert not await application_config_task_service.process_next_config_task(session)
        task.next_attempt_at = datetime.now()
        await session.commit()
        assert await application_config_task_service.process_next_config_task(session)
        await session.refresh(task)
        assert task.status == "success"
        assert task.retry_count == 1
        assert (await session.get(ConfigVersion, version_id)).status == "published"


async def test_uncertain_write_retry_only_reads_back(client, db_factory, seed, monkeypatch):
    """写入结果不确定且首次回读失败时，重试不得重复写入。"""
    class FakeAdapter:
        reads = 0
        writes = 0
        content = None

        async def read(self, connection, locator):
            self.reads += 1
            if self.reads == 2:
                raise ProviderUnavailableError("read", "timeout")
            return RemoteContent(locator, self.content) if self.content else None

        async def write(self, connection, locator, content, *, expect):
            self.writes += 1
            self.content = content.canonical
            raise ProviderWriteUncertainError("write", "timeout")

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, task_id)
        assert task.status == "queued"
        assert task.write_attempted is True
        task.next_attempt_at = datetime.now()
        await session.commit()
        await application_config_task_service.process_next_config_task(session)
        await session.refresh(task)
        assert task.status == "success"
        assert (await session.get(ConfigVersion, version_id)).status == "published"
    assert fake.writes == 1


async def test_manual_retry_after_uncertain_write_never_writes_again(client, db_factory, seed, monkeypatch):
    """自动回读耗尽后，人工重试继承写入不确定状态。"""
    class FakeAdapter:
        reads = 0
        writes = 0

        async def read(self, connection, locator):
            self.reads += 1
            if 2 <= self.reads <= 5:
                raise ProviderUnavailableError("read", "timeout")
            return None

        async def write(self, *args, **kwargs):
            self.writes += 1
            raise ProviderWriteUncertainError("write", "timeout")

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)
    async with db_factory() as session:
        for retry in range(4):
            await application_config_task_service.process_next_config_task(session)
            task = await session.get(ConfigTask, task_id)
            if retry < 3:
                assert task.status == "queued"
                task.next_attempt_at = datetime.now()
                await session.commit()
        assert task.status == "failed"
        assert task.write_attempted is True
    restarted = await client.post(
        f"/api/v1/application-configs/files/{file_id}/versions/{version_id}/publish", headers=admin,
    )
    assert restarted.json()["code"] == 0
    async with db_factory() as session:
        retry_task = await session.get(ConfigTask, restarted.json()["data"]["id"])
        assert retry_task.write_attempted is True
        await application_config_task_service.process_next_config_task(session)
    assert fake.writes == 1


async def test_drift_blocks_auto_publish_without_retry(client, db_factory, seed, monkeypatch):
    class FakeAdapter:
        async def read(self, connection, locator):
            return RemoteContent(locator, "enabled: false\n")

        async def write(self, *args, **kwargs):
            raise AssertionError("漂移不能写远端")

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: FakeAdapter())
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)
    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, task_id)
        assert task.status == "failed"
        assert task.retry_count == 0
        assert (await session.get(ConfigFile, file_id)).drift_status == "drifted"


async def test_changed_remote_blocks_initial_publish_and_marks_drift(client, db_factory, seed, monkeypatch):
    """首次发布前远端突然出现时，不得覆盖外部配置。"""
    admin, file_id, version_id = await _approved_missing_file(client, seed)

    class FakeAdapter:
        write_calls = 0

        async def read(self, connection, locator):
            return RemoteContent(locator, "enabled: false\n", revision="external")

        async def write(self, *args, **kwargs):
            self.write_calls += 1

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        version = await session.get(ConfigVersion, version_id)
        assert config_file.drift_status == "drifted"
        assert version.status == "approved"
    assert fake.write_calls == 0


async def test_uncertain_write_uses_readback_without_second_write(client, db_factory, seed, monkeypatch):
    """远端写入超时后只回读，不自动重试写请求。"""
    admin, file_id, version_id = await _approved_missing_file(client, seed)

    class FakeAdapter:
        write_calls = 0
        did_write = False

        async def read(self, connection, locator):
            return RemoteContent(locator, "enabled: true\n") if self.did_write else None

        async def write(self, *args, **kwargs):
            self.write_calls += 1
            self.did_write = True
            raise ProviderWriteUncertainError("write", "timeout")

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        version = await session.get(ConfigVersion, version_id)
        assert config_file.current_version_id == version_id
        assert version.status == "published"
        assert config_file.drift_status == "clean"
    assert fake.write_calls == 1


async def test_sync_detecting_drift_completes_successfully(client, db_factory, seed, monkeypatch):
    admin, file_id, _ = await _approved_missing_file(client, seed)

    class FakeAdapter:
        async def read(self, connection, locator):
            return RemoteContent(locator, "enabled: false\n")

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: FakeAdapter())
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    response = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
    task_id = response.json()["data"]["id"]
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, task_id)
        config_file = await session.get(ConfigFile, file_id)
        assert config_file.drift_status == "drifted"
        assert task.status == "success"
        assert task.last_error is None


@pytest.mark.parametrize("prior_failed_attempt", [False, True])
async def test_confirmed_drift_republish_overwrites_only_reviewed_remote(
    client, db_factory, seed, monkeypatch, prior_failed_attempt,
):
    """主动确认漂移后按已查看的远端快照覆盖，常规发布基线不受影响。"""
    class FakeAdapter:
        content = None
        writes = 0

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.content is not None else None

        async def write(self, connection, locator, content, *, expect):
            assert expect.content == self.content
            self.writes += 1
            self.content = content.canonical

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    fake.content = "enabled: false\n"
    synced = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
    assert synced.json()["code"] == 0
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        assert config_file.drift_status == "drifted"
        snapshot_id = config_file.latest_snapshot_id
        if prior_failed_attempt:
            session.add(ConfigTask(
                kind="publish", config_file_id=file_id, config_version_id=version_id,
                platform_instance_id=config_file.platform_instance_id, status="failed",
                write_attempted=True, total_count=1, created_by=seed["users"]["admin"], actor_name="admin",
            ))
            await session.commit()
    view = await client.get(f"/api/v1/application-configs/files/{file_id}/drift", headers=admin)
    assert view.json()["data"]["drift_status"] == "drifted"
    assert view.json()["data"]["latest_snapshot_id"] == snapshot_id
    queued = await client.post(
        f"/api/v1/application-configs/files/{file_id}/versions/{version_id}/publish",
        headers=admin, json={"confirmed_snapshot_id": snapshot_id},
    )
    assert queued.json()["code"] == 0
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, queued.json()["data"]["id"])
        config_file = await session.get(ConfigFile, file_id)
        assert task.status == "success"
        assert config_file.drift_status == "clean"
        assert config_file.current_version_id == version_id
    assert fake.writes == 2
    assert "enabled: true" in fake.content


async def test_drift_republish_rejects_stale_confirmed_snapshot(client, db_factory, seed, monkeypatch):
    class FakeAdapter:
        content = None

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.content is not None else None

        async def write(self, connection, locator, content, *, expect):
            self.content = content.canonical

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    fake.content = "enabled: false\n"
    for attempt in range(2):
        response = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
        assert response.json()["code"] == 0
        async with db_factory() as session:
            await application_config_task_service.process_next_config_task(session)
            snapshot_id = (await session.get(ConfigFile, file_id)).latest_snapshot_id
            if attempt == 0:
                stale_snapshot_id = snapshot_id
    assert snapshot_id != stale_snapshot_id
    response = await client.post(
        f"/api/v1/application-configs/files/{file_id}/versions/{version_id}/publish",
        headers=admin, json={"confirmed_snapshot_id": stale_snapshot_id},
    )
    assert response.status_code == 409
    assert response.json()["code"] == 40901


async def test_drift_republish_does_not_write_if_remote_changes_after_confirmation(
    client, db_factory, seed, monkeypatch,
):
    class FakeAdapter:
        content = None
        writes = 0

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.content is not None else None

        async def write(self, connection, locator, content, *, expect):
            self.writes += 1
            self.content = content.canonical

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    fake.content = "enabled: false\n"
    synced = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
    assert synced.json()["code"] == 0
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        snapshot_id = (await session.get(ConfigFile, file_id)).latest_snapshot_id
    queued = await client.post(
        f"/api/v1/application-configs/files/{file_id}/versions/{version_id}/publish",
        headers=admin, json={"confirmed_snapshot_id": snapshot_id},
    )
    assert queued.json()["code"] == 0
    fake.content = "enabled: changed\n"
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, queued.json()["data"]["id"])
        assert task.status == "failed"
        assert (await session.get(ConfigFile, file_id)).drift_status == "drifted"
        assert (await session.get(ConfigVersion, version_id)).status == "published"
    assert fake.writes == 1


async def test_queued_drift_republish_cannot_overwrite_later_local_import(
    client, db_factory, seed, monkeypatch,
):
    class FakeAdapter:
        content = None
        writes = 0

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.content is not None else None

        async def write(self, connection, locator, content, *, expect):
            self.writes += 1
            self.content = content.canonical

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    fake.content = "enabled: false\n"
    synced = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
    assert synced.json()["code"] == 0
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        snapshot_id = config_file.latest_snapshot_id
    queued = await client.post(
        f"/api/v1/application-configs/files/{file_id}/versions/{version_id}/publish",
        headers=admin, json={"confirmed_snapshot_id": snapshot_id},
    )
    assert queued.json()["code"] == 0
    async with db_factory() as session:
        config_file = await session.get(ConfigFile, file_id)
        config_file.operation_expires_at = datetime.now() - timedelta(seconds=1)
        await session.commit()
    imported = await client.post(f"/api/v1/application-configs/files/{file_id}/drift/import", headers=admin)
    assert imported.json()["code"] == 0
    imported_version_id = imported.json()["data"]["id"]
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, queued.json()["data"]["id"])
        config_file = await session.get(ConfigFile, file_id)
        assert task.status == "failed"
        assert config_file.current_version_id == imported_version_id
        assert config_file.drift_status == "clean"
    assert fake.writes == 1
    assert "enabled: false" in fake.content


async def test_failed_publish_readback_persists_remote_snapshot(client, db_factory, seed, monkeypatch):
    admin, file_id, version_id = await _approved_missing_file(client, seed)

    class FakeAdapter:
        attempted = False

        async def read(self, connection, locator):
            return RemoteContent(locator, "enabled: false\n") if self.attempted else None

        async def write(self, *args, **kwargs):
            self.attempted = True

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: FakeAdapter())
    task_id = await _queued_publish_id(db_factory, version_id)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        latest = await session.get(ConfigRemoteSnapshot, config_file.latest_snapshot_id)
        assert latest.exists is True
        assert config_file.drift_status == "drifted"
        assert (await session.get(ConfigVersion, version_id)).status == "approved"
        assert (await session.get(ConfigTask, task_id)).status == "failed"


async def test_stale_task_lease_cannot_process_config_file(client, db_factory, seed, monkeypatch):
    admin, file_id, _ = await _approved_missing_file(client, seed)

    class FakeAdapter:
        read_calls = 0

        async def read(self, connection, locator):
            self.read_calls += 1
            return None

        async def write(self, *args, **kwargs):
            return None

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    response = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
    task_id = response.json()["data"]["id"]
    async with db_factory() as session:
        task = await session.get(ConfigTask, task_id)
        task.status = "running"
        task.lease_token = "new-owner"
        await session.commit()
        await application_config_task_service._process_single_task(session, task_id, "expired-owner")
        await session.refresh(task)
        assert task.status == "running"
        assert task.lease_token == "new-owner"
    assert fake.read_calls == 2


async def test_lost_lease_during_publish_read_never_writes(client, db_factory, seed, monkeypatch):
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)

    class FakeAdapter:
        write_calls = 0

        async def read(self, connection, locator):
            async with db_factory() as other:
                task = await other.get(ConfigTask, task_id)
                task.lease_token = "replacement-owner"
                await other.commit()
            return None

        async def write(self, *args, **kwargs):
            self.write_calls += 1

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        task = await session.get(ConfigTask, task_id)
        assert task.status == "running"
        assert task.lease_token == "replacement-owner"
    assert fake.write_calls == 0


async def test_expired_lease_during_publish_read_never_writes(client, db_factory, seed, monkeypatch):
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)

    class FakeAdapter:
        write_calls = 0

        async def read(self, connection, locator):
            async with db_factory() as other:
                task = await other.get(ConfigTask, task_id)
                task.lease_expires_at = datetime.now() - timedelta(seconds=1)
                await other.commit()
            return None

        async def write(self, *args, **kwargs):
            self.write_calls += 1

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    assert fake.write_calls == 0


async def test_replaced_file_operation_lock_never_writes(client, db_factory, seed, monkeypatch):
    admin, file_id, version_id = await _approved_missing_file(client, seed)

    class FakeAdapter:
        write_calls = 0

        async def read(self, connection, locator):
            async with db_factory() as other:
                config_file = await other.get(ConfigFile, file_id)
                config_file.operation_token = "replacement-task"
                await other.commit()
            return None

        async def write(self, *args, **kwargs):
            self.write_calls += 1

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
    assert fake.write_calls == 0


async def test_queued_publish_does_not_write_after_file_archived(client, db_factory, seed, monkeypatch):
    admin, file_id, version_id = await _approved_missing_file(client, seed)
    task_id = await _queued_publish_id(db_factory, version_id)

    class FakeAdapter:
        calls = 0

        async def read(self, *args):
            self.calls += 1
            return None

        async def write(self, *args, **kwargs):
            self.calls += 1

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        config_file = await session.get(ConfigFile, file_id)
        config_file.status = "archived"
        await session.commit()
        await application_config_task_service.process_next_config_task(session)
        assert (await session.get(ConfigTask, task_id)).status == "failed"
        assert (await session.get(ConfigVersion, version_id)).status == "approved"
    assert fake.calls == 0


async def test_cas_conflict_captures_external_change_as_drift(client, db_factory, seed, monkeypatch):
    admin, file_id, version_id = await _approved_missing_file(client, seed)

    class FakeAdapter:
        changed = False

        async def read(self, connection, locator):
            return RemoteContent(locator, "enabled: false\n") if self.changed else None

        async def write(self, *args, **kwargs):
            self.changed = True
            raise ProviderRejectedError("write", "remote_changed")

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: FakeAdapter())
    task_id = await _queued_publish_id(db_factory, version_id)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        snapshot = await session.get(ConfigRemoteSnapshot, config_file.latest_snapshot_id)
        assert snapshot.exists is True
        assert config_file.drift_status == "drifted"
        assert (await session.get(ConfigTask, task_id)).status == "failed"


async def test_sync_drift_import_creates_local_version_without_write(client, db_factory, seed, monkeypatch):
    """确认导入外部差异只生成本地版本，不更新外部内容。"""
    admin, file_id, version_id = await _approved_missing_file(client, seed)

    class FakeAdapter:
        write_calls = 0
        content = "enabled: true\n"
        did_write = False

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.did_write else None

        async def write(self, *args, **kwargs):
            self.write_calls += 1
            self.did_write = True

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)

    fake.content = "enabled: false\n"
    synced = await client.post(f"/api/v1/application-configs/files/{file_id}/sync", headers=admin)
    assert synced.json()["code"] == 0
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        assert (await session.get(ConfigFile, file_id)).drift_status == "drifted"
    view = await client.get(f"/api/v1/application-configs/files/{file_id}/drift", headers=admin)
    assert "enabled: false" in view.json()["data"]["external_content"]

    imported = await client.post(f"/api/v1/application-configs/files/{file_id}/drift/import", headers=admin)
    assert imported.json()["code"] == 0
    async with db_factory() as session:
        versions = list((await session.execute(
            select(ConfigVersion).where(ConfigVersion.config_file_id == file_id)
        )).scalars())
        assert len(versions) == 2
        assert versions[1].source == "external_import"
        assert (await session.get(ConfigFile, file_id)).drift_status == "clean"
    assert fake.write_calls == 1


async def test_drift_import_response_loads_server_timestamp_without_returning(client, db_factory, seed, monkeypatch):
    """MySQL 不支持 INSERT RETURNING 时，漂移导入响应仍须包含创建时间。"""
    admin, file_id, _ = await _approved_missing_file(client, seed)
    class FakeAdapter:
        async def read(self, connection, locator):
            return None

        async def write(self, *args, **kwargs):
            return None

    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: FakeAdapter())
    async with db_factory() as session:
        await application_config_task_service.process_next_config_task(session)
        config_file = await session.get(ConfigFile, file_id)
        snapshot = ConfigRemoteSnapshot(
            config_file_id=file_id, exists=True, content_enc=encrypt_text("enabled: false\n"),
            observed_at=datetime.now(),
        )
        session.add(snapshot)
        await session.flush()
        config_file.latest_snapshot_id = snapshot.id
        config_file.drift_status = "drifted"
        await session.commit()

    monkeypatch.setattr(ConfigVersion.__table__, "implicit_returning", False)
    imported = await client.post(f"/api/v1/application-configs/files/{file_id}/drift/import", headers=admin)
    assert imported.json()["code"] == 0
    assert imported.json()["data"]["created_at"] is not None
