"""独立执行器平台连接池，避免每副本放大 API 的默认连接数。"""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.async_database_url, pool_size=4, max_overflow=0,
    pool_pre_ping=True, pool_recycle=3600, echo=False)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
