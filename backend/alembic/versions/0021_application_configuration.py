"""建立应用配置、版本和异步任务表。"""
from alembic import op
from sqlalchemy import Boolean, Column, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.models.base import DT3, UBIGINT


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

MTEXT = MEDIUMTEXT().with_variant(Text(), "sqlite")


def _table_exists(bind, table: str) -> bool:
    return bool(bind.execute(
        text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = :t"),
        {"t": table},
    ).scalar())


def _timestamps() -> list[Column]:
    return [
        Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        Column("updated_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)")),
    ]


def _create_platform_instance() -> None:
    op.create_table(
        "config_platform_instance",
        Column("id", UBIGINT, primary_key=True, autoincrement=True), Column("name", String(64), nullable=False, unique=True),
        Column("provider", String(16), nullable=False), Column("base_url", String(512), nullable=False),
        Column("credential_id", UBIGINT, nullable=False), Column("description", String(255)),
        Column("enabled", Boolean, nullable=False, default=True), Column("first_bound_at", DT3),
        Column("compatibility_version", String(64)), Column("last_probed_at", DT3),
        Column("last_probe_result", String(16)), Column("last_probe_error", String(512)), Column("created_by", UBIGINT),
        *_timestamps(), mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_platform_credential", "config_platform_instance", ["credential_id"])


def _create_config_file() -> None:
    op.create_table(
        "config_file",
        Column("id", UBIGINT, primary_key=True, autoincrement=True), Column("name", String(128), nullable=False),
        Column("description", String(512)), Column("platform_instance_id", UBIGINT, nullable=False),
        Column("locator", JSON, nullable=False), Column("locator_key", String(64), nullable=False),
        Column("content_format", String(16), nullable=False), Column("approval_role_id", UBIGINT, nullable=False),
        Column("current_version_id", UBIGINT), Column("current_snapshot_id", UBIGINT), Column("latest_snapshot_id", UBIGINT),
        Column("status", String(16), nullable=False, default="active"), Column("drift_status", String(16), nullable=False, default="clean"),
        Column("last_synced_at", DT3), Column("last_sync_error", String(512)), Column("operation_token", String(36)),
        Column("operation_kind", String(16)), Column("operation_expires_at", DT3), Column("created_by", UBIGINT),
        UniqueConstraint("platform_instance_id", "locator_key", name="uk_config_file_instance_locator"),
        *_timestamps(), mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_file_drift", "config_file", ["drift_status"])
    op.create_index("idx_config_file_approval_role", "config_file", ["approval_role_id"])
    op.create_index("idx_config_file_instance", "config_file", ["platform_instance_id"])


def _create_file_application() -> None:
    op.create_table(
        "config_file_application",
        Column("id", UBIGINT, primary_key=True, autoincrement=True), Column("config_file_id", UBIGINT, nullable=False),
        Column("application_id", UBIGINT, nullable=False), Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        UniqueConstraint("config_file_id", "application_id", name="uk_config_file_application"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_file_application_app", "config_file_application", ["application_id"])


def _create_draft() -> None:
    op.create_table(
        "config_draft", Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("config_file_id", UBIGINT, nullable=False), Column("content_enc", MTEXT, nullable=False),
        Column("base_version_id", UBIGINT), Column("base_snapshot_id", UBIGINT), Column("updated_by", UBIGINT),
        UniqueConstraint("config_file_id", name="uk_config_draft_file"),
        *_timestamps(), mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )


def _create_version() -> None:
    op.create_table(
        "config_version", Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("config_file_id", UBIGINT, nullable=False), Column("version_no", Integer, nullable=False),
        Column("source", String(24), nullable=False), Column("status", String(24), nullable=False), Column("content_enc", MTEXT, nullable=False),
        Column("base_snapshot_id", UBIGINT), Column("approval_role_id", UBIGINT), Column("submitted_by", UBIGINT),
        Column("approved_by", UBIGINT), Column("approved_at", DT3), Column("published_at", DT3),
        Column("rejected_at", DT3), Column("reject_reason", String(512)),
        UniqueConstraint("config_file_id", "version_no", name="uk_config_version_file_no"),
        *_timestamps(), mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_version_file_status", "config_version", ["config_file_id", "status"])


def _create_snapshot() -> None:
    op.create_table(
        "config_remote_snapshot", Column("id", UBIGINT, primary_key=True, autoincrement=True),
        Column("config_file_id", UBIGINT, nullable=False), Column("exists", Boolean, nullable=False),
        Column("content_enc", MTEXT), Column("provider_revision", String(255)), Column("observed_at", DT3, nullable=False),
        Column("created_at", DT3, nullable=False, server_default=text("CURRENT_TIMESTAMP(3)")),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_snapshot_file_observed", "config_remote_snapshot", ["config_file_id", "observed_at"])


def _create_task() -> None:
    op.create_table(
        "config_task", Column("id", UBIGINT, primary_key=True, autoincrement=True), Column("kind", String(16), nullable=False),
        Column("config_file_id", UBIGINT), Column("config_version_id", UBIGINT), Column("platform_instance_id", UBIGINT),
        Column("status", String(16), nullable=False, default="queued"), Column("total_count", Integer, nullable=False, default=0),
        Column("success_count", Integer, nullable=False, default=0), Column("skipped_count", Integer, nullable=False, default=0),
        Column("failed_count", Integer, nullable=False, default=0), Column("last_error", String(512)),
        Column("write_attempted", Boolean, nullable=False, server_default=text("0")),
        Column("created_by", UBIGINT, nullable=False), Column("actor_name", String(64), nullable=False), Column("source_ip", String(45)),
        Column("lease_token", String(36)), Column("lease_expires_at", DT3), Column("started_at", DT3), Column("finished_at", DT3),
        *_timestamps(), mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_task_status_created", "config_task", ["status", "created_at"])
    op.create_index("idx_config_task_file", "config_task", ["config_file_id"])


def _create_task_item() -> None:
    op.create_table(
        "config_task_item", Column("id", UBIGINT, primary_key=True, autoincrement=True), Column("task_id", UBIGINT, nullable=False),
        Column("item_key", String(64), nullable=False), Column("locator", JSON, nullable=False), Column("name", String(128)),
        Column("content_format", String(16)), Column("approval_role_id", UBIGINT), Column("application_ids", JSON, nullable=False),
        Column("config_file_id", UBIGINT), Column("status", String(16), nullable=False, default="pending"), Column("reason", String(512)),
        Column("lease_token", String(36)), Column("started_at", DT3), Column("finished_at", DT3),
        UniqueConstraint("task_id", "item_key", name="uk_config_task_item_key"),
        *_timestamps(), mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("idx_config_task_item_task_status", "config_task_item", ["task_id", "status"])


def upgrade() -> None:
    """以幂等方式补建应用配置表，适应已由 ORM 初始化的开发库。"""
    bind = op.get_bind()
    for table, creator in [
        ("config_platform_instance", _create_platform_instance), ("config_file", _create_config_file),
        ("config_file_application", _create_file_application), ("config_draft", _create_draft),
        ("config_version", _create_version), ("config_remote_snapshot", _create_snapshot),
        ("config_task", _create_task), ("config_task_item", _create_task_item),
    ]:
        if not _table_exists(bind, table):
            creator()


def downgrade() -> None:
    bind = op.get_bind()
    for table in ["config_task_item", "config_task", "config_remote_snapshot", "config_version", "config_draft", "config_file_application", "config_file", "config_platform_instance"]:
        if _table_exists(bind, table):
            op.drop_table(table)
