"""为明确确认的漂移覆盖任务保存远端快照。"""
from alembic import op
from sqlalchemy import Column, inspect

from app.models.base import UBIGINT


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 兼容 0001 由当前 ORM 建出的新库，不影响真实旧库升级。
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("config_task")}
    if "confirmed_snapshot_id" not in existing:
        op.add_column("config_task", Column("confirmed_snapshot_id", UBIGINT))


def downgrade() -> None:
    op.drop_column("config_task", "confirmed_snapshot_id")
