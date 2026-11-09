"""五引擎注册表：懒加载避免 API 为输入校验加载驱动和目标连接。"""
from importlib import import_module

from app.database_adapters.base import AdapterError, DatabaseAdapter

_ADAPTERS = {
    "mysql": ("mysql", "MySQLAdapter"),
    "postgresql": ("postgresql", "PostgreSQLAdapter"),
    "mongodb": ("mongodb", "MongoDBAdapter"),
    "redis": ("redis", "RedisAdapter"),
    "clickhouse": ("clickhouse", "ClickHouseAdapter"),
}


def get_adapter(engine: str) -> DatabaseAdapter:
    """只允许已登记的内置适配器，不从用户输入导入任意模块。"""
    if engine not in _ADAPTERS:
        raise AdapterError("unsupported_engine", "不支持的数据库引擎")
    module, class_name = _ADAPTERS[engine]
    return getattr(import_module(f"app.database_adapters.{module}"), class_name)()
