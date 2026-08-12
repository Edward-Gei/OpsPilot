"""通知域请求模型（04-API 设计 §10）。"""
from pydantic import BaseModel, Field


class ChannelUpdateRequest(BaseModel):
    """渠道配置更新：secret 不传或提交掩码 ****** = 保留原密钥，空串 = 清空。"""

    enabled: bool = False
    config: dict = Field(default_factory=dict)
    secret: str | None = None


class ChannelTestRequest(BaseModel):
    """渠道测试：传当前事件和未保存配置；Email 渠道 receiver 直接给邮箱地址。"""

    config: dict = Field(default_factory=dict)
    secret: str | None = None
    event: str = "ticket.approved"
    receiver: str | None = Field(None, max_length=255)


class EventMappingsUpdateRequest(BaseModel):
    """事件-渠道映射全量提交：{event: [channel_type, ...]}。"""

    mappings: dict[str, list[str]] = Field(default_factory=dict)
