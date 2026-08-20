"""应用项目类型字段。"""
from alembic import op
from sqlalchemy import text

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

TABLE = "application"
COLUMN = "project_type"


def _has_column(bind) -> bool:
    """兼容 0001 直接使用当前 ORM 元数据创建的新库。"""
    count = bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": TABLE, "c": COLUMN},
    ).scalar()
    return bool(count)


def upgrade() -> None:
    """补齐项目类型并将存量应用归为前端。"""
    bind = op.get_bind()
    if not _has_column(bind):
        op.execute(
            "ALTER TABLE application ADD COLUMN project_type VARCHAR(16) NULL "
            "COMMENT '项目类型：frontend/backend' AFTER deploy_type"
        )
    op.execute("UPDATE application SET project_type = 'frontend' WHERE project_type IS NULL")
    op.execute(
        "ALTER TABLE application MODIFY COLUMN project_type VARCHAR(16) NOT NULL "
        "DEFAULT 'frontend' COMMENT '项目类型：frontend/backend'"
    )


def downgrade() -> None:
    """开发环境回滚时删除项目类型字段。"""
    bind = op.get_bind()
    if _has_column(bind):
        op.execute("ALTER TABLE application DROP COLUMN project_type")
