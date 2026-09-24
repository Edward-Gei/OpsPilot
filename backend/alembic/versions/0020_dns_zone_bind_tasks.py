"""建立 Zone 异步绑定任务表。"""
from alembic import op
from sqlalchemy import Column, Integer, String, UniqueConstraint, text

from app.models.base import DT3, UBIGINT


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _table_exists(bind, table: str) -> bool:
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            ),
            {"t": table},
        ).scalar()
    )


def _create_dns_zone_bind_task() -> None:
    op.create_table(
        "dns_zone_bind_task",
        Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("provider", String(32), nullable=False),
        Column("credential_id", UBIGINT, nullable=False),
        Column("status", String(16), nullable=False, default="queued"),
        Column("total_count", Integer, nullable=False, default=0),
        Column("success_count", Integer, nullable=False, default=0),
        Column("skipped_count", Integer, nullable=False, default=0),
        Column("failed_count", Integer, nullable=False, default=0),
        Column("last_error", String(512)),
        Column("created_by", UBIGINT, nullable=False),
        Column("actor_name", String(64), nullable=False),
        Column("source_ip", String(45)),
        Column("lease_token", String(36)),
        Column("lease_expires_at", DT3),
        Column("started_at", DT3),
        Column("finished_at", DT3),
        Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        Column(
            "updated_at",
            DT3,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_dns_zone_bind_task_status_created", "dns_zone_bind_task", ["status", "created_at"])
    op.create_index("idx_dns_zone_bind_task_creator", "dns_zone_bind_task", ["created_by"])


def _create_dns_zone_bind_task_item() -> None:
    op.create_table(
        "dns_zone_bind_task_item",
        Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("task_id", UBIGINT, nullable=False),
        Column("remote_zone_id", String(255), nullable=False),
        Column("zone_name", String(253)),
        Column("description", String(255)),
        Column("zone_id", UBIGINT),
        Column("status", String(16), nullable=False, default="pending"),
        Column("reason", String(512)),
        Column("lease_token", String(36)),
        Column("started_at", DT3),
        Column("finished_at", DT3),
        Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        Column(
            "updated_at",
            DT3,
            nullable=False,
            server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"),
        ),
        UniqueConstraint("task_id", "remote_zone_id", name="uk_dns_zone_bind_task_item_task_remote"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index(
        "idx_dns_zone_bind_task_item_task_status",
        "dns_zone_bind_task_item",
        ["task_id", "status"],
    )


def upgrade() -> None:
    """仅补建缺失的任务表，兼容当前 ORM 元数据已建表的数据库。"""
    bind = op.get_bind()
    if not _table_exists(bind, "dns_zone_bind_task"):
        _create_dns_zone_bind_task()
    if not _table_exists(bind, "dns_zone_bind_task_item"):
        _create_dns_zone_bind_task_item()


def downgrade() -> None:
    """按子项、任务顺序删除 DNS 异步绑定任务表。"""
    bind = op.get_bind()
    if _table_exists(bind, "dns_zone_bind_task_item"):
        op.drop_table("dns_zone_bind_task_item")
    if _table_exists(bind, "dns_zone_bind_task"):
        op.drop_table("dns_zone_bind_task")
