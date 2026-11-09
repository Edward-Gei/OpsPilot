"""数据库台账、持久执行队列、短期结果与独立审计；全部关系由业务事务维护。"""
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, DT3, UBIGINT, created_at_column, pk_column, updated_at_column

MTEXT = MEDIUMTEXT().with_variant(Text(), "sqlite")


class DatabaseInstance(Base):
    """统一入口台账；登记信息不参与连接身份版本。"""
    __tablename__ = "database_instance"
    __table_args__ = (
        Index("idx_database_instance_credential", "credential_id"),
        Index("idx_database_instance_engine_enabled", "engine", "enabled"),
        Index("idx_database_instance_environment", "environment"), Base.__table_args__,
    )
    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True)
    engine: Mapped[str] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    credential_id: Mapped[int | None] = mapped_column(UBIGINT)
    business_line: Mapped[str | None] = mapped_column(String(16))
    cluster_name: Mapped[str | None] = mapped_column(String(128))
    cluster_role: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(128))
    connection_options: Mapped[dict] = mapped_column(JSON, default=dict)
    config_revision: Mapped[int] = mapped_column(Integer, default=1)
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    last_probe_at: Mapped[datetime | None] = mapped_column(DT3)
    last_probe_status: Mapped[str] = mapped_column(String(16), default="untested")
    last_probe_message: Mapped[str | None] = mapped_column(String(512))
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DatabaseInstanceApplication(Base):
    """应用业务使用关系，独立于应用的部署主机。"""
    __tablename__ = "database_instance_application"
    __table_args__ = (UniqueConstraint("instance_id", "app_id", name="uk_database_instance_application"),
                      Index("idx_database_instance_application_app", "app_id"), Base.__table_args__)
    id: Mapped[int] = pk_column()
    instance_id: Mapped[int] = mapped_column(UBIGINT)
    app_id: Mapped[int] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()


class DatabaseInstanceHost(Base):
    """实例承载主机；云入口可以没有主机。"""
    __tablename__ = "database_instance_host"
    __table_args__ = (UniqueConstraint("instance_id", "host_id", name="uk_database_instance_host"),
                      Index("idx_database_instance_host_host", "host_id"), Base.__table_args__)
    id: Mapped[int] = pk_column()
    instance_id: Mapped[int] = mapped_column(UBIGINT)
    host_id: Mapped[int] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()


class DatabaseRoleInstance(Base):
    """角色显式查询授权；管理员功能权限不提供实例范围豁免。"""
    __tablename__ = "database_role_instance"
    __table_args__ = (UniqueConstraint("role_id", "instance_id", name="uk_database_role_instance"),
                      Index("idx_database_role_instance_instance", "instance_id"), Base.__table_args__)
    id: Mapped[int] = pk_column()
    role_id: Mapped[int] = mapped_column(UBIGINT)
    instance_id: Mapped[int] = mapped_column(UBIGINT)
    granted_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()


class DatabaseTask(Base):
    """任务与执行现场；租约失效不能据此重放或释放未知目标操作。"""
    __tablename__ = "database_task"
    __table_args__ = (
        Index("idx_database_task_claim", "status", "created_at"),
        Index("idx_database_task_owner", "owner_id", "created_at"),
        Index("idx_database_task_instance", "instance_id", "status"),
        Index("idx_database_task_lease", "lease_expires_at"),
        Index("idx_database_task_finished", "finished_at"), Base.__table_args__,
    )
    id: Mapped[int] = pk_column()
    owner_id: Mapped[int] = mapped_column(UBIGINT)
    actor_name: Mapped[str] = mapped_column(String(64))
    source_ip: Mapped[str | None] = mapped_column(String(45))
    instance_id: Mapped[int] = mapped_column(UBIGINT)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    engine: Mapped[str] = mapped_column(String(16))
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    target_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_text_enc: Mapped[str | None] = mapped_column(MTEXT)
    executed_statement_enc: Mapped[str | None] = mapped_column(MTEXT)
    submitted_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    submitted_sha256: Mapped[str] = mapped_column(String(64), default="")
    operation_summary: Mapped[str] = mapped_column(String(512), default="")
    prepared_operation_enc: Mapped[str | None] = mapped_column(MTEXT)
    policy_snapshot: Mapped[dict | None] = mapped_column(JSON)
    config_revision: Mapped[int] = mapped_column(Integer)
    credential_id: Mapped[int | None] = mapped_column(UBIGINT)
    credential_updated_at: Mapped[datetime] = mapped_column(DT3)
    queue_deadline_at: Mapped[datetime | None] = mapped_column(DT3)
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    deadline_at: Mapped[datetime | None] = mapped_column(DT3)
    target_attempted_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    executor_id: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DT3)
    backend_ref: Mapped[dict | None] = mapped_column(JSON)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DT3)
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    stop_confirmation: Mapped[dict | None] = mapped_column(JSON)
    quota_snapshot: Mapped[dict | None] = mapped_column(JSON)
    quota_released: Mapped[bool] = mapped_column(Boolean, default=False)
    result_reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    result_summary: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DatabaseResult(Base):
    """所有分块写完才发布 ready，过期立即拒读。"""
    __tablename__ = "database_result"
    __table_args__ = (Index("idx_database_result_expiry", "expires_at"),
                      Index("idx_database_result_owner", "owner_id", "ready"), Base.__table_args__)
    id: Mapped[int] = pk_column()
    task_id: Mapped[int] = mapped_column(UBIGINT, unique=True)
    owner_id: Mapped[int] = mapped_column(UBIGINT)
    instance_id: Mapped[int] = mapped_column(UBIGINT)
    ready: Mapped[bool] = mapped_column(Boolean, default=False)
    manifest_enc: Mapped[str] = mapped_column(MTEXT)
    returned_rows: Mapped[int] = mapped_column(Integer, default=0)
    returned_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    stored_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    truncate_reason: Mapped[str | None] = mapped_column(String(64))
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    expires_at: Mapped[datetime | None] = mapped_column(DT3)
    storage_released: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DatabaseResultChunk(Base):
    """密文块只含二维 rows，列定义由 manifest 保存一次。"""
    __tablename__ = "database_result_chunk"
    __table_args__ = (UniqueConstraint("result_id", "chunk_no", name="uk_database_result_chunk"),
                      Index("idx_database_result_chunk_rows", "result_id", "start_row"), Base.__table_args__)
    id: Mapped[int] = pk_column()
    result_id: Mapped[int] = mapped_column(UBIGINT)
    chunk_no: Mapped[int] = mapped_column(Integer)
    start_row: Mapped[int] = mapped_column(Integer)
    row_count: Mapped[int] = mapped_column(Integer)
    payload_enc: Mapped[str] = mapped_column(MTEXT)
    stored_bytes: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at_column()


class DatabaseAuditEvent(Base):
    """追加式独立证据，不依赖任务或结果的存续。"""
    __tablename__ = "database_audit_event"
    __table_args__ = (
        Index("idx_database_audit_task", "task_id", "id"),
        Index("idx_database_audit_type", "task_id", "event_type"),
        Index("idx_database_audit_terminal", "terminal_at"),
        Index("idx_database_audit_actor", "actor_id", "created_at"),
        Index("idx_database_audit_instance", "instance_id", "created_at"), Base.__table_args__,
    )
    id: Mapped[int] = pk_column()
    task_id: Mapped[int] = mapped_column(UBIGINT)
    event_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[int | None] = mapped_column(UBIGINT)
    actor_name: Mapped[str | None] = mapped_column(String(64))
    source_ip: Mapped[str | None] = mapped_column(String(45))
    instance_id: Mapped[int | None] = mapped_column(UBIGINT)
    instance_name: Mapped[str | None] = mapped_column(String(128))
    engine: Mapped[str | None] = mapped_column(String(16))
    target_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    credential_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    submitted_text_enc: Mapped[str | None] = mapped_column(MTEXT)
    executed_statement_enc: Mapped[str | None] = mapped_column(MTEXT)
    submitted_bytes: Mapped[int | None] = mapped_column(BigInteger)
    submitted_sha256: Mapped[str | None] = mapped_column(String(64))
    validation: Mapped[dict | None] = mapped_column(JSON)
    stop_confirmation: Mapped[dict | None] = mapped_column(JSON)
    result_summary: Mapped[dict | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    terminal_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()


class DatabaseQuotaBucket(Base):
    """跨副本共享计量与维护租约，按 global→instance→user 锁序更新。"""
    __tablename__ = "database_quota_bucket"
    __table_args__ = (UniqueConstraint("scope", "scope_key", name="uk_database_quota_bucket"), Base.__table_args__)
    id: Mapped[int] = pk_column()
    scope: Mapped[str] = mapped_column(String(16))
    scope_key: Mapped[str] = mapped_column(String(128))
    active_count: Mapped[int] = mapped_column(Integer, default=0)
    data_active_count: Mapped[int] = mapped_column(Integer, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    payload_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    target_sockets: Mapped[int] = mapped_column(BigInteger, default=0)
    control_sockets: Mapped[int] = mapped_column(BigInteger, default=0)
    control_operations: Mapped[int] = mapped_column(Integer, default=0)
    owner: Mapped[str | None] = mapped_column(String(64))
    token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
