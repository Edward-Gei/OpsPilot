"""模型基类与公共列助手：统一主键 / 时间戳 / MySQL 表选项，避免逐表重复定义。"""
from datetime import datetime

from sqlalchemy import BigInteger, MetaData, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 统一索引/约束命名规则，保证 Alembic 迁移可预测
NAMING_CONVENTION = {
    "ix": "idx_%(column_0_label)s",
    "uq": "uk_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

# MySQL DATETIME(3) 类型别名
DT3 = DATETIME(fsp=3)
# 无符号大整型主键/外键列类型（兼容 SQLite 单测：BigInteger 变体）
UBIGINT = BIGINT(unsigned=True).with_variant(BigInteger(), "sqlite")


class Base(DeclarativeBase):
    """声明式基类：utf8mb4 + InnoDB 表选项全局生效。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


def pk_column() -> Mapped[int]:
    """标准自增主键列。"""
    return mapped_column(UBIGINT, primary_key=True, autoincrement=True)


def created_at_column() -> Mapped[datetime]:
    """创建时间：数据库侧默认 CURRENT_TIMESTAMP(3)。"""
    return mapped_column(DT3, server_default=text("CURRENT_TIMESTAMP(3)"), nullable=False)


def updated_at_column() -> Mapped[datetime]:
    """更新时间：MySQL ON UPDATE 自动刷新（借助 DEFAULT 子句一并渲染）。"""
    return mapped_column(
        DT3,
        server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        nullable=False,
    )
