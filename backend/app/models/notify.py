"""通知域模型：渠道配置 / 事件映射 / 发送记录 / 站内信（03-数据库设计 §7）。"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import DT3, UBIGINT, Base, created_at_column, pk_column, updated_at_column


class NotifyChannel(Base):
    """通知渠道：每类型一行，管理员全局配置（PRD NOTIFY-03）。"""

    __tablename__ = "notify_channel"

    id: Mapped[int] = pk_column()
    type: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, comment="email/webhook/teams/...")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否启用")
    config: Mapped[dict | None] = mapped_column(JSON, comment="非敏感配置（SMTP 地址 / Webhook URL 等）")
    secret_enc: Mapped[str | None] = mapped_column(Text, comment="敏感配置密文（SMTP 密码 / 签名密钥）")
    updated_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class NotifyChannelEvent(Base):
    """事件-渠道映射：决定某事件通过哪些渠道发送。"""

    __tablename__ = "notify_channel_event"
    __table_args__ = (
        UniqueConstraint("event", "channel_type", name="uk_event_channel"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    event: Mapped[str] = mapped_column(String(64), nullable=False, comment="事件编码，如 ticket.approved")
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="渠道类型")


class NotificationRecord(Base):
    """通知发送记录：含重试调度字段（指数退避 1m/5m/15m，最多 3 次）。"""

    __tablename__ = "notification_record"
    __table_args__ = (
        Index("idx_notification_event_time", "event", "created_at"),
        Index("idx_notification_status", "status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    event: Mapped[str] = mapped_column(String(64), nullable=False, comment="触发事件")
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="发送渠道")
    receiver: Mapped[str | None] = mapped_column(String(255), comment="收件人（邮箱/URL 摘要）")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="通知标题")
    content: Mapped[str | None] = mapped_column(Text, comment="通知正文")
    ref_type: Mapped[str | None] = mapped_column(String(16), comment="关联对象类型 ticket/execution")
    ref_id: Mapped[int | None] = mapped_column(UBIGINT, comment="关联对象 ID")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", comment="pending/success/failed")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="已重试次数")
    next_retry_at: Mapped[datetime | None] = mapped_column(DT3, comment="下次重试时间")
    error: Mapped[str | None] = mapped_column(String(512), comment="最近失败原因")
    sent_at: Mapped[datetime | None] = mapped_column(DT3, comment="成功发送时间")
    created_at: Mapped[datetime] = created_at_column()


class UserNotification(Base):
    """站内信（NOTIFY-06）：emit 时按收件人每人一行，顶栏铃铛消费；无发送/重试概念。"""

    __tablename__ = "user_notification"
    __table_args__ = (
        Index("idx_user_notification_read", "user_id", "is_read"),
        Index("idx_user_notification_time", "user_id", "created_at"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    user_id: Mapped[int] = mapped_column(UBIGINT, nullable=False, comment="收件用户")
    event: Mapped[str] = mapped_column(String(64), nullable=False, comment="触发事件")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="通知标题（业务默认文案）")
    content: Mapped[str | None] = mapped_column(Text, comment="通知正文")
    ref_type: Mapped[str | None] = mapped_column(String(16), comment="关联对象类型 ticket/execution")
    ref_id: Mapped[int | None] = mapped_column(UBIGINT, comment="关联对象 ID")
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="已读标记")
    read_at: Mapped[datetime | None] = mapped_column(DT3, comment="已读时间")
    created_at: Mapped[datetime] = created_at_column()
