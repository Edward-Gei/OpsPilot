"""域名管理请求与服务层结果模型。"""
from typing import Literal

from pydantic import BaseModel, Field

from app.core.constants import DomainProvider


class ZoneDiscoverRequest(BaseModel):
    """按服务商与已有凭据发现公网 Zone。"""

    provider: DomainProvider
    credential_id: int


class ZoneBindRequest(ZoneDiscoverRequest):
    """批量绑定用户已勾选的公网 Zone。"""

    selections: list["ZoneBindSelection"] = Field(min_length=1)


class ZoneDescriptionUpdateRequest(BaseModel):
    """本地 Zone 台账说明更新，不影响远端配置。"""

    description: str | None = Field(default=None, max_length=255)


class RecordSetCreateRequest(BaseModel):
    """简单路由记录集的新建请求。"""

    owner_name: str = Field(min_length=1, max_length=253)
    record_type: Literal["A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV"]
    ttl: int = Field(default=300, ge=300, le=86400)
    values: list[str] = Field(min_length=1)


class RecordSetUpdateRequest(BaseModel):
    """记录集更新仅允许调整 TTL 与记录值。"""

    ttl: int = Field(ge=300, le=86400)
    values: list[str] = Field(min_length=1)


class ZoneBindSelection(BaseModel):
    """用户从已发现的公网 Zone 中选择的绑定项。"""

    remote_zone_id: str = Field(min_length=1, max_length=255)
    zone_name: str | None = Field(default=None, min_length=1, max_length=253)
    description: str | None = Field(default=None, max_length=255)


class ZoneBindResult(BaseModel):
    """批量绑定中单个远端 Zone 的处理结果。"""

    remote_zone_id: str
    zone_id: int | None = None
    status: Literal["success", "skipped", "failed"]
    reason: str | None = None
