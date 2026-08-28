"""工单领域模型：提交时固化流程、参数和执行步骤，保证历史工单可独立查看。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column, updated_at_column
from app.models.job import MTEXT


class Ticket(Base):
    """工单主表；流程模板删除或编辑后，已创建工单仍只依赖自身快照。"""

    __tablename__ = "ticket"
    __table_args__ = (
        Index("idx_ticket_status", "status"),
        Index("idx_ticket_creator", "creator_id", "created_at"),
        Index("idx_ticket_template", "template_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON)
    job_host_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    job_host_snap: Mapped[dict] = mapped_column(JSON, nullable=False)
    process_template_id_snap: Mapped[int | None] = mapped_column(UBIGINT)
    process_name_snap: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="approving")
    interrupt_reason: Mapped[str | None] = mapped_column(String(32))
    exec_strategy_snap: Mapped[dict | None] = mapped_column(JSON)
    credential_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    flow_snap: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    allow_withdraw_snap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    creator_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()

class TicketStep(Base):
    """工单步骤快照；每条记录包含执行所需的脚本、参数和审批角色。"""

    __tablename__ = "ticket_step"
    __table_args__ = (
        UniqueConstraint("ticket_id", "step_order", name="uk_ticket_step"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name_snap: Mapped[str] = mapped_column(String(128), nullable=False)
    script_type_snap: Mapped[str] = mapped_column(String(16), nullable=False)
    content_snap: Mapped[str] = mapped_column(MTEXT, nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON)
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    approval_role_id_snap: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()


class TicketApproval(Base):
    """审批流水；同一角色任意一名成员通过即可推进步骤。"""

    __tablename__ = "ticket_approval"
    __table_args__ = (Index("idx_ticket_approval_ticket_id", "ticket_id"), Base.__table_args__)

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    role_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    approver_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    comment: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = created_at_column()

class TicketParameterPrepare(Base):
    """动态参数预生成临时结果；短期保存且不写入审计或执行日志。"""

    __tablename__ = "ticket_parameter_prepare"
    __table_args__ = (Index("idx_ticket_prepare_expires", "expires_at"), Base.__table_args__)

    id: Mapped[int] = pk_column()
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    input_params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    options: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DT3, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()
