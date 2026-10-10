"""应用配置模块的请求模型。"""
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, Field

from app.core.constants import ConfigProvider


ContentFormat = Literal["properties", "yaml", "json", "text", "consul_kv"]


class PlatformInstanceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    provider: ConfigProvider
    base_url: AnyHttpUrl
    credential_id: int = Field(gt=0)
    description: str | None = Field(default=None, max_length=255)
    compatibility_version: str | None = Field(default=None, max_length=64)


class PlatformInstanceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: ConfigProvider | None = None
    base_url: AnyHttpUrl | None = None
    credential_id: int | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=255)
    enabled: bool | None = None
    compatibility_version: str | None = Field(default=None, max_length=64)


class ConfigFileSelection(BaseModel):
    locator: dict[str, str]
    content_format: ContentFormat
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    approval_role_id: int = Field(gt=0)
    application_ids: list[int] = Field(default_factory=list)
    initial_content: str = ""


class ConfigFileCreateRequest(BaseModel):
    platform_instance_id: int = Field(gt=0)
    selection: ConfigFileSelection


class ConfigFileMetadataRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    approval_role_id: int | None = Field(default=None, gt=0)
    application_ids: list[int] | None = None


class ConfigDraftUpdateRequest(BaseModel):
    content: str


class ConfigCandidateRejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=512)


class ConfigPublishRequest(BaseModel):
    confirmed_snapshot_id: int | None = Field(default=None, gt=0)


class ConfigDiscoverRequest(BaseModel):
    platform_instance_id: int = Field(gt=0)
    query: dict[str, str] = Field(default_factory=dict)


class ConfigImportTaskCreateRequest(BaseModel):
    platform_instance_id: int = Field(gt=0)
    selections: list[ConfigFileSelection] = Field(min_length=1, max_length=100)
