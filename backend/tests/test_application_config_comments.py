"""配置注释在接入、版本、草稿、发布及漂移处理之间保留。"""
import pytest
from sqlalchemy import select

from app.config_providers.base import RemoteContent
from app.core.security import decrypt_text
from app.models.application_config import ConfigFile, ConfigRemoteSnapshot, ConfigVersion
from app.services import application_config_task_service as tasks
from tests.conftest import auth_header, login_for_tokens


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("content_format, original, changed", [
    ("yaml", "# 原说明\nenabled: true # 开关\n", "# 新说明\nenabled: true # 开关\n"),
    ("properties", "# 原说明\n! 开关\nenabled=true\n", "# 新说明\n! 开关\nenabled=true\n"),
])
async def test_comments_survive_import_draft_publish_and_comment_only_drift(
    client, db_factory, seed, monkeypatch, content_format, original, changed,
):
    """正文入库被规范化或只比较键值，会丢失注释或漏报注释漂移。"""
    headers = auth_header(await login_for_tokens(client, "admin"))
    cred = await client.post("/api/v1/credentials", headers=headers, json={
        "name": "nacos", "auth_type": "api_token", "secret": "test-token",
    })
    instance = await client.post("/api/v1/application-configs/platform-instances", headers=headers, json={
        "name": "nacos", "provider": "nacos", "base_url": "https://nacos.example",
        "credential_id": cred.json()["data"]["id"],
    })

    class Remote:
        content = original
        writes = 0

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content)

        async def write(self, connection, locator, content, *, expect):
            assert expect.content == self.content
            self.content = content.text
            self.writes += 1

    remote = Remote()
    monkeypatch.setattr(tasks, "get_config_adapter", lambda _: remote)
    created = await client.post("/api/v1/application-configs/import-tasks", headers=headers, json={
        "platform_instance_id": instance.json()["data"]["id"],
        "selections": [{"name": "comments", "content_format": content_format,
                        "approval_role_id": seed["roles"]["ops"],
                        "locator": {"namespace": "", "group": "DEFAULT_GROUP", "data_id": "comments"}}],
    })
    assert created.json()["code"] == 0
    async with db_factory() as session:
        assert await tasks.process_next_config_task(session)
        file = (await session.execute(select(ConfigFile))).scalar_one()
        file_id, version_id = file.id, file.current_version_id
        version = await session.get(ConfigVersion, version_id)
        snapshot = await session.get(ConfigRemoteSnapshot, file.current_snapshot_id)
        assert decrypt_text(version.content_enc) == decrypt_text(snapshot.content_enc) == original
    prefix = f"/api/v1/application-configs/files/{file_id}"
    preview = await client.get(f"{prefix}/versions/{version_id}/content", headers=headers)
    assert preview.json()["data"]["content"] == original
    draft = await client.get(f"{prefix}/draft", headers=headers)
    assert draft.json()["data"]["content"] == original
    saved = await client.put(f"{prefix}/draft", headers=headers, json={"content": changed})
    assert saved.json()["code"] == 0
    candidate = await client.post(f"{prefix}/candidates", headers=headers)
    candidate_id = candidate.json()["data"]["id"]
    approved = await client.post(f"{prefix}/candidates/{candidate_id}/approve", headers=headers)
    assert approved.json()["code"] == 0
    async with db_factory() as session:
        assert await tasks.process_next_config_task(session)
        assert (await session.get(ConfigVersion, candidate_id)).status == "published"
    assert remote.content == changed
    assert remote.writes == 1
    remote.content = original
    synced = await client.post(f"{prefix}/sync", headers=headers)
    assert synced.json()["code"] == 0
    async with db_factory() as session:
        assert await tasks.process_next_config_task(session)
        assert (await session.get(ConfigFile, file_id)).drift_status == "drifted"
    view = await client.get(f"{prefix}/drift", headers=headers)
    assert view.json()["data"]["baseline_content"] == changed
    assert view.json()["data"]["external_content"] == original
    imported = await client.post(f"{prefix}/drift/import", headers=headers)
    assert imported.json()["code"] == 0
    preview = await client.get(f'{prefix}/versions/{imported.json()["data"]["id"]}/content', headers=headers)
    assert preview.json()["data"]["content"] == original
    assert remote.writes == 1


@pytest.mark.parametrize("content_format, left, right", [
    ("yaml", "enabled: true\nsize: 10\n", "size: 10\nenabled: true\n"),
    ("properties", "enabled=true\nsize=10\n", "size: 10\nenabled : true\n"),
])
async def test_layout_only_changes_remain_consistent(content_format, left, right):
    """保留注释后仍沿用语义比较，排版或键顺序变化不单独产生漂移。"""
    assert tasks._same_content(content_format, left, right)


async def test_apollo_properties_local_comments_do_not_fail_key_value_readback(client, db_factory, seed, monkeypatch):
    """Apollo properties 的发布版本只回读键值，本地注释仍保留在正式版本。"""
    headers = auth_header(await login_for_tokens(client, "admin"))
    cred = await client.post("/api/v1/credentials", headers=headers, json={
        "name": "apollo", "auth_type": "api_token", "secret": "test-token",
    })
    instance = await client.post("/api/v1/application-configs/platform-instances", headers=headers, json={
        "name": "apollo", "provider": "apollo", "base_url": "https://apollo.example",
        "credential_id": cred.json()["data"]["id"],
    })
    created = await client.post("/api/v1/application-configs/files", headers=headers, json={
        "platform_instance_id": instance.json()["data"]["id"],
        "selection": {"name": "comments", "content_format": "properties", "approval_role_id": seed["roles"]["ops"],
                      "locator": {"app_id": "payment", "cluster": "default", "namespace": "application"},
                      "initial_content": "# 本地说明\nenabled=true\n"},
    })
    file_id = created.json()["data"]["id"]

    class Remote:
        content = None

        async def read(self, connection, locator):
            return RemoteContent(locator, self.content) if self.content is not None else None

        async def write(self, connection, locator, content, *, expect):
            self.content = "enabled=true\n"

    remote = Remote()
    monkeypatch.setattr(tasks, "get_config_adapter", lambda _: remote)
    candidate = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates", headers=headers)
    candidate_id = candidate.json()["data"]["id"]
    approved = await client.post(f"/api/v1/application-configs/files/{file_id}/candidates/{candidate_id}/approve", headers=headers)
    assert approved.json()["code"] == 0
    async with db_factory() as session:
        assert await tasks.process_next_config_task(session)
        version = await session.get(ConfigVersion, candidate_id)
        assert version.status == "published"
        assert decrypt_text(version.content_enc) == "# 本地说明\nenabled=true\n"
