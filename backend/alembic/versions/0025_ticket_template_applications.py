"""建立工单模板适用应用的多对多关联。"""
from alembic import op
import sqlalchemy as sa

from app.models.base import DT3, UBIGINT

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 初始迁移复用当前 ORM 元数据时可能已建表，避免重复创建。
    if sa.inspect(op.get_bind()).has_table("ticket_template_application"):
        return
    op.create_table(
        "ticket_template_application",
        sa.Column("id", UBIGINT, primary_key=True, autoincrement=True),
        sa.Column("template_id", UBIGINT, nullable=False),
        sa.Column("app_id", UBIGINT, nullable=False),
        sa.Column("created_at", DT3, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(3)")),
        sa.UniqueConstraint("template_id", "app_id", name="uk_ticket_template_application"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_ticket_template_application_app", "ticket_template_application", ["app_id"])


def downgrade() -> None:
    op.drop_table("ticket_template_application")
