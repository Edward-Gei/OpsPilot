"""工单模板并发控制。"""
from alembic import op
from sqlalchemy import text

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _column_exists(bind, column: str) -> bool:
    """兼容新库由当前 ORM 元数据直接建表的场景。"""
    return bool(bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'ticket_template' AND column_name = :column"
        ),
        {"column": column},
    ).scalar())


def upgrade() -> None:
    """为模板增加默认关闭的并发控制及当前执行占用记录。"""
    bind = op.get_bind()
    if not _column_exists(bind, "concurrency_control_enabled"):
        op.execute(
            "ALTER TABLE ticket_template ADD COLUMN concurrency_control_enabled TINYINT(1) NULL "
            "COMMENT '是否启用同模板并发控制' AFTER allow_withdraw"
        )
    op.execute("UPDATE ticket_template SET concurrency_control_enabled = 0 WHERE concurrency_control_enabled IS NULL")
    op.execute(
        "ALTER TABLE ticket_template MODIFY COLUMN concurrency_control_enabled TINYINT(1) NOT NULL "
        "DEFAULT 0 COMMENT '是否启用同模板并发控制'"
    )
    if not _column_exists(bind, "active_execution_id"):
        op.execute(
            "ALTER TABLE ticket_template ADD COLUMN active_execution_id BIGINT UNSIGNED NULL "
            "COMMENT '并发控制当前占用的执行实例 ID' AFTER concurrency_control_enabled"
        )


def downgrade() -> None:
    """开发环境回滚时删除并发控制字段。"""
    bind = op.get_bind()
    if _column_exists(bind, "active_execution_id"):
        op.execute("ALTER TABLE ticket_template DROP COLUMN active_execution_id")
    if _column_exists(bind, "concurrency_control_enabled"):
        op.execute("ALTER TABLE ticket_template DROP COLUMN concurrency_control_enabled")
