"""真实 MySQL 对执行历史格式化搜索执行与 SQLite 相同的展示向量。"""
import pytest

from app.models.auth import Permission, Role, RolePermission, User, UserRole
from app.schemas.database_management import ActorContext
from app.services import database_task_service as tasks
from test_database_coordination import real_coordination
from test_database_history_search import FrozenDateTime, seed_search_records, verify_search_vectors

pytestmark = pytest.mark.integration


async def test_real_mysql_history_keyword_display_and_visibility(real_coordination, monkeypatch):
    factory = real_coordination[0]
    monkeypatch.setattr(tasks, "datetime", FrozenDateTime)
    async with factory() as session:
        permissions = [Permission(code=code, name=code, module="database") for code in ("database:query", "database:write")]
        role = Role(code="history-search", name="history-search", is_builtin=False)
        owner = User(username="history-owner", display_name="history-owner", source="local", status="active", must_change_password=False)
        other = User(username="history-other", display_name="history-other", source="local", status="active", must_change_password=False)
        session.add_all([*permissions, role, owner, other])
        await session.flush()
        session.add_all([RolePermission(role_id=role.id, permission_id=permission.id) for permission in permissions])
        session.add(UserRole(user_id=owner.id, role_id=role.id))
        await session.commit()
        actor, other_id = ActorContext(owner.id, owner.username), other.id
    ids = await seed_search_records(factory, actor.user_id, other_id)
    await verify_search_vectors(factory, actor, ids)
