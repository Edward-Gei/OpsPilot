"""为配置发布任务持久化重试次数和下次执行时间。"""
from alembic import op
from sqlalchemy import Column, Integer, inspect

from app.models.base import DT3


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 0001 复用当前 ORM，空库初始化可能已含后续列；旧库仍正常补列。
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("config_task")}
    if "retry_count" not in existing:
        op.add_column("config_task", Column("retry_count", Integer, nullable=False, server_default="0"))
    if "next_attempt_at" not in existing:
        op.add_column("config_task", Column("next_attempt_at", DT3))


def downgrade() -> None:
    op.drop_column("config_task", "next_attempt_at")
    op.drop_column("config_task", "retry_count")
