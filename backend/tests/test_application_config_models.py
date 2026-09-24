"""应用配置 ORM 约束测试。"""
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.application_config import ConfigDraft, ConfigFile, ConfigPlatformInstance, ConfigTaskItem
from app.services.application_config_service import normalize_locator


pytestmark = pytest.mark.asyncio


async def test_config_file_locator_is_unique(db_factory):
    """同一平台实例不能重复管理同一个规范化远端定位器。"""
    async with db_factory() as session:
        instance = ConfigPlatformInstance(
            name="nacos-prod",
            provider="nacos",
            base_url="https://nacos.example",
            credential_id=1,
        )
        session.add(instance)
        await session.flush()
        locator_key = '{"data_id":"payment.yaml","group":"DEFAULT_GROUP","namespace":"prod"}'
        session.add(ConfigFile(
            name="payment-prod",
            platform_instance_id=instance.id,
            locator={"namespace": "prod", "group": "DEFAULT_GROUP", "data_id": "payment.yaml"},
            locator_key=locator_key,
            content_format="yaml",
            approval_role_id=1,
        ))
        await session.flush()
        session.add(ConfigFile(
            name="payment-copy",
            platform_instance_id=instance.id,
            locator={"namespace": "prod", "group": "DEFAULT_GROUP", "data_id": "payment.yaml"},
            locator_key=locator_key,
            content_format="yaml",
            approval_role_id=1,
        ))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_config_file_has_only_one_editable_draft(db_factory):
    """单个配置文件同时只允许保存一份可编辑草稿。"""
    async with db_factory() as session:
        instance = ConfigPlatformInstance(
            name="apollo-prod",
            provider="apollo",
            base_url="https://apollo.example",
            credential_id=1,
        )
        session.add(instance)
        await session.flush()
        file = ConfigFile(
            name="payment-properties",
            platform_instance_id=instance.id,
            locator={"app_id": "payment", "cluster": "default", "namespace": "application"},
            locator_key='{"app_id":"payment","cluster":"default","namespace":"application"}',
            content_format="properties",
            approval_role_id=1,
        )
        session.add(file)
        await session.flush()
        session.add(ConfigDraft(config_file_id=file.id, content_enc="ciphertext", base_snapshot_id=None))
        await session.flush()
        session.add(ConfigDraft(config_file_id=file.id, content_enc="other-ciphertext", base_snapshot_id=None))
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_locator_keys_fit_mysql_utf8mb4_unique_index_and_are_stable(db_factory):
    assert ConfigFile.__table__.c.locator_key.type.length <= 64
    assert ConfigTaskItem.__table__.c.item_key.type.length <= 64
    async with db_factory() as session:
        instance = ConfigPlatformInstance(name="stable", provider="nacos", base_url="https://nacos.example", credential_id=1)
        session.add(instance)
        await session.flush()
        first, first_key = await normalize_locator(session, instance, {
            "namespace": "prod", "group": "DEFAULT_GROUP", "data_id": "payment.yaml",
        })
        second, second_key = await normalize_locator(session, instance, {
            "data_id": "payment.yaml", "group": "DEFAULT_GROUP", "namespace": "prod",
        })
        assert first == second
        assert first_key == second_key
        assert len(first_key) == 64
