"""流程模板请求模型：统一定义参数、步骤、审批角色和执行策略。"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.constants import NotifyEvent
from app.schemas.ticket import ExecStrategy


class ProcessParam(BaseModel):
    """流程级参数；fixed 直接使用 default，user 在创建工单时填写。"""

    name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=64)
    source: Literal["fixed", "user", "generated"]
    input_type: Literal["text", "enum"] = "text"
    options: list[str] = Field(default_factory=list)
    default: str | None = None
    required: bool = False
    description: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_definition(self) -> "ProcessParam":
        """约束参数名和三类来源，避免脚本渲染时出现歧义。"""
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


class ProcessStepInput(BaseModel):
    """流程步骤；每步最多绑定一个审批角色。"""

    name: str = Field(min_length=1, max_length=128)
    script_type: Literal["shell", "playbook"]
    content: str = Field(min_length=1)
    timeout: int = Field(default=600, ge=1, le=86400)
    approval_role_id: int | None = None


class ProcessTemplateUpsertRequest(BaseModel):
    """流程模板创建和全量更新请求。"""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    params_schema: list[ProcessParam] = Field(default_factory=list, max_length=100)
    generator_script: str | None = None
    generator_timeout: int | None = Field(default=60, ge=1, le=3600)
    exec_strategy: ExecStrategy = Field(default_factory=ExecStrategy)
    steps: list[ProcessStepInput] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_params(self) -> "ProcessTemplateUpsertRequest":
        """参数名称必须唯一，且最多一个动态生成入口。"""
        names = [p.name for p in self.params_schema]
        if len(names) != len(set(names)):
            raise ValueError("流程参数名称不能重复")
        generated = [p for p in self.params_schema if p.source == "generated"]
        if generated and not self.generator_script:
            raise ValueError("动态参数必须配置生成脚本")
        if self.generator_script and not generated:
            raise ValueError("配置生成脚本时至少声明一个动态参数")
        return self


class ProcessTemplateStatusRequest(BaseModel):
    """流程模板启停请求。"""

    status: Literal["enabled", "disabled"]


class NotifyRuleInput(BaseModel):
    """工单通知规则，沿用现有事件和角色接收人表达式。"""

    event: str
    receivers: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_event(self) -> "NotifyRuleInput":
        if self.event not in {e.value for e in NotifyEvent}:
            raise ValueError(f"未知通知事件: {self.event}")
        return self
