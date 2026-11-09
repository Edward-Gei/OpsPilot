"""前端发送UTC ISO时间，服务端必须转换到既有本地naive时间再筛选。"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.database_management import DatabaseAuditEvent, DatabaseTask
from tests.conftest import auth_header, login_for_tokens
from test_database_core import target, submit
from app.services.database_audit_service import append_audit_event, list_audit


async def test_history_and_audit_accept_utc_iso_time_bounds(client, db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    local_stamp = datetime.now()
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        task.created_at = local_stamp
        for event in (await session.execute(select(DatabaseAuditEvent).where(DatabaseAuditEvent.task_id == task.id))).scalars():
            event.created_at = local_stamp
        await session.commit()
    headers = auth_header(await login_for_tokens(client, 'admin'))
    utc_stamp = local_stamp.astimezone(timezone.utc)
    for base in ('tasks','audit/logs'):
        response = await client.get('/api/v1/database/'+base, headers=headers, params={
            'started_from':(utc_stamp-timedelta(minutes=1)).isoformat(),
            'started_to':(utc_stamp+timedelta(minutes=1)).isoformat(),
        })
        assert response.status_code == 200
        assert [item['task_id'] for item in response.json()['data']['items']] == [submission.task_id]


async def test_audit_time_filter_uses_submission_instead_of_last_event(db_factory, seed):
    instance_id, _ = await target(db_factory, seed)
    submission = await submit(db_factory, seed, instance_id)
    started = datetime.now()-timedelta(minutes=2)
    async with db_factory() as session:
        task = await session.get(DatabaseTask, submission.task_id)
        first = (await session.execute(select(DatabaseAuditEvent).where(
            DatabaseAuditEvent.task_id == task.id))).scalar_one()
        first.created_at = started
        task.status, task.finished_at = 'succeeded', started+timedelta(minutes=1)
        last = await append_audit_event(session, task, 'succeeded', terminal=True)
        last.created_at = task.finished_at
        await session.commit()
    async with db_factory() as session:
        page = await list_audit(session, started_from=started-timedelta(seconds=1),
                                started_to=started+timedelta(seconds=30))
        assert [item['task_id'] for item in page['items']] == [submission.task_id]
        assert page['items'][0]['status'] == 'succeeded'
        assert page['items'][0]['created_at'] == started
