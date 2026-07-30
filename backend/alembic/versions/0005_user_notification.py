"""NOTIFY-06 站内通知：新增 user_notification 站内信表。

事件发射（notify_service.emit）时按收件人用户名解析为有效用户、每人落一行，
顶栏铃铛未读角标 + 下拉面板消费；无发送/重试概念，不受渠道启用/事件映射影响。

幂等策略：先查 information_schema 判断表是否存在再按 ORM 元数据建表
（全新库执行 0001 create_all 时已含该表）。
"""
from alembic import op
from sqlalchemy import text

from app.models import Base

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLE = "user_notification"


def upgrade() -> None:
    """存量库补建 user_notification 表；全新库（0001 已建）跳过。"""
    bind = op.get_bind()
    exists = bind.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": TABLE},
    ).scalar()
    if not exists:
        Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[TABLE]])


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
