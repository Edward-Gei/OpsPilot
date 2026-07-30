"""个人访问密钥：新增 api_token 表。

头像菜单「访问密钥」自助管理个人 API Token（`Bearer opsp_xxx` 直调平台 API），
库中仅存 SHA-256 哈希，明文只在创建时返回一次。

幂等策略：先查 information_schema 判断表是否存在再按 ORM 元数据建表
（全新库执行 0001 create_all 时已含该表）。
"""
from alembic import op
from sqlalchemy import text

from app.models import Base

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLE = "api_token"


def upgrade() -> None:
    """存量库补建 api_token 表；全新库（0001 已建）跳过。"""
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
