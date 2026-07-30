"""Worker 崩溃恢复：启动时执行的三分支扫描（02-技术架构 §4.2，已确认决策三）。

分支①  Stream pending 未 ACK（queued 阶段崩溃）：
        delivery_count ≤ 阈值 → XCLAIM 重认领正常重试（尚未跑过命令，重跑安全）；
        超阈值 → XACK 丢弃，execution/工单置 interrupted(worker_lost)。
分支②  DB 中 running 的 execution（已 ACK 后崩溃）：
        置 interrupted(system_crash)，主机现场保留（不改主机行，供排查）。
分支③  DB 中 paused 的 execution（调度上下文丢失，V1 不承诺断点续跑）：
        同样置 interrupted(system_crash)。

每次恢复写系统事件审计 worker_crash_recovered 并通知提交人 + admin 角色成员
（走通知集成 + 系统事件日志，V1 不引入告警中心）。

V1 为单 worker 容器：恢复仅在启动时执行一次，无需运行期互抢（多实例扩展时
需改为周期性 XAUTOCLAIM + 实例互斥，见 02 §4.2 备注）。
"""
import logging
from datetime import datetime

from sqlalchemy import select

from app import audit
from app.core import redis as redis_mod
from app.core.constants import ExecutionStatus, InterruptReason, NotifyEvent, TicketStatus
from app.core.database import async_session_factory
from app.models.auth import Role, User, UserRole
from app.models.execution import Execution
from app.models.ticket import Ticket
from app.services import notify_service

logger = logging.getLogger("opspilot.engine.recovery")

# 消息重试上限：delivery_count 超过即判定 worker_lost（已确认决策三，默认 3）
MAX_DELIVERY = 3


async def _admin_usernames(session) -> list[str]:
    """admin 角色全部成员用户名（崩溃通知的额外收件人）。"""
    rows = await session.execute(
        select(User.username)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(Role.code == "admin")
    )
    return list(rows.scalars())


async def _mark_interrupted(session, execution: Execution, reason: str, detail: str) -> None:
    """把一条执行及其工单归档为 interrupted(reason)，并审计 + 通知。"""
    execution.status = ExecutionStatus.INTERRUPTED.value
    execution.finished_at = datetime.now()
    ticket = await session.get(Ticket, execution.ticket_id)
    receivers: list[str] = await _admin_usernames(session)
    if ticket is not None:
        ticket.status = TicketStatus.INTERRUPTED.value
        ticket.interrupt_reason = reason
        ticket.finished_at = datetime.now()
        creator = await session.get(User, ticket.creator_id)
        if creator and creator.username not in receivers:
            receivers.insert(0, creator.username)
        # 崩溃/失联属系统性事件：通知提交人 + admin（决策三，不引入告警中心）
        await notify_service.emit(
            session, NotifyEvent.EXECUTION_INTERRUPTED,
            receiver=",".join(receivers) or None,
            title=f"工单 {ticket.ticket_no} 执行中断（{reason}）",
            content=f"{ticket.title}：{detail}",
            ref_type="ticket", ref_id=ticket.id,
            # 模板变量：直调 emit 不经 _emit_ticket_event，需自行补齐工单基础变量
            variables={
                "ticket_no": ticket.ticket_no,
                "ticket_title": ticket.title,
                "app_name": ticket.app_name_snap,
                "creator": creator.username if creator else str(ticket.creator_id),
                "reason": reason,
                "detail": detail,
            },
        )
    # 系统事件审计：崩溃恢复动作留痕
    audit.log(
        module="execution", action="worker_crash_recovered",
        target_type="execution", target_id=str(execution.id),
        target_name=f"ticket:{execution.ticket_id}",
        detail={"reason": reason, "detail": detail},
    )
    logger.warning("崩溃恢复：execution=%s 置 interrupted(%s)：%s", execution.id, reason, detail)


async def recover_db_executions(consumer_name: str) -> None:
    """分支②③：把 DB 中滞留 running / paused 的执行归档为 interrupted(system_crash)。

    启动时执行（此刻本 worker 尚未认领任何消息），凡 running/paused 都是
    上一进程崩溃留下的现场；主机行保留原状态供排查。
    """
    async with async_session_factory() as session:
        rows = list((await session.execute(
            select(Execution).where(Execution.status.in_(
                [ExecutionStatus.RUNNING.value, ExecutionStatus.PAUSED.value]
            ))
        )).scalars())
        for execution in rows:
            await _mark_interrupted(
                session, execution, InterruptReason.SYSTEM_CRASH.value,
                f"worker 重启（{consumer_name}）时发现执行滞留 {execution.status}，现场已丢失",
            )
        await session.commit()
    if rows:
        logger.warning("崩溃恢复：共归档 %d 条滞留执行", len(rows))


async def reclaim_pending_messages(consumer_name: str) -> list[tuple[str, dict]]:
    """分支①：接管 PEL 中的未 ACK 消息。

    返回可安全重试的 (msg_id, fields) 列表（由 worker 按新消息流程处理）；
    重试耗尽（delivery_count > MAX_DELIVERY）的消息 ACK 丢弃并置 worker_lost。
    """
    r = redis_mod.redis_client
    pending = await r.xpending_range(
        redis_mod.EXEC_QUEUE, redis_mod.EXEC_CONSUMER_GROUP, min="-", max="+", count=100,
    )
    if not pending:
        return []
    retry_ids, dead_ids = [], []
    for item in pending:
        # redis-py 返回 {message_id, consumer, time_since_delivered, times_delivered}
        if int(item["times_delivered"]) > MAX_DELIVERY:
            dead_ids.append(item["message_id"])
        else:
            retry_ids.append(item["message_id"])
    reclaimed: list[tuple[str, dict]] = []
    if retry_ids:
        # XCLAIM 到本消费者（min_idle_time=0：启动期无并发竞争）
        claimed = await r.xclaim(
            redis_mod.EXEC_QUEUE, redis_mod.EXEC_CONSUMER_GROUP, consumer_name,
            min_idle_time=0, message_ids=retry_ids,
        )
        reclaimed = [(msg_id, fields) for msg_id, fields in claimed]
        logger.warning("崩溃恢复：重认领 %d 条 pending 消息重试", len(reclaimed))
    if dead_ids:
        # 读取消息体定位 execution，再 ACK 丢弃
        claimed_dead = await r.xclaim(
            redis_mod.EXEC_QUEUE, redis_mod.EXEC_CONSUMER_GROUP, consumer_name,
            min_idle_time=0, message_ids=dead_ids,
        )
        async with async_session_factory() as session:
            for msg_id, fields in claimed_dead:
                await r.xack(redis_mod.EXEC_QUEUE, redis_mod.EXEC_CONSUMER_GROUP, msg_id)
                eid = int(fields.get("execution_id", 0) or 0)
                execution = await session.get(Execution, eid) if eid else None
                if execution and execution.status == ExecutionStatus.QUEUED.value:
                    await _mark_interrupted(
                        session, execution, InterruptReason.WORKER_LOST.value,
                        f"排队消息重试超过 {MAX_DELIVERY} 次仍未成功认领，已丢弃",
                    )
            await session.commit()
        logger.error("崩溃恢复：%d 条消息重试耗尽已丢弃（worker_lost）", len(dead_ids))
    return reclaimed
