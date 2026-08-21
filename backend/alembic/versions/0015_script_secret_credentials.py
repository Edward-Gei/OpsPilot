"""脚本密钥凭据字段扩展。"""
from alembic import op
from sqlalchemy import text

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _column_exists(bind, column: str) -> bool:
    return bool(bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'credential' AND column_name = :column"
        ),
        {"column": column},
    ).scalar())


def upgrade() -> None:
    """兼容存量 SSH 凭据，追加脚本密钥所需的类型和文件名字段。"""
    bind = op.get_bind()
    if not _column_exists(bind, "file_name"):
        op.execute("ALTER TABLE credential ADD COLUMN file_name VARCHAR(128) NULL COMMENT '文本密钥文件名'")
    op.execute("ALTER TABLE credential MODIFY COLUMN auth_type VARCHAR(32) NOT NULL")
    op.execute("ALTER TABLE credential MODIFY COLUMN login_user VARCHAR(64) NULL")


def downgrade() -> None:
    """仅回退新增文件名；存量脚本类型不能无损收窄。"""
    bind = op.get_bind()
    if _column_exists(bind, "file_name"):
        op.execute("ALTER TABLE credential DROP COLUMN file_name")
