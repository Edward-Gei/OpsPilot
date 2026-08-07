"""作业主机与凭据接口请求模型。流程模板模型位于 process_template。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CredentialCreateRequest(BaseModel):
    """创建 SSH 凭据；明文只在写入时出现。"""

    name: str = Field(min_length=1, max_length=64)
    login_user: str = Field(min_length=1, max_length=64)
    auth_type: Literal["password", "private_key"]
    secret: str = Field(min_length=1)
    passphrase: str | None = None
    description: str | None = Field(default=None, max_length=255)


class CredentialUpdateRequest(BaseModel):
    """更新 SSH 凭据；secret 为空表示保留原密文。"""

    name: str = Field(min_length=1, max_length=64)
    login_user: str = Field(min_length=1, max_length=64)
    auth_type: Literal["password", "private_key"]
    secret: str = ""
    passphrase: str | None = None
    description: str | None = Field(default=None, max_length=255)


class JobHostCreateRequest(BaseModel):
    """创建作业主机；凭据由作业主机管理，模板不再直接引用。"""

    name: str = Field(min_length=1, max_length=128)
    ip: str = Field(min_length=1, max_length=45)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    credential_id: int
    workdir: str = Field(default="/opt/opspilot/workspace", max_length=255)


class JobHostUpdateRequest(BaseModel):
    """更新作业主机连接信息。"""

    name: str | None = Field(default=None, max_length=128)
    ip: str | None = Field(default=None, max_length=45)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    credential_id: int | None = None
    workdir: str | None = Field(default=None, max_length=255)


class JobHostStatusRequest(BaseModel):
    """启用或停用作业主机。"""

    enabled: bool


class JobHostDetailResponse(BaseModel):
    """作业主机详情，不返回凭据密文。"""

    id: int
    name: str
    ip: str
    ssh_port: int
    credential_id: int | None = None
    workdir: str
    enabled: bool
    last_check_at: datetime | None = None
    last_check_ok: bool | None = None
    last_check_msg: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
