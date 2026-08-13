"""将参数契约从流程模板移动到工单模板。"""

from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    return bool(bind.execute(sa.text(
        "SELECT COUNT(*) FROM information_schema.columns "
        "WHERE table_schema = DATABASE() AND table_name = :table AND column_name = :column"
    ), {"table": table, "column": column}).scalar())


def upgrade() -> None:
    """开发阶段直接调整空测试表结构，不迁移旧参数数据。"""
    bind = op.get_bind()
    if not _column_exists(bind, "ticket_template", "params_schema"):
        op.add_column("ticket_template", sa.Column("params_schema", sa.JSON(), nullable=True))
        op.execute("UPDATE ticket_template SET params_schema = '[]' WHERE params_schema IS NULL")
        op.alter_column("ticket_template", "params_schema", nullable=False)
    if not _column_exists(bind, "ticket_template", "generator_script"):
        op.add_column("ticket_template", sa.Column("generator_script", sa.Text(), nullable=True))
    if not _column_exists(bind, "ticket_template", "generator_timeout"):
        op.add_column("ticket_template", sa.Column("generator_timeout", sa.Integer(), nullable=True))
    for column in ("params_schema", "generator_script", "generator_timeout"):
        if _column_exists(bind, "process_template", column):
            op.drop_column("process_template", column)


def downgrade() -> None:
    pass
