"""通知分发器：队列消费 + 定时扫描兜底 + 指数退避重试（05-任务拆解 M6-2）。

可靠性模型（冲突决策 C3/C4）：
- notify_service.emit 落 pending 记录后 XADD ops:notify:queue；队列消息仅作
  "加速器"（尽快触达），XADD 与业务事务 commit 存在竞态，故以 30s 定时扫描
  pending 记录作为可靠兜底——消息丢失/早到均不影响最终送达。
- 失败退避 1m/5m/15m：保持 pending 并累加 retry_count + 排 next_retry_at；
  3 次重试耗尽后置 failed 留痕（PRD NOTIFY-04）。
- 与 audit_writer 同为 asyncio 后台任务（不引调度框架），由 worker 进程
  启动序 start/stop 管理。
"""
import asyncio
import logging
import socket
from datetime import datetime, timedelta

from app import audit
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_mod
from app.core.database import async_session_factory
from app.core.security import decrypt_text
from app.models.auth import User
from app.models.notify import NotificationRecord, NotifyChannel
from app.notify import ChannelSendError, NotifyMessage, get_channel

logger = logging.getLogger("opspilot.notify")

NOTIFY_CONSUMER_GROUP = "notify-workers"

# 失败重试退避序列（秒）：1m / 5m / 15m，超过次数置 failed
RETRY_BACKOFF = [60, 300, 900]
MAX_RETRY = len(RETRY_BACKOFF)

# 定时扫描间隔（秒）与单轮扫描上限
SCAN_INTERVAL = 30
SCAN_BATCH = 100

# 队列消息触发后的延迟（秒）：给业务事务留出 commit 时间，降低竞态空扫概率
_QUEUE_SETTLE_DELAY = 1.0


async def _audit_terminal_password_reset_failure(record: NotificationRecord) -> None:
    if record.event != "password_reset" or record.channel_type != "email" or record.status != "failed":
        return
    key = redis_mod.KEY_PWD_RESET_NOTIFICATION_IP.format(record_id=record.id)
    source_ip = await redis_mod.redis_client.get(key)
    audit.log(
        module="auth",
        action="pwd_reset_failed",
        result="failed",
        source_ip=source_ip,
        detail={"reason": "email_send_failed", "error": record.error},
    )
    await redis_mod.redis_client.delete(key)


async def _resolve_emails(session: AsyncSession, receiver: str | None) -> list[str]:
    """把 record.receiver（逗号分隔用户名）解析为邮箱列表（冲突决策 C2）。

    无邮箱的用户直接跳过；全部无邮箱时返回空列表，由调用方置 failed 留痕。
    """
    usernames = [u.strip() for u in (receiver or "").split(",") if u.strip()]
    if not usernames:
        return []
    rows = await session.execute(
        select(User.email).where(User.username.in_(usernames), User.email.is_not(None))
    )
    return sorted({e for e in rows.scalars() if e})


def _schedule_retry(record: NotificationRecord, error: str) -> None:
    """失败退避调度：未耗尽次数保持 pending 并排下次重试，耗尽置 failed。

    注：scan_once 发送前已 CAS 把记录认领为 sending，重试分支必须显式写回 pending，
    否则记录会卡在 sending（status 内存值未变不会被 flush）而永久漏扫。
    """
    record.error = error[:500]
    if record.retry_count >= MAX_RETRY:
        record.status = "failed"
        return
    record.status = "pending"
    delay = RETRY_BACKOFF[min(record.retry_count, MAX_RETRY - 1)]
    record.retry_count += 1
    record.next_retry_at = datetime.now() + timedelta(seconds=delay)


async def dispatch_record(session: AsyncSession, record: NotificationRecord) -> None:
    """发送单条 pending 记录并就地回写结果（分发循环与单测共用入口）。

    终态规则：渠道未启用/收件人无邮箱属"重试也无意义"的确定性失败，直接
    置 failed；网络/服务类异常走退避重试。
    """
    channel_row = (
        await session.execute(select(NotifyChannel).where(NotifyChannel.type == record.channel_type))
    ).scalar_one_or_none()
    security_email = record.event == "password_reset" and record.channel_type == "email"
    if channel_row is None or (not channel_row.enabled and not security_email):
        record.status = "failed"
        record.error = "渠道未启用"
        return
    receivers: list[str] = []
    if record.channel_type == "email":
        receivers = [record.receiver] if security_email and record.receiver else await _resolve_emails(session, record.receiver)
        if not receivers:
            record.status = "failed"
            record.error = "收件人均未配置邮箱"
            return
    else:
        receivers = [item.strip() for item in (record.receiver or "").split(",") if item.strip()]
    # 解密敏感配置（SMTP 密码 / 签名密钥）；解密失败按可重试错误处理
    try:
        secret = decrypt_text(channel_row.secret_enc) if channel_row.secret_enc else None
    except Exception:  # noqa: BLE001 密钥变更等导致解密失败
        _schedule_retry(record, "渠道密钥解密失败")
        return
    message = NotifyMessage(
        event=record.event, title=record.title, content=record.content, receivers=receivers
    )
    try:
        await get_channel(record.channel_type).send(message, channel_row.config or {}, secret)
    except ChannelSendError as exc:
        _schedule_retry(record, str(exc))
        await _audit_terminal_password_reset_failure(record)
        logger.warning("通知 #%s [%s/%s] 发送失败: %s", record.id, record.event, record.channel_type, exc)
        return
    except Exception as exc:  # noqa: BLE001 渠道实现漏转的异常兜底为可重试
        _schedule_retry(record, f"渠道异常: {exc}")
        logger.exception("通知 #%s 渠道异常", record.id)
        return
    record.status = "success"
    record.sent_at = datetime.now()
    record.error = None


async def _claim_record(session: AsyncSession, record_id: int) -> bool:
    """原子认领单条待发记录：仅当仍为 pending 时 CAS 置 sending，返回是否拿到发送权。

    Why：_consume_loop（队列触发）与 _scan_loop（30s 定时）是同一事件循环内的两个
    并发任务；旧逻辑下记录在整批 SMTP 发送期间仍为 pending（commit 在末尾），
    两轮扫描会取到同一条记录并各发一次 → 重复邮件。以 status='pending' 为 CAS 条件
    原子置 sending，仅首个扫描者 rowcount==1 拿到发送权，其余扫描者/未来多 worker
    实例均 rowcount==0 跳过。立即 commit 使认领对其他连接可见。
    """
    result = await session.execute(
        update(NotificationRecord)
        .where(NotificationRecord.id == record_id, NotificationRecord.status == "pending")
        .values(status="sending")
    )
    await session.commit()
    return result.rowcount == 1


async def scan_once() -> int:
    """扫描一轮到期的 pending 记录，逐条原子认领后发送，返回实际发送条数。

    条件：status=pending 且（next_retry_at 为空 或 已到期）——首发与重试
    统一走此入口，队列消息只是提前触发一轮扫描。
    防重复发送：发送前先 _claim_record 原子认领，认领失败（已被并发扫描/其他
    实例抢走）则跳过，避免同一记录被发送多次。
    """
    async with async_session_factory() as session:
        records = (
            (
                await session.execute(
                    select(NotificationRecord)
                    .where(
                        NotificationRecord.status == "pending",
                        or_(
                            NotificationRecord.next_retry_at.is_(None),
                            NotificationRecord.next_retry_at <= datetime.now(),
                        ),
                    )
                    .order_by(NotificationRecord.id)
                    .limit(SCAN_BATCH)
                )
            )
            .scalars()
            .all()
        )
        processed = 0
        for record in records:
            # 认领失败说明已被并发扫描/其他实例处理，跳过（不重复发）
            if not await _claim_record(session, record.id):
                continue
            await dispatch_record(session, record)
            await session.commit()
            processed += 1
        return processed


class NotifyDispatcher:
    """分发器单例：队列消费 + 定时扫描两个后台任务，start/stop 由 worker 管理。"""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        # 消费者名沿用容器 hostname（与执行队列消费者同约定）
        self._consumer = socket.gethostname() or "notify-1"

    async def start(self) -> None:
        """确保消费组存在并启动两个后台循环（worker 启动序调用）。"""
        await redis_mod.ensure_stream_group(redis_mod.NOTIFY_QUEUE, NOTIFY_CONSUMER_GROUP)
        # 上次进程在 sending 中途崩溃/重启会留下“悬挂”记录（扫描只取 pending），
        # 启动时重置回 pending 交由扫描重新认领发送。
        # 注：V1 单 worker 下启动时无合法 sending，全量重置安全；若未来扩为多 worker，
        # 需改为按认领时长阀值只重置超时 sending（需新增认领时间列），以免误重置其他实例在飞记录。
        await self._reset_orphaned_sending()
        self._stopping = False
        loop = asyncio.get_running_loop()
        self._tasks = [
            loop.create_task(self._consume_loop()),
            loop.create_task(self._scan_loop()),
        ]
        logger.info("通知分发器已启动（扫描间隔 %ss，退避 %s）", SCAN_INTERVAL, RETRY_BACKOFF)

    @staticmethod
    async def _reset_orphaned_sending() -> None:
        """启动兜底：把上次异常退出遗留的 sending 记录重置回 pending，交由扫描重新发送。"""
        async with async_session_factory() as session:
            await session.execute(
                update(NotificationRecord)
                .where(NotificationRecord.status == "sending")
                .values(status="pending")
            )
            await session.commit()

    async def stop(self) -> None:
        """取消后台任务；在途 pending 记录由下次启动的定时扫描兜底续发。"""
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        logger.info("通知分发器已停止")

    async def _consume_loop(self) -> None:
        """队列消费循环：收到消息即认领 ACK，稍候触发一轮扫描（加速器语义）。

        消息本身不携带必须信息（记录以 DB 为准），竞态导致的空扫由
        30s 定时扫描最终兜底，因此可以放心认领即 ACK。
        """
        while not self._stopping:
            try:
                messages = await redis_mod.redis_client.xreadgroup(
                    groupname=NOTIFY_CONSUMER_GROUP,
                    consumername=self._consumer,
                    streams={redis_mod.NOTIFY_QUEUE: ">"},
                    count=100,
                    block=5000,
                )
                if not messages:
                    continue
                for _stream, entries in messages:
                    for msg_id, _fields in entries:
                        await redis_mod.redis_client.xack(
                            redis_mod.NOTIFY_QUEUE, NOTIFY_CONSUMER_GROUP, msg_id
                        )
                # 给业务事务留 commit 窗口后立即扫描，缩短通知触达延迟
                await asyncio.sleep(_QUEUE_SETTLE_DELAY)
                await scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 消费循环不能因单次异常退出
                logger.exception("通知队列消费异常，5 秒后重试")
                await asyncio.sleep(5)

    async def _scan_loop(self) -> None:
        """定时扫描循环：可靠兜底（丢消息/重试到期/进程重启后的续发）。"""
        while not self._stopping:
            try:
                await asyncio.sleep(SCAN_INTERVAL)
                await scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 扫描失败等待下一轮，不退出
                logger.exception("通知扫描异常，等待下一轮")


# 全局单例（worker 进程内共享）
notify_dispatcher = NotifyDispatcher()
