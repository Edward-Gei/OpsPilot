"""建立 DNS Zone 与记录集快照表。"""
from alembic import op
from sqlalchemy import Boolean, Column, Integer, JSON, String, UniqueConstraint, text

from app.models.base import DT3, UBIGINT


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    """兼容当前 ORM 元数据已建表的数据库。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
    )


def _create_dns_zone() -> None:
    op.create_table(
        "dns_zone",
        Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("provider", String(32), nullable=False),
        Column("remote_zone_id", String(255), nullable=False),
        Column("zone_name", String(253), nullable=False),
        Column("credential_id", UBIGINT, nullable=False),
        Column("description", String(255)),
        Column("record_count", Integer, nullable=False, default=0),
        Column("sync_status", String(16), nullable=False, default="success"),
        Column("last_synced_at", DT3),
        Column("last_sync_error", String(512)),
        Column("operation_token", String(36)),
        Column("operation_kind", String(32)),
        Column("operation_expires_at", DT3),
        Column("created_by", UBIGINT),
        Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        Column(
            "updated_at",
            DT3,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        ),
        UniqueConstraint("provider", "remote_zone_id", name="uk_dns_zone_provider_remote"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_dns_zone_credential", "dns_zone", ["credential_id"])
    op.create_index("idx_dns_zone_sync_status", "dns_zone", ["sync_status"])


def _create_dns_record_set() -> None:
    op.create_table(
        "dns_record_set",
        Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("zone_id", UBIGINT, nullable=False),
        Column("record_key", String(512), nullable=False),
        Column("record_name", String(253), nullable=False),
        Column("record_type", String(16), nullable=False),
        Column("ttl", Integer),
        Column("values", JSON, nullable=False, default=list),
        Column("read_only", Boolean, nullable=False, default=False),
        Column("read_only_reason", String(255)),
        Column("provider_meta", JSON, nullable=False, default=dict),
        Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        Column(
            "updated_at",
            DT3,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        ),
        UniqueConstraint("zone_id", "record_key", name="uk_dns_record_set_zone_key"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_dns_record_set_zone", "dns_record_set", ["zone_id"])
    op.create_index(
        "idx_dns_record_set_zone_name_type",
        "dns_record_set",
        ["zone_id", "record_name", "record_type"],
    )


def upgrade() -> None:
    """仅补建缺失的 DNS 快照表，避免新库重复建表。"""
    bind = op.get_bind()
    if not _table_exists(bind, "dns_zone"):
        _create_dns_zone()
    if not _table_exists(bind, "dns_record_set"):
        _create_dns_record_set()


def downgrade() -> None:
    """按记录集、Zone 的依赖顺序删除已存在表。"""
    bind = op.get_bind()
    if _table_exists(bind, "dns_record_set"):
        op.drop_table("dns_record_set")
    if _table_exists(bind, "dns_zone"):
        op.drop_table("dns_zone")
