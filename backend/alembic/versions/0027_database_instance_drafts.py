"""Excel 台账草稿不绑定凭据，拒绝任务同步保留空凭据身份。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("database_instance", "database_task"):
        op.alter_column(table, "credential_id", existing_type=mysql.BIGINT(unsigned=True), nullable=True)


def downgrade():
    # 不伪造凭据或删除草稿；有空凭据证据时阻止有损降级。
    bind = op.get_bind()
    for table in ("database_instance", "database_task"):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE credential_id IS NULL LIMIT 1")).first():
            raise RuntimeError("仍有无凭据实例或任务，不能回退凭据必填约束")
    for table in ("database_instance", "database_task"):
        op.alter_column(table, "credential_id", existing_type=mysql.BIGINT(unsigned=True), nullable=False)
