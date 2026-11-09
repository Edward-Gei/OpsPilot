"""容器健康检查只确认本进程近期心跳与平台库可达。"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.database_management import DatabaseQuotaBucket


async def check():
    identity = Path("/tmp/opspilot-database-executor-id").read_text().strip()
    engine = create_async_engine(settings.async_database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine)() as session:
            heartbeat = (await session.execute(select(DatabaseQuotaBucket.heartbeat_at).where(
                DatabaseQuotaBucket.scope == "executor", DatabaseQuotaBucket.scope_key == identity))).scalar_one_or_none()
            return heartbeat is not None and heartbeat > datetime.now()-timedelta(seconds=25)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        healthy = asyncio.run(check())
    except Exception:
        healthy = False
    raise SystemExit(0 if healthy else 1)
