"""工单中心域模型：工单 / 步骤快照 / 审批记录（03-数据库设计 §5，V2）。

多重快照原则：提交时固化作业主机、步骤脚本内容、审批节点、执行策略、模板版本，
后续 CMDB / 模板变更不影响已提交工单。无草稿态，提交即生效。
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column, updated_at_column
from app.models.job import MTEXT


class Ticket(Base):
    """工单主表：基于模板某版本的一次具体提交，9 态状态机（approving→queued→running→终态）。"""

    __tablename__ = "ticket"
    __table_args__ = (
        Index("idx_ticket_status", "status"),
        Index("idx_ticket_creator", "creator_id", "created_at"),
        Index("idx_ticket_template", "template_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, comment="工单号 T20260727-0001")
    template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="源模板（溯源）")
    template_version_snap: Mapped[int] = mapped_column(Integer, nullable=False, comment="提交时模板版本号快照")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="提交时模板名快照（标题=模板名）")
    type: Mapped[str] = mapped_column(String(16), nullable=False, comment="模板类型快照 release/daily_ops/other")
    params: Mapped[dict | None] = mapped_column(JSON, comment="提交人填写的汇总参数 {name: value}")
    job_host_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="作业主机")
    job_host_snap: Mapped[dict] = mapped_column(JSON, nullable=False, comment="作业主机快照 {id,name,ip,ssh_port,workdir}")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="approving",
        comment="过程态 approving/queued/running/paused + 终态 success/failed/rejected/cancelled/interrupted",
    )
    interrupt_reason: Mapped[str | None] = mapped_column(
        String(32), comment="终态为 interrupted 时的起因：user_abort/system_crash/worker_lost"
    )
    exec_strategy_snap: Mapped[dict | None] = mapped_column(JSON, comment="执行策略快照（来自模板）")
    flow_snap: Mapped[list | None] = mapped_column(
        JSON, comment="审批节点快照 [{node,role_id,role_name,approve_mode}]；免审为 []"
    )
    allow_withdraw_snap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否允许撤回（来自模板）")
    current_node: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="当前审批节点（0=未进入/已结束）")
    creator_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="创建人")
    submitted_at: Mapped[datetime | None] = mapped_column(DT3, comment="提交时间")
    finished_at: Mapped[datetime | None] = mapped_column(DT3, comment="终态时间")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class TicketStep(Base):
    """步骤快照：提交时由模板步骤固化，参数为固定值+提交人填写合并后的生效值。"""

    __tablename__ = "ticket_step"
    __table_args__ = (
        UniqueConstraint("ticket_id", "step_order", name="uk_ticket_step"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="步骤序号，从 1 开始")
    step_name_snap: Mapped[str] = mapped_column(String(128), nullable=False, comment="步骤名快照")
    script_type_snap: Mapped[str] = mapped_column(String(16), nullable=False, comment="shell/playbook")
    content_snap: Mapped[str] = mapped_column(MTEXT, nullable=False, comment="提交时脚本内容快照")
    params: Mapped[dict | None] = mapped_column(JSON, comment="本步骤生效参数值 {name: value}")
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=600, comment="步骤超时秒数")
    created_at: Mapped[datetime] = created_at_column()


class TicketApproval(Base):
    """审批记录：每次通过/驳回一条，只 INSERT。"""

    __tablename__ = "ticket_approval"
    __table_args__ = (
        Index("idx_ticket_approval_ticket_id", "ticket_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    ticket_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    node_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="审批节点序号")
    role_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="审批角色（快照自 flow_snap）")
    approver_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="实际审批人")
    action: Mapped[str] = mapped_column(String(16), nullable=False, comment="approve/reject")
    comment: Mapped[str | None] = mapped_column(String(512), comment="审批意见")
    created_at: Mapped[datetime] = created_at_column()
