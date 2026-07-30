"""Email 渠道：aiosmtplib 异步 SMTP 发送（05-任务拆解 M6-1）。

config 结构（notify_channel.config）：
    {host, port, username, from_addr, use_tls, starttls}
    use_tls=True 走隐式 TLS（465）；use_tls=False 且 starttls=True 走 STARTTLS（587）。
secret 为 SMTP 密码（AES-256-GCM 解密后传入）。
"""
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import aiosmtplib

from app.notify.base import BaseChannel, ChannelSendError, NotifyMessage

# SMTP 建连+发送整体超时（秒），避免故障服务器拖死分发循环
_SMTP_TIMEOUT = 15


class EmailChannel(BaseChannel):
    """SMTP 邮件渠道：receivers 为分发器解析好的邮箱地址列表。"""

    type = "email"

    async def send(self, message: NotifyMessage, config: dict, secret: str | None) -> None:
        """发送纯文本邮件；配置缺失/无收件人/SMTP 异常均抛 ChannelSendError。"""
        config = config or {}
        host = str(config.get("host") or "").strip()
        if not host:
            raise ChannelSendError("SMTP 服务器地址未配置")
        if not message.receivers:
            raise ChannelSendError("无有效收件邮箱")
        from_addr = str(config.get("from_addr") or config.get("username") or "").strip()
        if not from_addr:
            raise ChannelSendError("发件人地址未配置")

        # 组装 MIME：标题即事件标题，正文缺省回退标题
        mime = MIMEText(message.content or message.title, "plain", "utf-8")
        mime["Subject"] = Header(message.title, "utf-8")
        mime["From"] = formataddr(("OpsPilot", from_addr))
        mime["To"] = ", ".join(message.receivers)

        use_tls = bool(config.get("use_tls", True))
        starttls = bool(config.get("starttls", False)) and not use_tls
        username = str(config.get("username") or "").strip()
        try:
            await aiosmtplib.send(
                mime,
                sender=from_addr,
                recipients=message.receivers,
                hostname=host,
                port=int(config.get("port") or (465 if use_tls else 587)),
                username=username or None,
                password=secret or None,
                use_tls=use_tls,
                start_tls=starttls or None,
                timeout=_SMTP_TIMEOUT,
            )
        except ChannelSendError:
            raise
        except Exception as exc:  # noqa: BLE001 SMTP 库异常种类繁多，统一转渠道错误
            raise ChannelSendError(f"SMTP 发送失败: {exc}") from exc
