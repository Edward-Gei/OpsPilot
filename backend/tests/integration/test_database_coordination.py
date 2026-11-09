"""真实 MySQL 双独立连接池的认领、额度与租约 fencing。"""
import asyncio
from datetime import datetime, timedelta
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pymysql
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.security import encrypt_text
from app.database_adapters.base import StopConfirmation
from app.models.auth import Permission, Role, RolePermission, User, UserRole
from app.models.database_management import DatabaseInstance, DatabaseRoleInstance, DatabaseTask
from app.models.job import Credential
from app.models.cmdb import Application
from app.models.database_management import DatabaseInstanceApplication
from app.schemas.database_management import ActorContext, SqlTaskRequest
from app.services.database_task_service import submit_task
from app.services.database_quota_service import bucket, claim_next_task
from app.services.database_execution_service import _settle
from app.services.database_maintenance_service import run_maintenance_once
from app.core.response import BizError
from app.schemas.database_management import InstanceWrite
from app.services.credential_service import delete_credential
from app.services.database_instance_service import write_instance, replace_application_instances

pytestmark = pytest.mark.integration


@pytest.fixture
async def real_coordination():
    if not os.environ.get("DATABASE_TEST_INTERNAL_URL"):
        pytest.skip("requires isolated real MySQL fixture")
    from sqlalchemy.engine import make_url
    url = make_url(os.environ["DATABASE_TEST_INTERNAL_URL"])
    database = "db_coord_"+uuid.uuid4().hex[:16]
    root_password = os.environ.get("DATABASE_TEST_ROOT_PASSWORD", "local-fixture-root")
    def admin():
        return pymysql.connect(host=url.host, port=url.port or 3306, user="root", password=root_password, autocommit=True)
    with admin() as conn:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
    engines = []
    try:
        environment = {**os.environ, "MYSQL_USER": "root", "MYSQL_PASSWORD": root_password, "MYSQL_DB": database}
        outcome = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd="/app", env=environment,
                                 capture_output=True, text=True, timeout=120)
        assert outcome.returncode == 0, outcome.stderr[-5000:]
        root_url = url.set(username="root", password=root_password, database=database)
        for _ in range(3):
            engines.append(create_async_engine(root_url, pool_size=4, max_overflow=0, pool_pre_ping=True))
        yield [async_sessionmaker(engine, expire_on_commit=False) for engine in engines]
    finally:
        for engine in engines:
            await engine.dispose()
        with admin() as conn:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")


async def test_two_replicas_atomic_claim_and_unknown_recovery(real_coordination):
    api, first, second = real_coordination
    async with api() as session:
        permission = Permission(code="database:query", name="query", module="database")
        role = Role(code="db-read", name="db-read", is_builtin=False)
        session.add_all([permission, role])
        await session.flush()
        session.add(RolePermission(role_id=role.id, permission_id=permission.id))
        credential = Credential(name="synthetic", auth_type="username_password", login_user="readonly",
                                secret_enc=encrypt_text("synthetic"))
        session.add(credential)
        await session.flush()
        instances = []
        for i in range(6):
            row = DatabaseInstance(name=f"db-{i}", engine="mysql", environment="demo", host="target-mysql",
                port=3306, credential_id=credential.id, connection_options={})
            session.add(row)
            await session.flush()
            session.add(DatabaseRoleInstance(role_id=role.id, instance_id=row.id))
            instances.append(row.id)
        actors = []
        for i in range(100):
            user = User(username=f"load-{i}", display_name=f"load-{i}", source="local", status="active", must_change_password=False)
            session.add(user)
            await session.flush()
            session.add(UserRole(user_id=user.id, role_id=role.id))
            actors.append(ActorContext(user.id, user.username))
        await session.commit()
    semaphore = asyncio.Semaphore(12)
    async def submit(index):
        async with semaphore, api() as session:
            # 前半集中热点，后半分布冷实例，100个独立身份。
            instance_id = instances[0] if index < 50 else instances[1+(index % 5)]
            return await submit_task(session, actors[index], SqlTaskRequest(kind="sql", instance_id=instance_id, sql="SELECT 1"))
    submissions = await asyncio.gather(*(submit(index) for index in range(100)), return_exceptions=True)
    assert not [item for item in submissions if isinstance(item, BaseException)]
    assert len({item.task_id for item in submissions}) == 100
    async def claim_all(factory, name):
        claims = []
        for _ in range(20):
            claim = await claim_next_task(factory, name)
            if claim:
                claims.append(claim)
            else:
                await asyncio.sleep(0)
        return claims
    groups = await asyncio.gather(claim_all(first, "replica-a"), claim_all(second, "replica-b"), return_exceptions=True)
    assert not [group for group in groups if isinstance(group, BaseException)]
    claims = [claim for group in groups for claim in group]
    assert len(claims) == 18
    assert len({claim.task_id for claim in claims}) == 18
    async with api() as session:
        global_row = await bucket(session, "global", "all")
        assert global_row.active_count == global_row.data_active_count == 18
        assert global_row.queued_count == 82 and global_row.target_sockets == 18
        assert global_row.reserved_bytes == 18*(2*5242880+65536)
        for instance_id in instances:
            assert (await bucket(session, "instance", instance_id)).data_active_count <= 4
        assert all([(await bucket(session, "executor", executor)).active_count <= 9 for executor in ("replica-a", "replica-b")])
        cold_running = (await session.execute(select(DatabaseTask).where(DatabaseTask.status == "running",
            DatabaseTask.instance_id != instances[0]))).scalars().all()
        assert len(cold_running) >= 14
        # 模拟旧副本在发出目标访问后失联；缺少可核实目标身份必须保留占用。
        task = await session.get(DatabaseTask, claims[0].task_id)
        task.target_attempted_at = datetime.now()-timedelta(seconds=31)
        task.lease_expires_at = datetime.now()-timedelta(seconds=1)
        task.prepared_operation_enc = encrypt_text('{"connection_options":{}}')
        await session.commit()
    await run_maintenance_once(second, "recovery-owner")
    async with api() as session:
        recovered = await session.get(DatabaseTask, claims[0].task_id)
        assert recovered.status == "recovering" and recovered.lease_token != claims[0].lease_token
        assert not recovered.quota_released
        assert (await bucket(session, "global", "all")).active_count == 18
    assert not await _settle(first, claims[0], "failed", StopConfirmation(True, "late_old_worker", {}))
    # 其他17条操作有可信完成证据后才分别释放，恢复任务仍占一个额度。
    for claim in claims[1:]:
        assert await _settle(first, claim, "failed", StopConfirmation(True, "synthetic_confirmed", {}))
    async with api() as session:
        assert (await bucket(session, "global", "all")).active_count == 1
        assert (await bucket(session, "global", "all")).target_sockets == 1


async def test_reference_check_uses_current_read_after_waiting_for_mutex(real_coordination):
    """认证已开启 RR 快照时，等待短锁后仍须看见刚提交的凭据引用。"""
    api, first, second = real_coordination
    async with api() as session:
        permission = Permission(code="database:write", name="write", module="database")
        role = Role(code="reference-race", name="reference-race", is_builtin=False)
        user = User(username="reference-actor", display_name="reference-actor", source="local",
                    status="active", must_change_password=False)
        credential = Credential(name="reference-credential", auth_type="username_password",
                                login_user="readonly", secret_enc=encrypt_text("synthetic"))
        session.add_all([permission, role, user, credential])
        await session.flush()
        session.add_all([RolePermission(role_id=role.id, permission_id=permission.id),
                         UserRole(user_id=user.id, role_id=role.id)])
        user_id, credential_id = user.id, credential.id
        await session.commit()
    async with first() as writer, second() as deleter:
        assert (await deleter.execute(text("SELECT @@transaction_isolation"))).scalar_one() == "REPEATABLE-READ"
        # 与 API 鉴权一致：在 global mutex 之前建立一致性读视图。
        await deleter.execute(select(User).where(User.id == user_id))
        instance = await write_instance(writer, user_id, InstanceWrite(name="reference-instance", engine="mysql",
            host="synthetic.invalid", port=3306, credential_id=credential_id))
        instance_id = instance.id
        async def remove():
            with pytest.raises(BizError, match="数据库实例"):
                await delete_credential(deleter, credential_id)
            await deleter.rollback()
        pending = asyncio.create_task(remove())
        await asyncio.sleep(0.15)
        assert not pending.done(), "delete must wait for the global mutex"
        await writer.commit()
        await pending
    async with api() as session:
        assert await session.get(Credential, credential_id) is not None
        assert (await session.get(DatabaseInstance, instance_id)).credential_id == credential_id


async def test_deleted_parent_cannot_gain_new_database_links_from_rr_snapshot(real_coordination):
    """关联编辑持有的旧应用对象不能在应用删除后产生孤立关联。"""
    api, first, second = real_coordination
    async with api() as session:
        app = Application(name="removed-parent", deploy_type="docker", project_type="backend", language="Python")
        credential = Credential(name="parent-reference", auth_type="username_password",
                                login_user="readonly", secret_enc=encrypt_text("synthetic"))
        session.add_all([app, credential])
        await session.flush()
        instance = DatabaseInstance(name="parent-target", engine="mysql", host="synthetic.invalid",
            environment="demo", port=3306, credential_id=credential.id, connection_options={})
        session.add(instance)
        await session.flush()
        app_id, instance_id = app.id, instance.id
        await session.commit()
    async with first() as updater, second() as remover:
        await updater.execute(select(Application).where(Application.id == app_id))
        await bucket(remover, "global", "all")
        await remover.delete(await remover.get(Application, app_id))
        await remover.commit()
        with pytest.raises(BizError, match="应用不存在"):
            await replace_application_instances(updater, app_id, [instance_id])
        await updater.rollback()
    async with api() as session:
        assert (await session.execute(select(DatabaseInstanceApplication).where(
            DatabaseInstanceApplication.app_id == app_id))).scalar_one_or_none() is None
