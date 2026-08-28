"""工单中心请求模型（04-API设计 §6，V2）。

工单中心只能使用模板：提交时仅传 {template_id, params}，标题使用模板名，
其余规则全部来自模板快照；无草稿态，提交即生效。

exec_strategy 数值边界（模板配置，schemas.job 复用）：
    timeout 步骤级超时秒数
"""
from typing import Literal

from pydantic import BaseModel, Field


class ExecStrategy(BaseModel):
    """执行策略：配置在模板内，提交时快照到工单（TPL-04）。"""

    timeout: int = Field(default=600, ge=1, le=86400, description="步骤超时秒数")
    fail_fast: bool = Field(default=True, description="失败即中断后续")
    kill_on_stop: bool = Field(default=False, description="停止时强杀进行中连接")


class TicketCreateRequest(BaseModel):
    """提交工单：只填参数，标题使用模板名（TICKET-01）。"""

    template_id: int
    params: dict[str, str] = Field(default_factory=dict, description="汇总参数值 {name: value}")
    prepare_id: str | None = Field(default=None, description="动态参数预生成令牌")


class TicketPrepareRequest(BaseModel):
    """创建工单前执行流程模板动态参数脚本。"""

    template_id: int
    params: dict[str, str] = Field(default_factory=dict)


class ApproveRequest(BaseModel):
    """审批操作：驳回意见必填在 service 层校验（FLOW-04）。"""

    action: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=512)
