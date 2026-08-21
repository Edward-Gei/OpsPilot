"""作业主机与凭据接口请求模型。流程模板模型位于 process_template。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


CredentialType = Literal["password", "private_key", "api_token", "username_password", "secret_file"]
_SSH_CREDENTIAL_TYPES = {"password", "private_key"}


class CredentialCreateRequest(BaseModel):
    """创建凭据；密文仅在请求内出现，类型决定字段约束。"""

    name: str = Field(min_length=1, max_length=64)
    login_user: str | None = Field(default=None, max_length=64)
    auth_type: CredentialType
    secret: str = Field(min_length=1)
    passphrase: str | None = None
    file_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_fields(self) -> "CredentialCreateRequest":
        if self.auth_type in _SSH_CREDENTIAL_TYPES | {"username_password"} and not self.login_user:
            raise ValueError("该凭据类型必须填写登录用户")
        if self.auth_type != "private_key" and self.passphrase:
            raise ValueError("仅 SSH 私钥支持私钥口令")
        if self.auth_type == "secret_file":
            if not self.file_name or "/" in self.file_name or "\\" in self.file_name:
                raise ValueError("文本密钥文件名不合法")
            try:
                size = len(self.secret.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError("文本密钥文件必须是 UTF-8 文本") from exc
            if size > 128 * 1024:
                raise ValueError("文本密钥文件不能超过 128 KiB")
        elif self.file_name:
            raise ValueError("仅文本密钥文件支持文件名")
        return self


class CredentialUpdateRequest(BaseModel):
    """更新凭据；类型固定，secret 为空表示保留原密文。"""

    name: str = Field(min_length=1, max_length=64)
    login_user: str | None = Field(default=None, max_length=64)
    auth_type: CredentialType | None = None
    secret: str = ""
    passphrase: str | None = None
    file_name: str | None = Field(default=None, max_length=128)
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
