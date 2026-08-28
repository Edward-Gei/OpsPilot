"""补丁：删除 ticket_step.credential_id 遗留列（0007 漏删）。

0007 执行范式改造删除了 template_step.credential_id，但工单侧快照表
ticket_step 的同名列被遗漏。该列 NOT NULL 且无默认值，而 ORM 模型
（TicketStep）已不再包含此字段，导致提交工单 INSERT 报 1364 → 500。

幂等策略：与 0007 一致，先查 information_schema 判断列是否存在再执行，
全新库（0001 create_all 即新结构）与已修复库重复执行结果一致。
"""
from alembic import op
from sqlalchemy import text

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    """判断列是否存在（当前库）。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if _column_exists(bind, "ticket_step", "credential_id"):
        op.execute("ALTER TABLE ticket_step DROP COLUMN credential_id")


def downgrade() -> None:
    # 凭据已随作业主机管理，该列无业务含义，不提供回滚恢复
    pass
