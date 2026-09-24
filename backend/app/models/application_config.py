"""应用配置领域模型：本地管理配置、版本与异步远端操作。"""
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, DT3, UBIGINT, created_at_column, pk_column, updated_at_column


MTEXT = MEDIUMTEXT().with_variant(Text(), "sqlite")


class ConfigPlatformInstance(Base):
    """可复用的平台连接边界，首次绑定后锁定平台和地址。"""

    __tablename__ = "config_platform_instance"
    __table_args__ = (Index("idx_config_platform_credential", "credential_id"), Base.__table_args__)

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    credential_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_bound_at: Mapped[datetime | None] = mapped_column(DT3)
    compatibility_version: Mapped[str | None] = mapped_column(String(64))
    last_probed_at: Mapped[datetime | None] = mapped_column(DT3)
    last_probe_result: Mapped[str | None] = mapped_column(String(16))
    last_probe_error: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ConfigFile(Base):
    """一个精确远端定位器对应一个本地配置文件。"""

    __tablename__ = "config_file"
    __table_args__ = (
        UniqueConstraint("platform_instance_id", "locator_key", name="uk_config_file_instance_locator"),
        Index("idx_config_file_drift", "drift_status"),
        Index("idx_config_file_approval_role", "approval_role_id"),
        Index("idx_config_file_instance", "platform_instance_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    platform_instance_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    locator_key: Mapped[str] = mapped_column(String(64), nullable=False)
    content_format: Mapped[str] = mapped_column(String(16), nullable=False)
    approval_role_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    current_version_id: Mapped[int | None] = mapped_column(UBIGINT)
    current_snapshot_id: Mapped[int | None] = mapped_column(UBIGINT)
    latest_snapshot_id: Mapped[int | None] = mapped_column(UBIGINT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    drift_status: Mapped[str] = mapped_column(String(16), nullable=False, default="clean")
    last_synced_at: Mapped[datetime | None] = mapped_column(DT3)
    last_sync_error: Mapped[str | None] = mapped_column(String(512))
    operation_token: Mapped[str | None] = mapped_column(String(36))
    operation_kind: Mapped[str | None] = mapped_column(String(16))
    operation_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ConfigFileApplication(Base):
    """配置文件与 CMDB 应用的多对多上下文关联。"""

    __tablename__ = "config_file_application"
    __table_args__ = (
        UniqueConstraint("config_file_id", "application_id", name="uk_config_file_application"),
        Index("idx_config_file_application_app", "application_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    config_file_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    application_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ConfigDraft(Base):
    """配置文件唯一的可编辑草稿，正文始终加密保存。"""

    __tablename__ = "config_draft"
    __table_args__ = (UniqueConstraint("config_file_id", name="uk_config_draft_file"), Base.__table_args__)

    id: Mapped[int] = pk_column()
    config_file_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    content_enc: Mapped[str] = mapped_column(MTEXT, nullable=False)
    base_version_id: Mapped[int | None] = mapped_column(UBIGINT)
    base_snapshot_id: Mapped[int | None] = mapped_column(UBIGINT)
    updated_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ConfigVersion(Base):
    """候选、已批准和正式配置版本，内容不可在版本上原地修改。"""

    __tablename__ = "config_version"
    __table_args__ = (
        UniqueConstraint("config_file_id", "version_no", name="uk_config_version_file_no"),
        Index("idx_config_version_file_status", "config_file_id", "status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    config_file_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    content_enc: Mapped[str] = mapped_column(MTEXT, nullable=False)
    base_snapshot_id: Mapped[int | None] = mapped_column(UBIGINT)
    approval_role_id: Mapped[int | None] = mapped_column(UBIGINT)
    submitted_by: Mapped[int | None] = mapped_column(UBIGINT)
    approved_by: Mapped[int | None] = mapped_column(UBIGINT)
    approved_at: Mapped[datetime | None] = mapped_column(DT3)
    published_at: Mapped[datetime | None] = mapped_column(DT3)
    rejected_at: Mapped[datetime | None] = mapped_column(DT3)
    reject_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ConfigRemoteSnapshot(Base):
    """一次远端读取的加密快照，用于基线和漂移比较。"""

    __tablename__ = "config_remote_snapshot"
    __table_args__ = (Index("idx_config_snapshot_file_observed", "config_file_id", "observed_at"), Base.__table_args__)

    id: Mapped[int] = pk_column()
    config_file_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    exists: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_enc: Mapped[str | None] = mapped_column(MTEXT)
    provider_revision: Mapped[str | None] = mapped_column(String(255))
    observed_at: Mapped[datetime] = mapped_column(DT3, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ConfigTask(Base):
    """接入、同步和发布的持久化任务，Worker 通过租约独占处理。"""

    __tablename__ = "config_task"
    __table_args__ = (
        Index("idx_config_task_status_created", "status", "created_at"),
        Index("idx_config_task_file", "config_file_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    config_file_id: Mapped[int | None] = mapped_column(UBIGINT)
    config_version_id: Mapped[int | None] = mapped_column(UBIGINT)
    confirmed_snapshot_id: Mapped[int | None] = mapped_column(UBIGINT)
    platform_instance_id: Mapped[int | None] = mapped_column(UBIGINT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(512))
    write_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DT3)
    created_by: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(45))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ConfigTaskItem(Base):
    """批量接入任务的独立资源状态，单项失败不影响其它选择。"""

    __tablename__ = "config_task_item"
    __table_args__ = (
        UniqueConstraint("task_id", "item_key", name="uk_config_task_item_key"),
        Index("idx_config_task_item_task_status", "task_id", "status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    task_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    content_format: Mapped[str | None] = mapped_column(String(16))
    approval_role_id: Mapped[int | None] = mapped_column(UBIGINT)
    application_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    config_file_id: Mapped[int | None] = mapped_column(UBIGINT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(String(512))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
