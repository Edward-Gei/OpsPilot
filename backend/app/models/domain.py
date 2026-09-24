"""DNS Zone 与记录集快照模型：保存供应商资源的本地状态。"""
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, DT3, UBIGINT, created_at_column, pk_column, updated_at_column


class DnsZone(Base):
    """DNS 托管 Zone 快照，凭据仅以 ID 关联。"""

    __tablename__ = "dns_zone"
    __table_args__ = (
        UniqueConstraint("provider", "remote_zone_id", name="uk_dns_zone_provider_remote"),
        Index("idx_dns_zone_credential", "credential_id"),
        Index("idx_dns_zone_sync_status", "sync_status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_zone_id: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_name: Mapped[str] = mapped_column(String(253), nullable=False)
    credential_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    last_synced_at: Mapped[datetime | None] = mapped_column(DT3)
    last_sync_error: Mapped[str | None] = mapped_column(String(512))
    operation_token: Mapped[str | None] = mapped_column(String(36))
    operation_kind: Mapped[str | None] = mapped_column(String(32))
    operation_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DnsRecordSet(Base):
    """Zone 内的归一化 DNS 记录集快照，不建立物理外键。"""

    __tablename__ = "dns_record_set"
    __table_args__ = (
        UniqueConstraint("zone_id", "record_key", name="uk_dns_record_set_zone_key"),
        Index("idx_dns_record_set_zone", "zone_id"),
        Index("idx_dns_record_set_zone_name_type", "zone_id", "record_name", "record_type"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    zone_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    record_name: Mapped[str] = mapped_column(String(253), nullable=False)
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    ttl: Mapped[int | None] = mapped_column(Integer)
    values: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_only_reason: Mapped[str | None] = mapped_column(String(255))
    provider_meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DnsZoneBindTask(Base):
    """Zone 批量绑定任务：请求快速返回，Worker 异步生成快照。"""

    __tablename__ = "dns_zone_bind_task"
    __table_args__ = (
        Index("idx_dns_zone_bind_task_status_created", "status", "created_at"),
        Index("idx_dns_zone_bind_task_creator", "created_by"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    credential_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    actor_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ip: Mapped[str | None] = mapped_column(String(45))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DnsZoneBindTaskItem(Base):
    """批量绑定任务中的单个 Zone，保存可恢复的逐项结果。"""

    __tablename__ = "dns_zone_bind_task_item"
    __table_args__ = (
        UniqueConstraint("task_id", "remote_zone_id", name="uk_dns_zone_bind_task_item_task_remote"),
        Index("idx_dns_zone_bind_task_item_task_status", "task_id", "status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    task_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    remote_zone_id: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_name: Mapped[str | None] = mapped_column(String(253))
    description: Mapped[str | None] = mapped_column(String(255))
    zone_id: Mapped[int | None] = mapped_column(UBIGINT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(String(512))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
