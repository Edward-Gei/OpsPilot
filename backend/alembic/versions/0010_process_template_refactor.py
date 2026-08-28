"""流程模板重构：拆分可复用流程并移除模板内嵌规则。"""

from alembic import op
import sqlalchemy as sa

from app.models import Base

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """开发测试阶段重建受影响表，不迁移旧模板规则或历史工单数据。"""
    bind = op.get_bind()
    for table in (
        "ticket_parameter_prepare", "process_step", "process_template",
        "ticket_approval", "ticket_step", "ticket", "ticket_template",
        "template_step", "template_approval_node", "template_version",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    names = ["ticket_template", "process_template", "process_step", "ticket",
             "ticket_step", "ticket_approval", "ticket_parameter_prepare"]
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[n] for n in names])


def downgrade() -> None:
    """开发阶段不提供旧版规则回滚。"""
    pass
