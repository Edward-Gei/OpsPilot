"""清理早期模板/审批结构，保持新库迁移链可重复执行。"""
from alembic import op
from sqlalchemy import text

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

# 这些表属于早期模板/审批结构；当前 ORM 不再注册它们，必须只按名称清理。
LEGACY_TABLES = [
    "job_template",
    "job_template_version",
    "approval_flow_level",
    "template_step",
    "template_approval_node",
    "template_version",
]


def upgrade() -> None:
    """幂等删除旧结构；当前表结构由 0001 和后续增量迁移负责。"""
    bind = op.get_bind()
    for table in LEGACY_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    # 审批总开关下线：审批规则全部内聚到模板。
    bind.execute(text("DELETE FROM system_config WHERE cfg_key = 'approval.enabled'"))


def downgrade() -> None:
    """不恢复已移除的旧模板/审批结构。"""
    raise NotImplementedError("V2 重建式变更不提供 downgrade")
