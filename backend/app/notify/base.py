"""通知渠道抽象（05-任务拆解 M6-1）。

职责边界：渠道只负责"传输"——收到已渲染的标题/正文与渠道配置后完成一次发送；
收件人解析（用户名→邮箱）、启用判断、重试调度均由分发器（dispatcher）承担。
渠道发送失败统一抛 ChannelSendError，由分发器写入 record.error 并退避重试。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ChannelSendError(Exception):
    """渠道发送失败（配置缺失/网络/认证错误），message 将写入发送记录。"""


@dataclass
class NotifyMessage:
    """一次发送的完整载荷（由分发器或测试接口组装）。

    Email 使用分发器解析后的邮箱地址；Webhook/Teams 使用原始用户名数组，
    供模板中的 {receivers} 变量透传展示。
    """

    event: str
    title: str
    content: str | None = None
    receivers: list[str] = field(default_factory=list)


class BaseChannel(ABC):
    """渠道基类：type 与 NotifyChannelType 枚举值一一对应。"""

    type: str

    @abstractmethod
    async def send(self, message: NotifyMessage, config: dict, secret: str | None) -> None:
        """执行一次发送。

        config 为渠道非敏感配置（notify_channel.config），secret 为已解密的
        敏感配置（SMTP 密码 / HMAC 签名密钥）；失败抛 ChannelSendError。
        """


class StubChannel(BaseChannel):
    """预留渠道空实现：钉钉/飞书/企微 V1 不落地，仅保留接口位（PRD NOTIFY-01）。"""

    def __init__(self, type_: str) -> None:
        self.type = type_

    async def send(self, message: NotifyMessage, config: dict, secret: str | None) -> None:
        """预留渠道直接报未实现，分发器按失败留痕。"""
        raise ChannelSendError(f"渠道 {self.type} 暂未实现")
