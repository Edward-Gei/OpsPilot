"""工单模板请求模型：只保留业务入口和流程引用。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.process_template import NotifyRuleInput


class TicketTemplateUpsertRequest(BaseModel):
    """工单模板不嵌入步骤、参数、审批或凭据。"""

    name: str = Field(min_length=1, max_length=128)
    type: Literal["release", "daily_ops", "other"]
    description: str | None = Field(default=None, max_length=512)
    job_host_id: int
    process_template_id: int
    allow_withdraw: bool = True
    notify_rules: list[NotifyRuleInput] = Field(default_factory=list)
    visible_role_ids: list[int] = Field(default_factory=list)
    status: Literal["enabled", "disabled"] = "enabled"


class TicketTemplateStatusRequest(BaseModel):
    """工单模板启停请求。"""

    status: Literal["enabled", "disabled"]
