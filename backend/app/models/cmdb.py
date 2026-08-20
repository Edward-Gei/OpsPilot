"""CMDB 域模型：主机 / 应用 / 关联 / 作业主机（03-数据库设计 §4）。"""
from datetime import datetime

from sqlalchemy import Boolean, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column, updated_at_column


class Host(Base):
    """主机表：手工录入 / Excel 导入；凭据不在 CMDB（PRD 决策 D1）。"""

    __tablename__ = "host"
    __table_args__ = (
        Index("idx_host_environment", "environment"),
        Index("idx_host_status", "status"),
        Index("idx_host_platform_region", "platform", "region"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    hostname: Mapped[str] = mapped_column(String(128), nullable=False, comment="主机名")
    ip: Mapped[str] = mapped_column(String(45), unique=True, nullable=False, comment="管理 IP（兼容 IPv6 长度）")
    project: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mitrade", comment="项目：mitrade/tradingkey"
    )
    public_ip: Mapped[str | None] = mapped_column(String(45), comment="公网 IP（兼容 IPv6 长度）")
    ri: Mapped[str | None] = mapped_column(String(64), comment="RI")
    host_series: Mapped[str | None] = mapped_column(String(64), comment="主机系列，自由文本")
    platform: Mapped[str | None] = mapped_column(String(64), comment="平台，自由文本")
    region: Mapped[str | None] = mapped_column(String(64), comment="区域，自由文本")
    os: Mapped[str | None] = mapped_column(String(64), comment="操作系统，自由文本")
    cpu_cores: Mapped[int | None] = mapped_column(Integer, comment="CPU 核数")
    memory_gb: Mapped[int | None] = mapped_column(Integer, comment="内存 GB")
    disk_gb: Mapped[int | None] = mapped_column(Integer, comment="磁盘 GB")
    environment: Mapped[str] = mapped_column(String(16), nullable=False, comment="demo/stage/prod")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="online", comment="online/offline/maintenance")
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22, comment="SSH 端口")
    description: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[int | None] = mapped_column(UBIGINT, comment="创建人 user.id")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Application(Base):
    """应用服务表：工单目标主机通过应用关联获取（PRD 决策 D8）。"""

    __tablename__ = "application"

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="应用名")
    description: Mapped[str | None] = mapped_column(String(255))
    language: Mapped[str | None] = mapped_column(String(32), comment="开发语言")
    deploy_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="shell/docker/k8s")
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class AppHost(Base):
    """应用-主机关联（多对多，PRD APP-03）。"""

    __tablename__ = "app_host"
    __table_args__ = (
        UniqueConstraint("app_id", "host_id", name="uk_app_host"),
        Index("idx_app_host_host_id", "host_id"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    app_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    host_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class JobHost(Base):
    """作业主机（Jenkins agent）：替平台执行 Pipeline 步骤的通用执行节点；登录认证引用凭据管理（credential_id）。"""

    __tablename__ = "job_host"
    __table_args__ = (
        UniqueConstraint("ip", "ssh_port", name="uk_job_host_ip_port"),
        Index("idx_job_host_enabled", "enabled"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, comment="作业主机名称")
    ip: Mapped[str] = mapped_column(String(45), nullable=False, comment="IP 地址")
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22, comment="SSH 端口")
    credential_id: Mapped[int | None] = mapped_column(
        UBIGINT, comment="关联凭据 credential.id（登录认证随凭据管理；API 层必填）"
    )
    workdir: Mapped[str] = mapped_column(String(255), nullable=False, default="/opt/opspilot/workspace", comment="工作目录")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, comment="启用状态")
    last_check_at: Mapped[datetime | None] = mapped_column(DT3, comment="最近连通性测试时间")
    last_check_ok: Mapped[bool | None] = mapped_column(Boolean, comment="最近测试结果")
    last_check_msg: Mapped[str | None] = mapped_column(String(255), comment="最近测试结果信息")
    created_by: Mapped[int | None] = mapped_column(UBIGINT, comment="创建人 user.id")
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
