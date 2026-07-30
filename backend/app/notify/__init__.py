"""通知包：渠道注册表（05-任务拆解 M6-1）。

三个落地渠道 + 三个预留空实现，分发器与测试接口统一经 get_channel 取实例。
"""
from app.core.constants import NotifyChannelType
from app.notify.base import BaseChannel, ChannelSendError, NotifyMessage, StubChannel
from app.notify.email import EmailChannel
from app.notify.teams import TeamsChannel
from app.notify.webhook import WebhookChannel

# 渠道单例注册表：落地渠道实例化，预留渠道用空实现占位
_CHANNELS: dict[str, BaseChannel] = {
    c.type: c for c in (EmailChannel(), WebhookChannel(), TeamsChannel())
}
for _t in (NotifyChannelType.DINGTALK, NotifyChannelType.FEISHU, NotifyChannelType.WECOM):
    _CHANNELS[_t.value] = StubChannel(_t.value)


def get_channel(channel_type: str) -> BaseChannel:
    """按类型取渠道实例；未知类型抛 ChannelSendError（分发器按失败留痕）。"""
    channel = _CHANNELS.get(channel_type)
    if channel is None:
        raise ChannelSendError(f"未知通知渠道: {channel_type}")
    return channel


__all__ = [
    "BaseChannel",
    "ChannelSendError",
    "NotifyMessage",
    "get_channel",
]
