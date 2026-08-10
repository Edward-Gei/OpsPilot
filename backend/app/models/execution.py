"""执行域模型：执行实例 / 步骤（03-数据库设计 §7）。"""
from datetime import datetime

from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column


class Execution(Base):
    """执行实例：审批通过后自动创建并入队（PRD 决策 D9，一工单一执行）。"""

    __tablename__ = "execution"
    __table_args__ = (
        Index("idx_execution_ticket_id", "ticket_id"),
        Index("idx_execution_status", "status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", comment="执行状态")
    total_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="步骤总数")
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
    created_at: Mapped[datetime] = created_at_column()


class ExecutionStep(Base):
    """步骤执行状态：串行推进，失败中断后续步骤。"""

    __tablename__ = "execution_step"
    __table_args__ = (
        UniqueConstraint("execution_id", "step_order", name="uk_execution_step"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    execution_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    ticket_step_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="对应工单步骤快照")
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", comment="步骤状态")
    exit_code: Mapped[int | None] = mapped_column(Integer, comment="退出码")
    error_summary: Mapped[str | None] = mapped_column(String(512), comment="失败摘要")
    started_at: Mapped[datetime | None] = mapped_column(DT3)
    finished_at: Mapped[datetime | None] = mapped_column(DT3)
