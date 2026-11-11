"""共享数据库配额：用平台 MySQL 的短行锁协调所有执行副本。"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid

from sqlalchemy import and_, cast, func, or_, select, String
from sqlalchemy.orm import aliased
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.database_management import DatabaseInstance, DatabaseQuotaBucket, DatabaseTask
from app.services import config_service
from app.services.database_audit_service import append_audit_event

TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out", "rejected", "queue_expired")
DATA_KINDS = ("sql", "mongo_read", "redis_read")
PROBE_TIMEOUT_SECONDS = 5
POLICY_DEFAULTS = {
    "execution_timeout_seconds": 60, "max_result_rows": 1000, "max_result_bytes": 5242880,
    "result_ttl_minutes": 30, "global_concurrency": 20, "instance_concurrency_default": 5,
    "user_concurrency": 2, "queue_max_tasks": 200, "user_queue_max_tasks": 10,
    "queue_timeout_seconds": 120, "metadata_reserved_concurrency": 2,
    "result_storage_budget_bytes": 1073741824,
}


@dataclass(frozen=True)
class ClaimedTask:
    """跨会话只携不可变身份，禁止传 ORM 或明文凭据。"""
    task_id: int
    lease_token: str
    executor_id: str
    deadline_at: datetime
    policy_snapshot: dict
    quota_snapshot: dict


async def policy(session):
    configs = await config_service.get_all_configs(session)
    return {key: configs.get("database." + key, value) for key, value in POLICY_DEFAULTS.items()}


async def bucket(session, scope, key):
    """先幂等建计量行再锁定，首副本竞争也不能创建重复桶。"""
    key = str(key)
    query = select(DatabaseQuotaBucket).where(DatabaseQuotaBucket.scope == scope,
        DatabaseQuotaBucket.scope_key == key).with_for_update().execution_options(populate_existing=True)
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing is not None:
        return existing
    values = dict(scope=scope, scope_key=key)
    dialect = session.get_bind().dialect.name
    if dialect == "mysql":
        statement = mysql_insert(DatabaseQuotaBucket).values(**values).on_duplicate_key_update(scope_key=key)
    elif dialect == "sqlite":
        statement = sqlite_insert(DatabaseQuotaBucket).values(**values).on_conflict_do_nothing(index_elements=["scope", "scope_key"])
    else:
        raise RuntimeError("数据库任务共享计量仅支持 MySQL / SQLite 测试")
    await session.execute(statement)
    return (await session.execute(query)).scalar_one()


async def lock_buckets(session, task, executor_id=None):
    """所有活动计量遵守 global→instance→user→executor 顺序。"""
    rows = [await bucket(session, "global", "all"), await bucket(session, "instance", task.instance_id),
            await bucket(session, "user", task.owner_id)]
    if executor_id:
        rows.append(await bucket(session, "executor", executor_id))
    return rows


async def finish_task(session, task, status, *, error_code=None, error_message=None, result=None):
    """持有 global/任务锁调用；终态、审计和释放在同一事务且仅一次。"""
    if task.status in TERMINAL_STATES or task.quota_released:
        return False
    original = task.status
    rows = await lock_buckets(session, task, (task.quota_snapshot or {}).get("executor_id"))
    if original == "queued":
        for row in rows[:3]:
            row.queued_count = max(0, row.queued_count - 1)
    else:
        data = task.kind in DATA_KINDS
        sockets = (task.quota_snapshot or {}).get("target_sockets", 0)
        for row in rows:
            row.active_count = max(0, row.active_count - 1)
            row.data_active_count = max(0, row.data_active_count - int(data))
            row.target_sockets = max(0, row.target_sockets - sockets)
        rows[0].reserved_bytes = max(0, rows[0].reserved_bytes - task.result_reserved_bytes)
        if result:
            rows[0].payload_bytes += result.stored_bytes
    task.status = status
    task.error_code, task.error_message = error_code, error_message
    task.finished_at = datetime.now()
    task.lease_expires_at = None
    task.quota_released = True
    if task.kind == "probe":
        instance = (await session.execute(select(DatabaseInstance).where(DatabaseInstance.id == task.instance_id).with_for_update())).scalar_one_or_none()
        if instance and instance.config_revision == task.config_revision:
            instance.last_probe_at = task.finished_at
            instance.last_probe_status = "succeeded" if status == "succeeded" else "failed"
            instance.last_probe_message = "连接检测成功" if status == "succeeded" else error_message or "连接检测未完成"
    await append_audit_event(session, task, "finished", terminal=True)
    await session.flush()
    return True


async def claim_next_task(factory, executor_id):
    """有界扫描避开热点实例；一次事务内认领并预留全部共享资源。"""
    from app.database_adapters import get_adapter
    async with factory() as session:
        async with session.begin():
            limits = await policy(session)
            global_row = await bucket(session, "global", "all")
            local_snapshot = (await session.execute(select(DatabaseQuotaBucket).where(
                DatabaseQuotaBucket.scope == "executor", DatabaseQuotaBucket.scope_key == executor_id))).scalar_one_or_none()
            local_active = local_snapshot.active_count if local_snapshot else 0
            local_data = local_snapshot.data_active_count if local_snapshot else 0
            if global_row.active_count >= limits["global_concurrency"] or local_active >= 10:
                return None
            iq, uq = aliased(DatabaseQuotaBucket), aliased(DatabaseQuotaBucket)
            capacity = func.coalesce(DatabaseInstance.concurrency_limit, limits["instance_concurrency_default"])
            data_allowed = (global_row.data_active_count < limits["global_concurrency"]-limits["metadata_reserved_concurrency"]
                            and local_data < 9)
            # 在 SQL 层排除满额实例/用户，冷实例不受队首任意数量热点任务阻塞。
            query = (select(DatabaseTask).join(DatabaseInstance, DatabaseInstance.id == DatabaseTask.instance_id)
                .outerjoin(iq, and_(iq.scope == "instance", iq.scope_key == cast(DatabaseTask.instance_id, String)))
                .outerjoin(uq, and_(uq.scope == "user", uq.scope_key == cast(DatabaseTask.owner_id, String)))
                .where(DatabaseTask.status == "queued",
                    func.coalesce(iq.active_count, 0) < capacity,
                    func.coalesce(uq.active_count, 0) < limits["user_concurrency"],
                    or_(DatabaseTask.kind.not_in(DATA_KINDS), and_(data_allowed,
                        func.coalesce(iq.data_active_count, 0) < capacity-1)))
                .order_by(DatabaseTask.id).limit(20).with_for_update(skip_locked=True))
            tasks = list((await session.execute(query)).scalars())
            for task in tasks:
                if task.queue_deadline_at <= datetime.now():
                    await finish_task(session, task, "queue_expired", error_code="queue_expired", error_message="排队等待已过期")
                    continue
                inst = (await session.execute(select(DatabaseInstance).where(DatabaseInstance.id == task.instance_id).with_for_update())).scalar_one_or_none()
                if inst is None or not inst.enabled:
                    await finish_task(session, task, "rejected", error_code="instance_disabled", error_message="实例不存在或已停用")
                    continue
                instance_row = await bucket(session, "instance", task.instance_id)
                user_row = await bucket(session, "user", task.owner_id)
                local_row = await bucket(session, "executor", executor_id)
                rows = [global_row, instance_row, user_row, local_row]
                instance_limit = inst.concurrency_limit or limits["instance_concurrency_default"]
                capacities = [limits["global_concurrency"], instance_limit, limits["user_concurrency"], 10]
                is_data = task.kind in DATA_KINDS
                reserves = [limits["metadata_reserved_concurrency"], 1, 0, 1]
                if any(r.active_count >= cap or (is_data and r.data_active_count >= cap - reserved)
                       for r, cap, reserved in zip(rows, capacities, reserves)):
                    continue
                reserved_bytes = 2*limits["max_result_bytes"] + 65536
                if global_row.reserved_bytes + global_row.payload_bytes + reserved_bytes > limits["result_storage_budget_bytes"]:
                    continue
                adapter = get_adapter(task.engine)
                sockets = adapter.target_connection_cost
                if sockets > 3 or sockets < 1:
                    await finish_task(session, task, "rejected", error_code="unsupported_connection_cost", error_message="连接成本未通过验证")
                    continue
                for row in rows:
                    row.active_count += 1
                    row.data_active_count += int(is_data)
                    row.target_sockets += sockets
                for row in rows[:3]:
                    row.queued_count = max(0, row.queued_count - 1)
                global_row.reserved_bytes += reserved_bytes
                now, token = datetime.now(), uuid.uuid4().hex
                task.status, task.executor_id, task.lease_token = "running", executor_id, token
                task.started_at, task.heartbeat_at = now, now
                # 连接检测固定使用短预算，查询仍沿用管理员配置的执行上限。
                task_limits = {**limits, "execution_timeout_seconds": PROBE_TIMEOUT_SECONDS} if task.kind == "probe" else limits
                task.deadline_at = now + timedelta(seconds=task_limits["execution_timeout_seconds"])
                task.lease_expires_at = now + timedelta(seconds=30)
                task.policy_snapshot = task_limits
                task.result_reserved_bytes = reserved_bytes
                task.quota_snapshot = {"executor_id": executor_id, "target_sockets": sockets}
                await append_audit_event(session, task, "started")
                await session.flush()
                return ClaimedTask(task.id, token, executor_id, task.deadline_at, task_limits, task.quota_snapshot)
    return None


async def acquire_control(factory, instance_id, cost, *, task_id=None, owner=None):
    """取消/核实连接单独计量，不能借用数据任务槽。"""
    async with factory() as session:
        async with session.begin():
            global_row = await bucket(session, "global", "all")
            instance_row = await bucket(session, "instance", instance_id)
            record = await bucket(session, "control", task_id if task_id is not None else f"instance-{instance_id}")
            if record.active_count:
                return None
            if global_row.control_operations >= 2 or instance_row.control_operations >= 1:
                return None
            for row in (global_row, instance_row):
                row.control_operations += 1
                row.control_sockets += cost
            token = uuid.uuid4().hex
            record.token, record.owner = token, owner or "controlling"
            record.active_count, record.control_sockets = 1, cost
            # scope_key 指向任务，目标身份保存在任务backend_ref；崩溃时不丢证据。
            record.scope_key = str(task_id) if task_id is not None else f"instance-{instance_id}"
            record.heartbeat_at = datetime.now()
            record.lease_expires_at = datetime.now()+timedelta(seconds=15)
            return token


async def release_control(factory, instance_id, cost, *, task_id=None, token=None):
    async with factory() as session:
        async with session.begin():
            rows = (await bucket(session, "global", "all"), await bucket(session, "instance", instance_id))
            record = await bucket(session, "control", task_id if task_id is not None else f"instance-{instance_id}")
            if record.token != token or not record.active_count:
                return False
            for row in rows:
                row.control_operations = max(0, row.control_operations - 1)
                row.control_sockets = max(0, row.control_sockets - cost)
            record.active_count, record.control_sockets = 0, 0
            record.owner, record.lease_expires_at = None, None
            return True
