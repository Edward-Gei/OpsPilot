"""审计异步写入器：asyncio.Queue 缓冲 + 后台任务批量 INSERT。

设计（02-技术架构 §5.2）：
- 写入方仅入队（非阻塞），后台协程按批（最多 50 条 / 1s 超时）落库；
- 应用关闭时 stop() 会先 flush 队列剩余事件再退出；
- 落库失败仅打日志不抛出（审计不能反噬业务请求）。
"""
import asyncio
import logging
from datetime import datetime

from app.core.database import async_session_factory
from app.models.audit import AuditLog

logger = logging.getLogger("opspilot.audit")

_BATCH_SIZE = 50       # 单批最大落库条数
_FLUSH_INTERVAL = 1.0  # 队列空闲时的批间隔（秒）


class AuditWriter:
    """审计写入器单例：start/stop 由应用 lifespan 管理。"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[AuditLog] = asyncio.Queue(maxsize=10000)
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        """启动后台消费任务（lifespan 启动阶段调用）。"""
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._run())
        logger.info("审计写入器已启动")

    async def stop(self) -> None:
        """停止并 flush 剩余事件（lifespan 关闭阶段调用）。"""
        self._stopping = True
        if self._task:
            await self._task
            self._task = None
        # 后台任务退出后队列可能仍有残留（stop 与入队竞争），再兜底刷一次
        await self._flush(self._drain())
        logger.info("审计写入器已停止")

    def enqueue(self, entry: AuditLog) -> None:
        """事件入队；队列满时丢弃并告警（保护内存，审计不阻塞业务）。"""
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.error("审计队列已满，丢弃事件: %s/%s", entry.module, entry.action)

    def _drain(self) -> list[AuditLog]:
        """非阻塞取空队列。"""
        items: list[AuditLog] = []
        while not self._queue.empty():
            items.append(self._queue.get_nowait())
        return items

    async def _run(self) -> None:
        """后台消费循环：攒批落库，stop 信号后退出。"""
        while not self._stopping:
            batch: list[AuditLog] = []
            try:
                # 等第一条（带超时以便响应 stop 信号）
                first = await asyncio.wait_for(self._queue.get(), timeout=_FLUSH_INTERVAL)
                batch.append(first)
            except asyncio.TimeoutError:
                continue
            # 非阻塞取满一批
            while len(batch) < _BATCH_SIZE and not self._queue.empty():
                batch.append(self._queue.get_nowait())
            await self._flush(batch)

    async def _flush(self, batch: list[AuditLog]) -> None:
        """批量落库；失败仅记录日志（审计写入不影响业务可用性）。"""
        if not batch:
            return
        try:
            async with async_session_factory() as session:
                session.add_all(batch)
                await session.commit()
        except Exception:  # noqa: BLE001 审计落库失败不能抛出
            logger.exception("审计批量落库失败，丢失 %d 条事件", len(batch))


# 全局单例（api 进程内共享）
audit_writer = AuditWriter()


def log(
    *,
    module: str,
    action: str,
    result: str = "success",
    actor_id: int | None = None,
    actor_name: str | None = None,
    source_ip: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_name: str | None = None,
    detail: dict | None = None,
) -> None:
    """记录一条审计事件（非阻塞入队）。

    result 取值 success/failed（与已建表 CHECK 一致，冲突决策②）；
    created_at 为分区键无数据库默认值，此处显式赋当前时间。
    """
    audit_writer.enqueue(
        AuditLog(
            created_at=datetime.now(),
            actor_id=actor_id,
            actor_name=actor_name,
            source_ip=source_ip,
            module=module,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            result=result,
            detail=detail,
        )
    )
