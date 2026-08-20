"""主机项目、RI、主机系列和公网 IP 字段。"""
from alembic import op
from sqlalchemy import text

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TABLE = "host"
COLUMNS: list[tuple[str, str]] = [
    ("project", "project VARCHAR(16) NULL COMMENT '项目：mitrade/tradingkey' AFTER ip"),
    ("public_ip", "public_ip VARCHAR(45) NULL COMMENT '公网 IP（兼容 IPv6 长度）' AFTER project"),
    ("ri", "ri VARCHAR(64) NULL COMMENT 'RI' AFTER public_ip"),
    ("host_series", "host_series VARCHAR(64) NULL COMMENT '主机系列，自由文本' AFTER ri"),
]


def _has_column(bind, column: str) -> bool:
    """兼容 0001 直接使用当前 ORM 元数据创建的新库。"""
    count = bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": TABLE, "c": column},
    ).scalar()
    return bool(count)


def upgrade() -> None:
    """补齐字段并为存量主机设置项目默认值。"""
    bind = op.get_bind()
    for name, ddl in COLUMNS:
        if not _has_column(bind, name):
            op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {ddl}")
    op.execute("UPDATE host SET project = 'mitrade' WHERE project IS NULL")
    op.execute(
        "ALTER TABLE host MODIFY COLUMN project VARCHAR(16) NOT NULL "
        "DEFAULT 'mitrade' COMMENT '项目：mitrade/tradingkey'"
    )


def downgrade() -> None:
    """开发环境回滚时逆序删除新增列。"""
    bind = op.get_bind()
    for name, _ in reversed(COLUMNS):
        if _has_column(bind, name):
            op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {name}")
