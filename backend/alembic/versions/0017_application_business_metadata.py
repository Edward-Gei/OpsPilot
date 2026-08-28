"""应用台账字段。"""
from alembic import op
from sqlalchemy import text

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

TABLE = "application"
COLUMNS: list[tuple[str, str]] = [
    ("business_line", "business_line VARCHAR(16) NULL COMMENT '所属业务线：mitrade/tradingkey' AFTER project_type"),
    ("system_name", "system_name VARCHAR(128) NULL COMMENT '所属系统' AFTER business_line"),
    ("service_level", "service_level VARCHAR(16) NULL COMMENT '服务级别：核心服务/一般服务' AFTER system_name"),
    ("ops_owner", "ops_owner VARCHAR(64) NULL COMMENT '运维负责人' AFTER service_level"),
    ("dev_owner", "dev_owner VARCHAR(64) NULL COMMENT '开发负责人' AFTER ops_owner"),
    ("repo_url", "repo_url VARCHAR(255) NULL COMMENT '代码仓库地址' AFTER dev_owner"),
    ("service_port", "service_port VARCHAR(64) NULL COMMENT '服务端口' AFTER repo_url"),
    ("cpu_quota", "cpu_quota VARCHAR(64) NULL COMMENT 'CPU 配额' AFTER service_port"),
    ("mem_quota", "mem_quota VARCHAR(64) NULL COMMENT 'MEM 配额' AFTER cpu_quota"),
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
    """补齐应用台账字段并回填两个枚举默认值。"""
    bind = op.get_bind()
    for name, ddl in COLUMNS:
        if not _has_column(bind, name):
            op.execute(f"ALTER TABLE {TABLE} ADD COLUMN {ddl}")
    op.execute("UPDATE application SET business_line = 'mitrade' WHERE business_line IS NULL")
    op.execute("UPDATE application SET service_level = '核心服务' WHERE service_level IS NULL")
    op.execute(
        "ALTER TABLE application MODIFY COLUMN business_line VARCHAR(16) NOT NULL "
        "DEFAULT 'mitrade' COMMENT '所属业务线：mitrade/tradingkey'"
    )
    op.execute(
        "ALTER TABLE application MODIFY COLUMN service_level VARCHAR(16) NOT NULL "
        "DEFAULT '核心服务' COMMENT '服务级别：核心服务/一般服务'"
    )


def downgrade() -> None:
    """开发环境回滚时逆序删除应用台账字段。"""
    bind = op.get_bind()
    for name, _ in reversed(COLUMNS):
        if _has_column(bind, name):
            op.execute(f"ALTER TABLE {TABLE} DROP COLUMN {name}")
