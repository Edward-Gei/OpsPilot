"""Webhook 渠道：HMAC-SHA256 签名 POST（05-任务拆解 M6-1，PRD NOTIFY-01）。

config 结构：{url}；secret 为签名密钥（可空，空则不附签名头）。
签名算法：sign = hex(HMAC-SHA256(secret, f"{timestamp}.{body}"))，
接收方用 X-Ops-Timestamp 与原始 body 按同法验签，可防篡改与重放。
"""
import hashlib
import hmac
import json
import time

import httpx

from app.notify.base import BaseChannel, ChannelSendError, NotifyMessage

# 单次 HTTP 请求超时（秒）
_HTTP_TIMEOUT = 10


class WebhookChannel(BaseChannel):
    """通用 Webhook 渠道：JSON POST 到全局配置 URL。"""

    type = "webhook"

    async def send(self, message: NotifyMessage, config: dict, secret: str | None) -> None:
        """POST 事件载荷；HTTP 异常或非 2xx/3xx 响应视为发送失败。"""
        url = str((config or {}).get("url") or "").strip()
        if not url:
            raise ChannelSendError("Webhook URL 未配置")
        # 载荷固定结构，receivers 透传供接收方展示（冲突决策 C2）
        body = json.dumps(
            {
                "event": message.event,
                "title": message.title,
                "content": message.content or "",
                "receivers": message.receivers,
                "timestamp": int(time.time()),
            },
            ensure_ascii=False,
        )
        headers = {"Content-Type": "application/json"}
        if secret:
            # 时间戳参与签名，接收方可校验时效防重放
            ts = str(int(time.time()))
            sign = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
            headers["X-Ops-Timestamp"] = ts
            headers["X-Ops-Signature"] = sign
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.post(url, content=body.encode(), headers=headers)
        except httpx.HTTPError as exc:
            raise ChannelSendError(f"Webhook 请求失败: {exc}") from exc
        if resp.status_code >= 400:
            raise ChannelSendError(f"Webhook 返回 HTTP {resp.status_code}")
