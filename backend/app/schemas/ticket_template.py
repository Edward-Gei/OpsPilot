"""工单模板请求模型：维护业务入口及本入口的参数契约。"""

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.process_template import NotifyRuleInput


class TicketParam(BaseModel):
    """工单入口参数；固定值直接使用 default，用户参数提交时填写。"""

    name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=64)
    source: Literal["fixed", "user", "generated"]
    input_type: Literal["text", "enum"] = "text"
    options: list[str] = Field(default_factory=list)
    default: str | None = None
    required: bool = False
    description: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_definition(self) -> "TicketParam":
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError("参数名仅支持字母、数字和下划线，且不能以数字开头")
        if self.source == "fixed" and self.default is None:
            raise ValueError("固定值参数必须设置默认值")
        if self.input_type == "enum":
            if self.source != "generated" and not self.options:
                raise ValueError("枚举参数至少需要一个选项")
            if self.options and self.default is not None and self.default not in self.options:
                raise ValueError("枚举参数默认值必须属于候选项")
        elif self.options:
            raise ValueError("文本参数不支持候选项")
        return self


class CredentialRefInput(BaseModel):
    """脚本密钥引用仅保存别名与凭据 ID，密文始终不进入模板。"""

    alias: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z][A-Z0-9_]{0,31}$")
    credential_id: int


class TicketTemplateUpsertRequest(BaseModel):
    """工单模板保存业务入口、参数定义和动态生成配置。"""

    name: str = Field(min_length=1, max_length=128)
    type: Literal["release", "daily_ops", "other"]
    description: str | None = Field(default=None, max_length=512)
    job_host_id: int
    process_template_id: int
    params_schema: list[TicketParam] = Field(default_factory=list, max_length=100)
    generator_script: str | None = None
    generator_timeout: int | None = Field(default=60, ge=1, le=3600)
    credential_refs: list[CredentialRefInput] = Field(default_factory=list, max_length=50)
    allow_withdraw: bool = True
    notify_rules: list[NotifyRuleInput] = Field(default_factory=list)
    visible_role_ids: list[int] = Field(default_factory=list)
    status: Literal["enabled", "disabled"] = "enabled"

    @model_validator(mode="after")
    def validate_params(self) -> "TicketTemplateUpsertRequest":
        """保证当前工单入口的参数定义和动态脚本彼此一致。"""
        names = [p.name for p in self.params_schema]
        if len(names) != len(set(names)):
            raise ValueError("工单参数名称不能重复")
        generated = [p for p in self.params_schema if p.source == "generated"]
        if generated and not self.generator_script:
            raise ValueError("动态参数必须配置生成脚本")
        if self.generator_script and not generated:
            raise ValueError("配置生成脚本时至少声明一个动态参数")
        aliases = [item.alias for item in self.credential_refs]
        if len(aliases) != len(set(aliases)):
            raise ValueError("脚本密钥别名不能重复")
        credential_ids = [item.credential_id for item in self.credential_refs]
        if len(credential_ids) != len(set(credential_ids)):
            raise ValueError("同一凭据不能重复绑定")
        return self


class TicketTemplateStatusRequest(BaseModel):
    """工单模板启停请求。"""

    status: Literal["enabled", "disabled"]
