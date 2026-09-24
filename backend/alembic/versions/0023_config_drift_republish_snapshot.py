"""为明确确认的漂移覆盖任务保存远端快照。"""
from alembic import op
from sqlalchemy import Column

from app.models.base import UBIGINT


revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_task", Column("confirmed_snapshot_id", UBIGINT))


def downgrade() -> None:
    op.drop_column("config_task", "confirmed_snapshot_id")
