"""补齐历史数据库遗漏的工单凭据引用字段。"""
from alembic import op
from sqlalchemy import text

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    """兼容已越过 0009 但实际列缺失的历史库。"""
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
    """补齐 0009 未实际创建的 credential_refs 列。"""
    bind = op.get_bind()
    if not _column_exists(bind, "ticket_template", "credential_refs"):
        op.execute(
            "ALTER TABLE ticket_template ADD COLUMN credential_refs JSON NULL "
            "COMMENT '引用凭据 [{alias, credential_id}]'"
        )
    if not _column_exists(bind, "ticket", "credential_refs"):
        op.execute(
            "ALTER TABLE ticket ADD COLUMN credential_refs JSON NULL "
            "COMMENT '引用凭据快照 [{alias, credential_id, credential_name}]'"
        )


def downgrade() -> None:
    """此补偿迁移不删除历史工单引用快照。"""
    pass
