"""Teams 渠道：Incoming Webhook MessageCard（05-任务拆解 M6-1，PRD NOTIFY-01）。

config 结构：{url}（Teams 频道 Incoming Webhook 地址）；无敏感配置。
"""
import json
import httpx

from app.notify.base import BaseChannel, ChannelSendError, NotifyMessage

_HTTP_TIMEOUT = 10

# 事件→卡片主题色：成功绿 / 失败红 / 中断橙 / 工单流转蓝
_EVENT_COLORS = {
    "execution.success": "22C55E",
    "execution.failed": "EF4444",
    "execution.interrupted": "F59E0B",
}
_DEFAULT_COLOR = "3B82F6"


class TeamsChannel(BaseChannel):
    """Teams 卡片渠道：POST MessageCard 到 Incoming Webhook。"""

    type = "teams"

    async def send(self, message: NotifyMessage, config: dict, secret: str | None) -> None:
        """发送模板渲染后的完整 MessageCard；Teams 接口返回非 2xx 视为失败。"""
        url = str((config or {}).get("url") or "").strip()
        if not url:
            raise ChannelSendError("Teams Webhook URL 未配置")
        try:
            card = json.loads(message.content or "")
        except json.JSONDecodeError as exc:
            raise ChannelSendError(f"Teams Card JSON 模板无效: {exc}") from exc
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(url, json=card)
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"Teams 请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelSendError(f"Teams 返回 HTTP {resp.status_code}")
