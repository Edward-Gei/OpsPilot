"""V2 工单模板化重构：重建模板域与工单域表（决策：重建式变更，不做数据迁移）。

变更内容（03-数据库设计 v2.0）：
    - 删除旧表：job_template / job_template_version / approval_flow_level
    - 重建模板四表：ticket_template / template_step / template_approval_node / template_version
    - 重建工单四表：ticket / ticket_host / ticket_step / ticket_approval（结构变更，历史数据不保留）
    - 清理 system_config 中已废弃的 approval.enabled 键

实现说明：全部 DROP IF EXISTS 后按 ORM 元数据重建，全新库（0001 已按新结构建表）
与存量库执行结果一致。
"""
from alembic import op
from sqlalchemy import text

from app.models import Base

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# 旧结构表（V2 移除，不再存在于 ORM 元数据）
LEGACY_TABLES = ["job_template", "job_template_version", "approval_flow_level"]

# V2 重建表（存在于 ORM 元数据；顺序无外键依赖，仅逻辑关联）
REBUILD_TABLES = [
    "ticket_template",
    "template_step",
    "template_approval_node",
    "template_version",
    "ticket",
    "ticket_host",
    "ticket_step",
    "ticket_approval",
]


def upgrade() -> None:
    """删旧表 + 重建 V2 表；幂等（全新库重建结果与 0001 相同）。"""
    bind = op.get_bind()
    for table in LEGACY_TABLES + REBUILD_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    Base.metadata.create_all(
        bind=bind, tables=[Base.metadata.tables[t] for t in REBUILD_TABLES]
    )
    # 审批总开关下线：审批规则全部内聚到模板
    bind.execute(text("DELETE FROM system_config WHERE cfg_key = 'approval.enabled'"))


def downgrade() -> None:
    """不支持回滚到全局审批流结构（重建式变更，仅供开发环境重置后重跑 0001）。"""
    raise NotImplementedError("V2 重建式变更不提供 downgrade")
