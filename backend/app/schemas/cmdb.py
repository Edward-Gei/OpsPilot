"""CMDB 请求模型（04-API设计 §4）：主机 / 应用。"""
import ipaddress
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class HostUpsertRequest(BaseModel):
    """主机新增/编辑共用请求体。"""

    hostname: str = Field(min_length=1, max_length=128)
    ip: str = Field(max_length=45)
    project: Literal["mitrade", "tradingkey"] = "mitrade"
    public_ip: str | None = Field(default=None, max_length=45)
    ri: str | None = Field(default=None, max_length=64)
    host_series: str | None = Field(default=None, max_length=64)
    platform: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    os: str | None = Field(default=None, max_length=64)
    cpu_cores: int | None = Field(default=None, ge=1, le=4096)
    memory_gb: int | None = Field(default=None, ge=1, le=65536)
    disk_gb: int | None = Field(default=None, ge=1, le=1048576)
    environment: Literal["demo", "stage", "prod"]
    status: Literal["online", "offline", "maintenance"] = "online"
    ssh_port: int = Field(default=22, ge=1, le=65535)
    description: str | None = Field(default=None, max_length=255)

    @field_validator("ip", "public_ip")
    @classmethod
    def validate_ip(cls, v: str | None) -> str | None:
        """内外网 IP 格式校验：兼容 IPv4/IPv6。"""
        if v is None:
            return v
        try:
            ipaddress.ip_address(v)
        except ValueError as exc:
            raise ValueError(f"IP 格式不合法: {v}") from exc
        return v


class AppUpsertRequest(BaseModel):
    """应用新增/编辑共用请求体；host_ids 全量替换关联。"""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=255)
    language: str | None = Field(default=None, max_length=32)
    deploy_type: Literal["shell", "docker", "k8s"]
    host_ids: list[int] = Field(default_factory=list)
