"""Worker 进程入口（M5 执行引擎版，02-技术架构 §4.1/§4.2）。

启动序：
    1. ensure_stream_group：确保执行队列消费组存在
    2. audit_writer.start()：worker 进程独立启动审计写入器（lifespan 仅覆盖 api 进程）
    3. 崩溃恢复：recover_db_executions（DB running/paused → interrupted）
       + reclaim_pending_messages（PEL 重认领/丢弃），返回的可重试消息按新消息处理
    4. 消费循环：xreadgroup → 逐条**立即 XACK**（认领即 ACK，已确认决策三）
       → create_task(run_execution) 放入任务集合并发执行

认领即 ACK 的取舍：XACK 后、execution 置 running 前存在极小崩溃窗口，此时
消息已不在 PEL 而 DB 仍是 queued——该窗口由人工排查兜底（已声明接受）；
换来的收益是 queued/running 两阶段崩溃可精确区分（恢复三分支的前提）。

优雅退出：SIGTERM/SIGINT 置 stop_event，消费循环退出后**不等待**在跑执行
（compose stop 默认 10s 宽限期不足以跑完批量任务）；在跑执行由下次启动的
recover_db_executions 归档为 interrupted(system_crash)。
"""
import asyncio
import logging
import signal
import socket

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.audit.partition import run_maintenance
from app.audit.writer import audit_writer
from app.core import redis as redis_mod
from app.core.config import settings
from app.engine import pipeline, recovery
from app.notify.dispatcher import notify_dispatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("opspilot.worker")

# 消费者名用容器 hostname（V1 单 worker；多实例扩展时天然区分）
CONSUMER_NAME = socket.gethostname() or "worker-1"

# 在跑执行任务集合：持有强引用防 GC，退出时用于统计
_running_tasks: set[asyncio.Task] = set()

# APScheduler：内嵌 worker 进程的定时器（M7 仅承担审计分区维护，
# 通知重试已由 notify_dispatcher 自研 30s 扫描实现，不迁移）
_scheduler = AsyncIOScheduler()


async def _partition_job() -> None:
    """每日分区维护作业：滚动预建 + 按保留期 DROP 过期分区（异常不影响下次调度）。"""
    try:
        await run_maintenance()
    except Exception:  # noqa: BLE001 定时任务失败只记日志，等下次调度重试
        logger.exception("审计分区维护任务失败")


def _spawn_execution(execution_id: int) -> None:
    """把一次执行放入后台任务集合（认领已 ACK，生命周期由 pipeline 全权负责）。"""
    task = asyncio.get_running_loop().create_task(pipeline.run_execution(execution_id))
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)


async def _handle_message(msg_id: str, fields: dict) -> None:
    """处理一条队列消息：立即 XACK（认领即 ACK）后派发执行。"""
    await redis_mod.redis_client.xack(redis_mod.EXEC_QUEUE, redis_mod.EXEC_CONSUMER_GROUP, msg_id)
    try:
        execution_id = int(fields.get("execution_id", 0) or 0)
    except (TypeError, ValueError):
        execution_id = 0
    if not execution_id:
        logger.error("消息 %s 缺少合法 execution_id（%s），已 ACK 丢弃", msg_id, fields)
        return
    logger.info("认领执行 %s（消息 %s）", execution_id, msg_id)
    _spawn_execution(execution_id)


async def startup() -> None:
    """启动序：消费组 → 审计 → 崩溃恢复 → 通知分发器。"""
    await redis_mod.ensure_stream_group(redis_mod.EXEC_QUEUE, redis_mod.EXEC_CONSUMER_GROUP)
    audit_writer.start()
    # 崩溃恢复三分支（02 §4.2）：先归档 DB 滞留现场，再接管 PEL 消息
    await recovery.recover_db_executions(CONSUMER_NAME)
    reclaimed = await recovery.reclaim_pending_messages(CONSUMER_NAME)
    # 通知分发器（M6）：队列消费 + 定时扫描兜底，独立于执行队列
    await notify_dispatcher.start()
    # 审计分区维护（M7）：启动先补跑一次（覆盖停机跨日），再每日 03:30 定时滚动
    await _partition_job()
    _scheduler.add_job(_partition_job, CronTrigger(hour=3, minute=30))
    _scheduler.start()
    # 重认领的消息按新消息流程处理（XCLAIM 时已在 PEL 内，此处补 ACK+派发）
    for msg_id, fields in reclaimed:
        await _handle_message(msg_id, fields)


async def consume_loop(stop_event: asyncio.Event) -> None:
    """消费循环：阻塞读取执行队列，认领即 ACK 后异步派发 pipeline。"""
    logger.info("%s worker[%s] 启动，监听队列 %s", settings.app_name, CONSUMER_NAME, redis_mod.EXEC_QUEUE)
    while not stop_event.is_set():
        try:
            # block 5s：兼顾退出响应速度与空转开销
            messages = await redis_mod.redis_client.xreadgroup(
                groupname=redis_mod.EXEC_CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={redis_mod.EXEC_QUEUE: ">"},
                count=10,
                block=5000,
            )
            for _stream, entries in messages or []:
                for msg_id, fields in entries:
                    await _handle_message(msg_id, fields)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 消费循环不能因单次异常退出
            logger.exception("消费循环异常，5 秒后重试")
            await asyncio.sleep(5)


async def main() -> None:
    """Worker 主函数：启动序 + 消费循环 + SIGTERM/SIGINT 优雅退出。"""
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows 本地调试无信号处理器支持，Ctrl+C 走 KeyboardInterrupt
            pass
    await startup()
    task = asyncio.create_task(consume_loop(stop_event))
    await stop_event.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    if _running_tasks:
        # 不等待在跑执行（宽限期不足），由下次启动的崩溃恢复归 system_crash
        logger.warning("退出时仍有 %d 个执行在跑，将由重启后的崩溃恢复归档", len(_running_tasks))
    await notify_dispatcher.stop()
    # 先停定时器再停审计写入器（分区任务可能直接落库，顺序无严格依赖，保持对称）
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    await audit_writer.stop()
    await redis_mod.redis_client.aclose()
    logger.info("worker 已优雅退出")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
