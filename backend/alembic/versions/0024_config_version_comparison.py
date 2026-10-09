"""冻结配置版本生成时的正式版本对照基准。"""
from alembic import op
from sqlalchemy import Column

from app.models.base import UBIGINT


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_version", Column("base_version_id", UBIGINT))
    # 旧记录按现存发布时间回填，跳过生成后才发布和未生效的候选。
    op.execute("""
        UPDATE config_version AS target
        JOIN (
            SELECT current.id, previous.id AS base_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY current.id
                       ORDER BY previous.published_at DESC, previous.version_no DESC
                   ) AS position
            FROM config_version AS current
            JOIN config_version AS previous
              ON previous.config_file_id = current.config_file_id
             AND previous.version_no < current.version_no
             AND previous.published_at IS NOT NULL
             AND previous.published_at <= current.created_at
        ) AS baseline ON baseline.id = target.id AND baseline.position = 1
        SET target.base_version_id = baseline.base_id
    """)


def downgrade() -> None:
    op.drop_column("config_version", "base_version_id")
