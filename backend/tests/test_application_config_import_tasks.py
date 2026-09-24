"""多选远端配置接入任务逐项落库测试。"""
import pytest
from sqlalchemy import select

from app.config_providers.base import RemoteContent, ProviderRejectedError
from app.models.application_config import ConfigDraft, ConfigFile, ConfigVersion
from app.services import application_config_task_service
from app.services.application_config_service import ConfigActor
from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


async def test_multi_select_import_keeps_success_and_never_writes_remote(client, db_factory, seed, monkeypatch):
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

    class FakeAdapter:
        write_calls = 0

        async def read(self, connection, locator):
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
        for name in ("ok.yaml", "bad.yaml")
    ]
    created = await client.post("/api/v1/application-configs/import-tasks", headers=headers, json={
        "platform_instance_id": instance_id, "selections": selections,
    })
    assert created.json()["code"] == 0
    task_id = created.json()["data"]["id"]

    async with db_factory() as session:
        assert await application_config_task_service.process_next_config_task(session) is True
    task = await client.get(f"/api/v1/application-configs/tasks/{task_id}", headers=headers)
    assert task.json()["data"]["status"] == "partial_failed"
    assert [item["status"] for item in task.json()["data"]["items"]] == ["success", "failed"]
    async with db_factory() as session:
        files = list((await session.execute(select(ConfigFile))).scalars())
        versions = list((await session.execute(select(ConfigVersion))).scalars())
        drafts = list((await session.execute(select(ConfigDraft))).scalars())
    assert len(files) == len(versions) == 1
    assert versions[0].source == "external_import"
    assert drafts == []
    assert fake.write_calls == 0
