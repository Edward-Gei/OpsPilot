"""数据库执行器入口：持久队列、多副本共享额度，独立于工单 Worker。"""
import asyncio
import logging
import os
from pathlib import Path
import signal
import socket
import uuid

from app.core.database_executor import async_session_factory, engine
from app.services.database_execution_service import run_claimed_task
from app.services.database_maintenance_service import executor_heartbeat, run_maintenance_once
from app.services.database_quota_service import claim_next_task

logger = logging.getLogger("opspilot.database_executor")
IDENTITY_FILE = Path("/tmp/opspilot-database-executor-id")


async def run():
    """进程空闲也心跳，退出时给目标取消留出有界时间。"""
    identity = f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
    IDENTITY_FILE.write_text(identity)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    running = set()
    async def heartbeat_loop():
        while not stop.is_set():
            try:
                await executor_heartbeat(async_session_factory, identity)
            except Exception:
                logger.warning("database executor heartbeat_unavailable")
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except TimeoutError:
                pass

    async def maintenance_loop():
        while not stop.is_set():
            try:
                await run_maintenance_once(async_session_factory, identity)
            except Exception:
                logger.warning("database executor maintenance_unavailable")
            try:
                await asyncio.wait_for(stop.wait(), 5)
            except TimeoutError:
                pass

    # 慢控制核实不得阻塞空闲心跳或其他查询认领。
    heartbeat_job = asyncio.create_task(heartbeat_loop())
    maintenance_job = asyncio.create_task(maintenance_loop())
    try:
        while not stop.is_set():
            try:
                running = {job for job in running if not job.done()}
                if len(running) < 10:
                    claim = await claim_next_task(async_session_factory, identity)
                    if claim:
                        job = asyncio.create_task(run_claimed_task(async_session_factory, claim))
                        job.add_done_callback(_report_task)
                        running.add(job)
                        continue
            except Exception:
                # 不打印 SQLAlchemy / driver 原始异常，避免输入/结果/密码进入容器日志。
                logger.warning("database executor coordination_unavailable")
            try:
                await asyncio.wait_for(stop.wait(), 0.25)
            except TimeoutError:
                pass
    finally:
        stop.set()
        maintenance_job.cancel()
        await asyncio.gather(maintenance_job, return_exceptions=True)
        for job in running:
            job.cancel()
        try:
            await asyncio.wait_for(asyncio.gather(*running, return_exceptions=True), 20)
        except TimeoutError:
            logger.warning("database executor shutdown_pending_recovery")
        await heartbeat_job
        await engine.dispose()


def _report_task(job):
    if not job.cancelled() and job.exception():
        logger.warning("database executor task_failed_before_settlement")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
