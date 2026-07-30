"""工单中心请求模型（04-API设计 §6，V2）。

工单中心只能使用模板：提交时仅传 {template_id, params}，标题使用模板名，
其余规则全部来自模板快照；无草稿态，提交即生效。

exec_strategy 数值边界（模板配置，schemas.job 复用）：
    concurrency 1~50（全局上限 exec.global_concurrency 默认 50）
    batch_size  0=不分批；timeout 单机秒级
"""
from typing import Literal

from pydantic import BaseModel, Field


class ExecStrategy(BaseModel):
    """执行策略：配置在模板内，提交时快照到工单（TPL-04）。"""

    concurrency: int = Field(default=5, ge=1, le=50, description="批内并发数")
    batch_size: int = Field(default=0, ge=0, le=1000, description="分批大小，0=不分批")
    batch_pause: bool = Field(default=False, description="批间暂停等待确认")
    timeout: int = Field(default=600, ge=1, le=86400, description="单机超时秒数")
    fail_fast: bool = Field(default=True, description="失败即中断后续")
    kill_on_stop: bool = Field(default=False, description="停止时强杀进行中连接")


class TicketCreateRequest(BaseModel):
    """提交工单：只填参数，标题使用模板名（TICKET-01）。"""

    template_id: int
    params: dict[str, str] = Field(default_factory=dict, description="汇总参数值 {name: value}")


class ApproveRequest(BaseModel):
    """审批操作：驳回意见必填在 service 层校验（FLOW-04）。"""

    action: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=512)
