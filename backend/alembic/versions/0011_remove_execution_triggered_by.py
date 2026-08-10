"""移除执行记录的触发来源列，统一由执行状态和工单状态表达当前信息。"""

from alembic import op
from sqlalchemy import text


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    """兼容已初始化和半初始化数据库，确保迁移可重复执行。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :table "
                "AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).scalar()
    )


def upgrade() -> None:
    """删除执行表中的触发来源列；新建数据库由 ORM 模型直接生成正确结构。"""
    bind = op.get_bind()
    if _column_exists(bind, "execution", "triggered_by"):
        op.execute("ALTER TABLE execution DROP COLUMN triggered_by")


def downgrade() -> None:
    """该字段已不再属于执行记录模型，不提供回滚恢复。"""
    pass
