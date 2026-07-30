"""数据库接入：SQLAlchemy 2.x async 引擎与会话工厂。"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

# 连接池参数按千级用户/单机部署设定
engine = create_async_engine(
    settings.async_database_url,
    pool_pre_ping=True,   # 取连接前探活，规避 MySQL wait_timeout 断连
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    echo=settings.debug,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每请求一个会话，异常自动回滚。"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
