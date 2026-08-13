"""作业域模型：SSH 凭据、可复用流程模板和工单模板。"""

from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UBIGINT, created_at_column, pk_column, updated_at_column

MTEXT = MEDIUMTEXT().with_variant(Text(), "sqlite")


class Credential(Base):
    """独立管理 SSH 登录凭据；模板只引用作业主机，不直接引用凭据。"""

    __tablename__ = "credential"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    login_user: Mapped[str] = mapped_column(String(64), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    passphrase_enc: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class TicketTemplate(Base):
    """工单业务入口：绑定流程并独立维护本入口的参数契约。"""

    __tablename__ = "ticket_template"
    __table_args__ = (
        Index("idx_ticket_template_jh", "job_host_id"),
        Index("idx_ticket_template_process", "process_template_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    job_host_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    process_template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    params_schema: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    generator_script: Mapped[str | None] = mapped_column(MTEXT)
    generator_timeout: Mapped[int | None] = mapped_column(Integer)
    allow_withdraw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_rules: Mapped[list | None] = mapped_column(JSON)
    visible_role_ids: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ProcessTemplate(Base):
    """可复用流程：只维护步骤、执行策略和步骤审批，不绑定参数契约。"""

    __tablename__ = "process_template"
    __table_args__ = (
        Index("idx_process_template_status", "status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    exec_strategy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ProcessStep(Base):
    """流程步骤：严格串行，每步最多绑定一个前置审批角色。"""

    __tablename__ = "process_step"
    __table_args__ = (
        UniqueConstraint("process_template_id", "step_order", name="uk_process_step"),
        Index("idx_process_step_process", "process_template_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    process_template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    script_type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(MTEXT, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    approval_role_id: Mapped[int | None] = mapped_column(UBIGINT)
