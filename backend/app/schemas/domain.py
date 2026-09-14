"""域名管理请求与服务层结果模型。"""
from typing import Literal

from pydantic import BaseModel, Field


class ZoneBindSelection(BaseModel):
    """用户从已发现的公网 Zone 中选择的绑定项。"""

    remote_zone_id: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


class ZoneBindResult(BaseModel):
    """批量绑定中单个远端 Zone 的处理结果。"""

    remote_zone_id: str
    zone_id: int | None = None
    status: Literal["success", "skipped", "failed"]
    reason: str | None = None
