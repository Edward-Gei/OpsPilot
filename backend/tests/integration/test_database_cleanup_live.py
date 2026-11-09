"""真实平台 MySQL 验证关闭等待期间不持事务、不释放物理占用。"""
import asyncio

import pytest
from sqlalchemy import select

from app.models.database_management import DatabaseResult, DatabaseTask
from app.schemas.database_management import SqlTaskRequest
from app.services import database_execution_service as execution
from app.services.database_quota_service import bucket, claim_next_task
from app.services.database_task_service import submit_task
from test_database_cleanup import ClosingAdapter
from test_database_coordination import real_coordination
from test_database_executor_live import prepare

pytestmark = pytest.mark.integration


async def test_real_platform_pool_remains_free_and_accounted_while_close_waits(real_coordination, monkeypatch):
    api, executor, observer = real_coordination
    actor, instance_id, _ = await prepare(api, "mysql")
    async with api() as session:
        submission = await submit_task(session, actor, SqlTaskRequest(kind="sql", instance_id=instance_id, sql="SELECT 1"))
    adapter = ClosingAdapter()
    monkeypatch.setattr("app.database_adapters.get_adapter", lambda _: adapter)
    monkeypatch.setattr(execution, "get_adapter", lambda _: adapter)
    claim = await claim_next_task(executor, "blocking-close-executor")
    running = asyncio.create_task(execution.run_claimed_task(executor, claim))
    try:
        await asyncio.wait_for(adapter.close_entered.wait(), 5)
        async with observer() as session:
            async with session.begin():
                quota = await asyncio.wait_for(bucket(session, "global", "all"), 2)
                task = await session.get(DatabaseTask, submission.task_id)
                assert adapter.is_open and not task.quota_released
                assert quota.active_count == quota.target_sockets == 1
                assert (await session.execute(select(DatabaseResult).where(DatabaseResult.task_id == task.id))).scalar_one_or_none() is None
    finally:
        adapter.allow_close.set()
        await asyncio.wait_for(running, 5)
    async with observer() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        assert task.status == "succeeded" and task.quota_released
        assert (await bucket(session, "global", "all")).target_sockets == 0
