"""审计与系统配置模型（03-数据库设计 §9）。

注意：audit_log 按月 RANGE 分区，分区 DDL 无法由 ORM 表达，
建表在 Alembic 迁移中用原生 SQL 完成；本模型仅供 ORM 读写映射。
"""
from datetime import datetime

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column


class AuditLog(Base):
    """审计日志：只 INSERT/SELECT；复合主键 (id, created_at) 以满足分区键约束。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("idx_audit_actor_time", "actor_id", "created_at"),
        Index("idx_audit_module_action", "module", "action"),
        Base.__table_args__,
    )

    id: Mapped[int] = mapped_column(UBIGINT, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DT3, primary_key=True, comment="操作时间（分区键）"
    )
    actor_id: Mapped[int | None] = mapped_column(UBIGINT, comment="操作人 ID（登录失败等可为空）")
    actor_name: Mapped[str | None] = mapped_column(String(64), comment="操作人名称快照")
    source_ip: Mapped[str | None] = mapped_column(String(45), comment="来源 IP")
    module: Mapped[str] = mapped_column(String(32), nullable=False, comment="模块：auth/cmdb/ticket/...")
    action: Mapped[str] = mapped_column(String(64), nullable=False, comment="动作：login/host.create/...")
    target_type: Mapped[str | None] = mapped_column(String(32), comment="目标对象类型")
    target_id: Mapped[str | None] = mapped_column(String(64), comment="目标对象 ID")
    target_name: Mapped[str | None] = mapped_column(String(128), comment="目标对象名称快照")
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="success", comment="success/failed")
    detail: Mapped[dict | None] = mapped_column(JSON, comment="变更明细/上下文")


class SystemConfig(Base):
    """系统配置键值表：预置键见 constants.SYSTEM_CONFIG_DEFAULTS。"""

    __tablename__ = "system_config"

    cfg_key: Mapped[str] = mapped_column(String(64), primary_key=True, comment="配置键，如 mfa.policy")
    cfg_value: Mapped[dict | None] = mapped_column(JSON, comment="配置值（统一包 {value: ...}）")
    updated_by: Mapped[int | None] = mapped_column(UBIGINT)
    updated_at: Mapped[datetime] = created_at_column()  # 首次写入即当前时间，更新由应用层显式赋值
