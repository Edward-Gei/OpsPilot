"""M5 执行引擎：ticket 表新增 interrupt_reason 中断起因字段。

工单 9 态状态机（03-数据库设计 §5.1）：终态为 interrupted 时记录起因
user_abort / system_crash / worker_lost（预留 infra_failure / unknown），
用于列表筛选、统计与按起因区分的通知策略。

与 0002 相同的幂等策略：全新库执行 0001 create_all 时已含该列，需先查
information_schema 判断列是否存在再 ADD COLUMN。
"""
from alembic import op
from sqlalchemy import text

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLE = "ticket"
COLUMN = "interrupt_reason"
DDL = (
    "interrupt_reason VARCHAR(32) NULL "
    "COMMENT '终态为 interrupted 时的起因：user_abort/system_crash/worker_lost' "
    "AFTER status"
)


def _has_column(bind) -> bool:
    """查 information_schema 判断列是否已存在（MySQL 8 不支持 ADD COLUMN IF NOT EXISTS）。"""
    count = bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": TABLE, "c": COLUMN},
    ).scalar()
    return bool(count)


def upgrade() -> None:
    """存量库补列；全新库（0001 已含该字段）自动跳过。"""
    if not _has_column(op.get_bind()):
        op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {DDL}")


def downgrade() -> None:
    """回滚：删除中断起因列（仅供开发环境使用）。"""
    if _has_column(op.get_bind()):
        op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
