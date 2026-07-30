"""初始迁移：创建全部业务表 + audit_log 按月分区表。

设计依据：03-数据库设计。
实现说明：
    - 普通表直接复用 ORM 元数据 create_all，避免手写 24 张表 DDL 重复（对齐冲突声明 C1：迁移只建表）
    - audit_log 分区语法 ORM 无法表达，使用原生 SQL；预建 2026-01 ~ 2028-12 月分区 + pmax 兜底
    - 分区滚动创建/按保留期 DROP PARTITION 属 M7 任务
"""
from datetime import date

from alembic import op

from app.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

AUDIT_TABLE = "audit_log"


def _month_partitions(start: date, end: date) -> str:
    """生成按月 RANGE COLUMNS 分区子句：p202601 VALUES LESS THAN ('2026-02-01') ..."""
    parts: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
        parts.append(f"PARTITION p{year}{month:02d} VALUES LESS THAN ('{ny}-{nm:02d}-01')")
        year, month = ny, nm
    parts.append("PARTITION pmax VALUES LESS THAN (MAXVALUE)")
    return ",\n  ".join(parts)


def _audit_log_ddl() -> str:
    """audit_log 建表 DDL：复合主键 (id, created_at) 满足 MySQL 分区键必须包含在主键中的约束。"""
    return f"""
CREATE TABLE {AUDIT_TABLE} (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  created_at DATETIME(3) NOT NULL COMMENT '操作时间（分区键）',
  actor_id BIGINT UNSIGNED NULL COMMENT '操作人 ID（登录失败等可为空）',
  actor_name VARCHAR(64) NULL COMMENT '操作人名称快照',
  source_ip VARCHAR(45) NULL COMMENT '来源 IP',
  module VARCHAR(32) NOT NULL COMMENT '模块：auth/cmdb/ticket/...',
  action VARCHAR(64) NOT NULL COMMENT '动作：login/host.create/...',
  target_type VARCHAR(32) NULL COMMENT '目标对象类型',
  target_id VARCHAR(64) NULL COMMENT '目标对象 ID',
  target_name VARCHAR(128) NULL COMMENT '目标对象名称快照',
  result VARCHAR(16) NOT NULL DEFAULT 'success' COMMENT 'success/failed',
  detail JSON NULL COMMENT '变更明细/上下文',
  PRIMARY KEY (id, created_at),
  KEY idx_audit_actor_time (actor_id, created_at),
  KEY idx_audit_module_action (module, action)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
PARTITION BY RANGE COLUMNS(created_at) (
  {_month_partitions(date(2026, 1, 1), date(2028, 12, 1))}
)
"""


def upgrade() -> None:
    """建表：ORM 元数据建普通表，原生 SQL 建分区审计表。"""
    bind = op.get_bind()
    normal_tables = [t for name, t in Base.metadata.tables.items() if name != AUDIT_TABLE]
    Base.metadata.create_all(bind=bind, tables=normal_tables)
    op.execute(_audit_log_ddl())


def downgrade() -> None:
    """全量回滚：删除所有表（仅供开发环境使用）。"""
    op.execute(f"DROP TABLE IF EXISTS {AUDIT_TABLE}")
    normal_tables = [t for name, t in Base.metadata.tables.items() if name != AUDIT_TABLE]
    Base.metadata.drop_all(bind=op.get_bind(), tables=normal_tables)
