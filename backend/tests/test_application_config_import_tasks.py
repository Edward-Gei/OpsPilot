"""多选远端配置接入任务逐项落库测试。"""
import pytest
from sqlalchemy import select

from app.config_providers.base import RemoteContent, ProviderRejectedError, ProviderUnavailableError
from app.models.application_config import ConfigDraft, ConfigFile, ConfigTask, ConfigVersion
from app.services import application_config_task_service
from app.services.application_config_service import ConfigActor
from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("names, statuses, progress", [
    (("ok.yaml", "bad.yaml"), ["success", "failed"], [(0, 0, 0), (1, 0, 0)]),
    (("bad-network.yaml", "ok.yaml", "later.yaml"), ["failed", "success", "success"],
     [(0, 0, 0), (0, 0, 1), (1, 0, 1)]),
    (("ok.yaml", "bad-format.yaml", "later.yaml"), ["success", "failed", "success"],
     [(0, 0, 0), (1, 0, 0), (1, 0, 1)]),
    (("bad-network.yaml", "bad-format.yaml"), ["failed", "failed"], [(0, 0, 0), (0, 0, 1)]),
    (("already.yaml", "bad-network.yaml", "ok.yaml"), ["skipped", "failed", "success"],
     [(0, 1, 0), (0, 1, 1)]),
    (("ok.yaml", "later.yaml"), ["success", "success"], [(0, 0, 0), (1, 0, 0)]),
])
async def test_multi_select_import_keeps_success_and_never_writes_remote(
    client, db_factory, seed, monkeypatch, names, statuses, progress,
):
    """失败项不回滚成功项；已有远端接入直接建立正式版本。"""
    headers = auth_header(await login_for_tokens(client, "admin"))
    cred = await client.post("/api/v1/credentials", headers=headers, json={
        "name": "nacos-token", "auth_type": "api_token", "secret": "test-token",
    })
    instance = await client.post("/api/v1/application-configs/platform-instances", headers=headers, json={
        "name": "prod", "provider": "nacos", "base_url": "https://nacos.example",
        "credential_id": cred.json()["data"]["id"],
    })
    instance_id = instance.json()["data"]["id"]
    existing_file_id = None
    if "already.yaml" in names:
        existing = await client.post("/api/v1/application-configs/files", headers=headers, json={
            "platform_instance_id": instance_id,
            "selection": {"name": "already.yaml", "content_format": "yaml",
                          "approval_role_id": seed["roles"]["ops"],
                          "locator": {"namespace": "prod", "group": "PAY", "data_id": "already.yaml"}},
        })
        existing_file_id = existing.json()["data"]["id"]
    observed_progress = []

    class FakeAdapter:
        write_calls = 0

        async def read(self, connection, locator):
            # 在下一项远端读取期间，用独立会话检查已完成项的持久化进度。
            async with db_factory() as session:
                current = await session.get(ConfigTask, task_id)
                observed_progress.append((current.success_count, current.skipped_count, current.failed_count))
            if locator["data_id"] == "bad-network.yaml":
                raise ProviderUnavailableError("read", "network")
            if locator["data_id"] == "bad-format.yaml":
                return RemoteContent(locator, "enabled: [", revision="1")
            if locator["data_id"] == "bad.yaml":
                raise ProviderRejectedError("read", "http_403")
            return RemoteContent(locator, "enabled: true\n", revision="1")

        async def write(self, *args, **kwargs):
            self.write_calls += 1

    fake = FakeAdapter()
    monkeypatch.setattr(application_config_task_service, "get_config_adapter", lambda _: fake)
    selections = [
        {"name": name, "content_format": "yaml", "approval_role_id": seed["roles"]["ops"],
         "locator": {"namespace": "prod", "group": "PAY", "data_id": name}}
        for name in names
    ]
    created = await client.post("/api/v1/application-configs/import-tasks", headers=headers, json={
        "platform_instance_id": instance_id, "selections": selections,
    })
    assert created.json()["code"] == 0
    task_id = created.json()["data"]["id"]

    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session) is True
    task = await client.get(f"/api/v1/application-configs/tasks/{task_id}", headers=headers)
    data = task.json()["data"]
    expected_status = "partial_failed" if "failed" in statuses and len(set(statuses)) > 1 else (
        "failed" if "failed" in statuses else "success"
    )
    assert data["status"] == expected_status
    assert [item["status"] for item in data["items"]] == statuses
    assert (data["success_count"], data["skipped_count"], data["failed_count"]) == (
        statuses.count("success"), statuses.count("skipped"), statuses.count("failed"),
    )
    assert sum(data[key] for key in ("success_count", "skipped_count", "failed_count")) == data["total_count"]
    assert observed_progress == progress
    async with db_factory() as session:
        files = list((await session.execute(select(ConfigFile))).scalars())
        versions = list((await session.execute(select(ConfigVersion))).scalars())
        drafts = list((await session.execute(select(ConfigDraft))).scalars())
    assert len(files) == statuses.count("success") + statuses.count("skipped")
    assert len(versions) == statuses.count("success")
    assert all(version.source == "external_import" for version in versions)
    assert [draft.config_file_id for draft in drafts] == ([existing_file_id] if existing_file_id else [])
    assert fake.write_calls == 0
