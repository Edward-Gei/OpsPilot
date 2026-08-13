"""流程模板请求模型：定义步骤、审批角色和执行策略。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import NotifyEvent
from app.schemas.ticket import ExecStrategy


class ProcessStepInput(BaseModel):
    """流程步骤；每步最多绑定一个审批角色。"""

    name: str = Field(min_length=1, max_length=128)
    script_type: Literal["shell", "playbook"]
    content: str = Field(min_length=1)
    timeout: int = Field(default=600, ge=1, le=86400)
    approval_role_id: int | None = None


class ProcessTemplateUpsertRequest(BaseModel):
    """流程模板创建和全量更新请求。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    exec_strategy: ExecStrategy = Field(default_factory=ExecStrategy)
    steps: list[ProcessStepInput] = Field(min_length=1, max_length=50)


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
