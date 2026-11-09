"""真实 MySQL 验证全新升级与已有 0026 台账的无损可空迁移。"""
from datetime import datetime
from io import BytesIO
import os
import subprocess
import sys

import pytest
from openpyxl import load_workbook
from sqlalchemy import text, select

from test_database_coordination import real_coordination
from app.models.database_management import DatabaseInstance, DatabaseTask
from app.models.auth import User, UserRole, Role
from app.services import seed as seed_service
from app.services import database_excel
from app.services.database_task_service import submit_task
from app.schemas.database_management import ActorContext, ProbeTaskRequest
from app.models.database_management import DatabaseAuditEvent
from test_database_excel import workbook

pytestmark = pytest.mark.integration


def migrate(factory, direction, revision):
    url = factory.kw["bind"].url
    environment = {**os.environ, "MYSQL_HOST": url.host, "MYSQL_USER": url.username,
        "MYSQL_PASSWORD": url.password, "MYSQL_DB": url.database}
    result = subprocess.run([sys.executable, "-m", "alembic", direction, revision], cwd="/app",
        env=environment, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-5000:]


async def credential_nullability(session):
    rows = (await session.execute(text("""SELECT TABLE_NAME, IS_NULLABLE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN ('database_instance','database_task')
        AND COLUMN_NAME='credential_id'"""))).all()
    return {name: nullable for name, nullable in rows}


async def test_fresh_head_and_existing_0026_both_allow_drafts_without_losing_rows(real_coordination):
    factory = real_coordination[0]
    async with factory() as session:
        assert await credential_nullability(session) == {"database_instance": "YES", "database_task": "YES"}
        assert (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one() == "0027"
    migrate(factory, "downgrade", "0026")
    async with factory() as session:
        assert await credential_nullability(session) == {"database_instance": "NO", "database_task": "NO"}
        instance = DatabaseInstance(name="legacy", engine="mysql", environment="demo", host="mysql", port=3306,
            credential_id=321, connection_options={"default_database": "legacy"})
        session.add(instance)
        await session.flush()
        task = DatabaseTask(owner_id=1, actor_name="migration", instance_id=instance.id, kind="probe",
            status="rejected", engine="mysql", config_revision=1, credential_id=321, credential_updated_at=datetime.now())
        session.add(task)
        await session.commit()
        instance_id, task_id = instance.id, task.id
    migrate(factory, "upgrade", "head")
    async with factory() as session:
        assert await credential_nullability(session) == {"database_instance": "YES", "database_task": "YES"}
        assert (await session.get(DatabaseInstance, instance_id)).credential_id == 321
        assert (await session.get(DatabaseTask, task_id)).credential_id == 321
        draft = DatabaseInstance(name="draft", engine="postgresql", environment="demo", host="postgres", port=5432,
            credential_id=None, enabled=False, connection_options={})
        session.add(draft)
        await session.flush()
        session.add(DatabaseTask(owner_id=1, actor_name="migration", instance_id=draft.id, kind="probe",
            status="rejected", engine="postgresql", config_revision=1, credential_id=None, credential_updated_at=datetime.now()))
        await session.commit()
        assert len(list((await session.execute(select(DatabaseInstance))).scalars())) == 2
    url = factory.kw["bind"].url
    result = subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0026"], cwd="/app",
        env={**os.environ, "MYSQL_USER": url.username, "MYSQL_PASSWORD": url.password, "MYSQL_DB": url.database},
        capture_output=True, text=True, timeout=120)
    assert result.returncode != 0 and "不能回退凭据必填约束" in result.stderr
    async with factory() as session:
        assert await credential_nullability(session) == {"database_instance": "YES", "database_task": "YES"}


async def test_real_mysql_excel_partial_commit_and_draft_rejection(real_coordination):
    factory = real_coordination[0]
    async with factory() as session:
        await seed_service._sync_permissions(session)
        await seed_service._sync_builtin_roles(session)
        role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        user = User(username="excel-admin", display_name="excel-admin", password_hash="not-used",
            source="local", status="active", must_change_password=False)
        session.add(user)
        await session.flush()
        session.add(UserRole(user_id=user.id, role_id=role.id))
        await session.commit()
        user_id = user.id
    async with factory() as session:
        result = await database_excel.import_instances(session, user_id, workbook(
            ["first", "mysql", "target", 3306, "demo"],
            ["FIRST", "mysql", "duplicate", 3306, "demo"],
            ["=SUM(A1)", "postgresql", "postgres", 5432, "stage"],
            ["last", "redis", "redis", 6379, "prod"]))
        # MySQL 名称排序规则不区分大小写，第二行在独立事务失败，后行继续提交。
        assert result == {"success_count": 2, "created_count": 2, "updated_count": 0,
            "failed_rows": [{"row": 3, "reason": "实例名称已存在，可选择存在即更新"},
                            {"row": 4, "reason": "不支持公式或错误单元格，请填写文本或数值"}]}
    async with factory() as session:
        rows = list((await session.execute(select(DatabaseInstance).order_by(DatabaseInstance.id))).scalars())
        assert [row.name for row in rows] == ["first", "last"]
        assert all(row.credential_id is None and not row.enabled for row in rows)
        result = await database_excel.import_instances(session, user_id,
            workbook(["first", "mysql", "updated", 3307, "prod"]), upsert=True)
        assert result["updated_count"] == 1
    async with factory() as session:
        payload, count = await database_excel.export_instances(session, user_id, engine="mysql", sort_by="name")
        book = load_workbook(BytesIO(payload))
        assert count == 1
        assert list(book.active.values)[1][:5] == ("first", "mysql", "updated", 3307, "prod")
        book.close()
        draft = (await session.execute(select(DatabaseInstance).where(DatabaseInstance.name == "first"))).scalar_one()
        assert draft.config_revision == 2
        submission = await submit_task(session, ActorContext(user_id, "excel-admin"),
            ProbeTaskRequest(kind="probe", instance_id=draft.id))
        assert submission.status == "rejected"
    async with factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        assert task.credential_id is None and task.target_attempted_at is None
        audit = (await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == task.id))).scalar_one()
        assert audit.event_type == "rejected" and audit.validation["executed"] is False
