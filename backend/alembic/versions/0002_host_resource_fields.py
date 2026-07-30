"""主机资源配置字段：os / cpu_cores / memory_gb / disk_gb。

注意：0001 迁移复用 ORM 元数据 create_all，全新库执行 0001 时即已包含新字段，
因此本迁移必须先查 information_schema 判断列是否存在，避免重复 ADD COLUMN 报错。
"""
from alembic import op
from sqlalchemy import text

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLE = "host"
# (列名, DDL 片段)；顺序即 ALTER 追加顺序
COLUMNS: list[tuple[str, str]] = [
    ("os", "os VARCHAR(64) NULL COMMENT '操作系统，自由文本' AFTER region"),
    ("cpu_cores", "cpu_cores INT NULL COMMENT 'CPU 核数' AFTER os"),
    ("memory_gb", "memory_gb INT NULL COMMENT '内存 GB' AFTER cpu_cores"),
    ("disk_gb", "disk_gb INT NULL COMMENT '磁盘 GB' AFTER memory_gb"),
]


def _has_column(bind, column: str) -> bool:
    """查 information_schema 判断列是否已存在（MySQL 8 不支持 ADD COLUMN IF NOT EXISTS）。"""
    count = bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
        ),
        {"t": TABLE, "c": column},
    ).scalar()
    return bool(count)


def upgrade() -> None:
    """存量库补列；全新库（0001 已含新字段）自动跳过。"""
    bind = op.get_bind()
    for name, ddl in COLUMNS:
        if not _has_column(bind, name):
            op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {ddl}")


def downgrade() -> None:
    """回滚：逐列删除（仅供开发环境使用）。"""
    bind = op.get_bind()
    for name, _ in reversed(COLUMNS):
        if _has_column(bind, name):
            op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {name}")
