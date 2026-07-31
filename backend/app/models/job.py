"""作业域模型：SSH 凭据 / 工单模板四表（03-数据库设计 §4，V2 模板化重构）。

模板是"规则定义"：目标应用/步骤/策略/审批/通知/权限范围全部配置在模板内部，
无全局审批流；规则任一变更自动升版（template_version 行不可变）。
"""
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UBIGINT, created_at_column, pk_column, updated_at_column
from sqlalchemy import JSON

# MEDIUMTEXT 在 SQLite 单测下退化为 TEXT
MTEXT = MEDIUMTEXT().with_variant(Text(), "sqlite")


class Credential(Base):
    """SSH 凭据：独立管理，密文字段 AES-256-GCM 加密（_enc 后缀约定）。"""

    __tablename__ = "credential"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, comment="凭据名称")
    login_user: Mapped[str] = mapped_column(String(64), nullable=False, comment="SSH 登录账号")
    auth_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="password/private_key")
    secret_enc: Mapped[str] = mapped_column(Text, nullable=False, comment="密码或私钥密文")
    passphrase_enc: Mapped[str | None] = mapped_column(Text, comment="私钥口令密文（可空）")
    description: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class TicketTemplate(Base):
    """工单模板主表：规则定义（03-数据库设计 §4.2，PRD TPL-01）。"""

    __tablename__ = "ticket_template"
    __table_args__ = (
        Index("idx_ticket_template_jh", "job_host_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="模板名（工单标题直接使用）")
    type: Mapped[str] = mapped_column(String(16), nullable=False, comment="release/daily_ops/other")
    description: Mapped[str | None] = mapped_column(String(512))
    job_host_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="作业主机")
    exec_strategy: Mapped[dict | None] = mapped_column(
        JSON, comment="执行策略 {timeout, fail_fast}"
    )
    approval_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否需要审批")
    allow_withdraw: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="是否允许创建人撤回")
    allow_transfer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否允许转交（V1 仅存配置）")
    allow_countersign: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否允许加签（V1 仅存配置）")
    notify_rules: Mapped[list | None] = mapped_column(
        JSON, comment="通知规则 [{event,receivers,channels}]；receivers: creator/approver_role/role:<id>"
    )
    visible_role_ids: Mapped[list | None] = mapped_column(
        JSON, comment="可使用角色 id 数组；空=所有具备 ticket:write 的角色"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled", comment="enabled/disabled")
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="当前生效版本号")
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class TemplateStep(Base):
    """模板步骤：脚本内嵌，一模板多步严格串行（03-数据库设计 §4.3）。"""

    __tablename__ = "template_step"
    __table_args__ = (
        UniqueConstraint("template_id", "step_order", name="uk_template_step"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="步骤序号，从 1 开始")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="步骤名")
    script_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="shell/playbook")
    content: Mapped[str] = mapped_column(MTEXT, nullable=False, comment="脚本/Playbook 内容（含 {{ 变量 }}）")
    params_schema: Mapped[list | None] = mapped_column(
        JSON, comment="参数定义 [{name,label,default,required,fixed,description}]；fixed=true 锁定默认值"
    )
    timeout: Mapped[int] = mapped_column(Integer, nullable=False, default=600, comment="单机超时秒")
    created_at: Mapped[datetime] = created_at_column()


class TemplateApprovalNode(Base):
    """模板审批节点：每模板独立审批流，节点间依次推进（03-数据库设计 §4.4）。"""

    __tablename__ = "template_approval_node"
    __table_args__ = (
        UniqueConstraint("template_id", "node_order", name="uk_template_approval_node"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    node_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="节点序号 1..10")
    role_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="该节点审批角色")
    approve_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, default="any", comment="any(或签)/all(会签)/seq(依次)；V1 仅 any 生效"
    )
    created_at: Mapped[datetime] = created_at_column()


class TemplateVersion(Base):
    """模板版本：全量配置快照，行不可变只 INSERT（03-数据库设计 §4.5）。"""

    __tablename__ = "template_version"
    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uk_template_version"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    template_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="版本号，从 1 递增")
    snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, comment="全量配置快照（基本信息+步骤+策略+审批节点+通知+权限范围）"
    )
    changelog: Mapped[str | None] = mapped_column(String(500), comment="版本说明")
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
