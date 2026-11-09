"""数据库策略配置必须严格校验，整批写入不能部分生效。"""
import pytest

from app.core.response import BizError
from app.services import config_service
from tests.conftest import auth_header, login_for_tokens


@pytest.mark.parametrize("value", [True, 1.5, "60", 0, 601])
async def test_database_timeout_requires_bounded_integer(db_factory, value):
    async with db_factory() as session:
        with pytest.raises(BizError):
            await config_service.set_config(session, "database.execution_timeout_seconds", value, None)


async def test_database_policy_defaults_are_independent(db_factory):
    async with db_factory() as session:
        configs = await config_service.get_all_configs(session)
        assert configs["database.execution_timeout_seconds"] == 60
        assert configs["database.history_retention_days"] == 30
        assert configs["database.audit_retention_days"] == 90


async def test_database_config_batch_validates_merged_constraints(client):
    headers = auth_header(await login_for_tokens(client, "admin"))
    response = await client.put("/api/v1/system/configs", headers=headers, json={"configs": {
        "database.global_concurrency": 4,
        "database.metadata_reserved_concurrency": 4,
        "database.execution_timeout_seconds": 35,
    }})
    assert response.status_code == 400
    current = (await client.get("/api/v1/system/configs", headers=headers)).json()["data"]
    assert current["database.global_concurrency"] == 20
    assert current["database.execution_timeout_seconds"] == 60
    response = await client.put("/api/v1/system/configs", headers=headers, json={"configs": {
        "database.global_concurrency": 4,
        "database.metadata_reserved_concurrency": 3,
        "database.history_retention_days": 20,
        "database.audit_retention_days": 10,
    }})
    assert response.json()["code"] == 0
    current = (await client.get("/api/v1/system/configs", headers=headers)).json()["data"]
    assert current["database.global_concurrency"] == 4
    assert current["database.metadata_reserved_concurrency"] == 3
    assert current["database.history_retention_days"] == 20
    assert current["database.audit_retention_days"] == 10
