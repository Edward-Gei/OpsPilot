"""为配置发布任务持久化重试次数和下次执行时间。"""
from alembic import op
from sqlalchemy import Column, Integer

from app.models.base import DT3


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_task", Column("retry_count", Integer, nullable=False, server_default="0"))
    op.add_column("config_task", Column("next_attempt_at", DT3))


def downgrade() -> None:
    op.drop_column("config_task", "next_attempt_at")
    op.drop_column("config_task", "retry_count")
