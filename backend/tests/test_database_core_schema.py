"""数据库入口的严格边界：未知选项和关系输入不得静默忽略。"""
import pytest
from pydantic import ValidationError, TypeAdapter

from app.schemas.database_management import InstanceWrite, DatabaseTaskRequest


def instance(**changes):
    return dict(name="demo", engine="mysql", environment="demo", enabled=True,
                host="db", port=3306, credential_id=1, **changes)


@pytest.mark.parametrize("changes", [
    {"system_name": "removed"}, {"app_ids": None}, {"app_ids": [1, 1]},
    {"host_ids": [True]}, {"connection_options": {"auth_database": "admin"}},
    {"connection_options": {"arbitrary_driver_option": True}},
])
def test_instance_rejects_removed_fields_and_unsafe_options(changes):
    with pytest.raises(ValidationError):
        InstanceWrite.model_validate(instance(**changes))


def test_association_omission_is_distinct_from_clear():
    assert "app_ids" not in InstanceWrite.model_validate(instance()).model_fields_set
    assert InstanceWrite.model_validate(instance(app_ids=[])).app_ids == []
    assert InstanceWrite.model_validate(instance(cluster_name="  ")).cluster_name is None


@pytest.mark.parametrize("payload", [
    {"kind": "probe", "instance_id": 1, "sql": "SELECT 1"},
    {"kind": "redis_read", "instance_id": 1, "context": {"database": 0},
     "operation": {"type": "get", "key": "secret"}},
    {"kind": "mongo_read", "instance_id": 1,
     "context": {"database": "demo", "collection": "orders"},
     "operation": {"type": "find", "filter": ["invalid document type"]}},
])
def test_task_rejects_arbitrary_commands_scripts_and_extra_fields(payload):
    with pytest.raises(ValidationError):
        TypeAdapter(DatabaseTaskRequest).validate_python(payload)
