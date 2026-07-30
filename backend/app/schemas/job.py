"""凭据与模板请求模型（04-API设计 §5）。

params_schema 语义按 03-数据库设计 §4.3：参数定义数组
[{name, label, default, required, fixed, description}]（非 JSON Schema）；
fixed=true 的参数锁定默认值，提交人不可见不可改。
"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.constants import NotifyEvent
from app.schemas.ticket import ExecStrategy


# ---------- 凭据 ----------

class CredentialCreateRequest(BaseModel):
    """新建凭据：secret 必填（密码或私钥明文，入库前 AES-256-GCM 加密）。"""

    name: str = Field(min_length=1, max_length=64)
    login_user: str = Field(min_length=1, max_length=64)
    auth_type: Literal["password", "private_key"]
    secret: str = Field(min_length=1)
    passphrase: str | None = None
    description: str | None = Field(default=None, max_length=255)


class CredentialUpdateRequest(BaseModel):
    """编辑凭据：secret 传空 = 不变更密文（04-API §5）。"""

    name: str = Field(min_length=1, max_length=64)
    login_user: str = Field(min_length=1, max_length=64)
    auth_type: Literal["password", "private_key"]
    secret: str = ""
    passphrase: str | None = None
    description: str | None = Field(default=None, max_length=255)


# ---------- 工单模板（V2：全量规则配置） ----------

class TemplateParam(BaseModel):
    """步骤参数定义行：{{ name }} 占位变量（TPL-02）。"""

    name: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=64)
    default: str | None = None
    required: bool = False
    fixed: bool = Field(default=False, description="固定值：锁定默认值，提交人不可见不可改")
    description: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """变量名限定标识符格式，保证 {{ 变量 }} 渲染无歧义。"""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v):
            raise ValueError(f"参数名 {v} 不合法：仅限字母/数字/下划线且不能以数字开头")
        return v

    @model_validator(mode="after")
    def validate_fixed_default(self) -> "TemplateParam":
        """固定值参数必须提供默认值（提交人不可填，值即默认值）。"""
        if self.fixed and (self.default is None or self.default == ""):
            raise ValueError(f"固定值参数 {self.name} 必须设置默认值")
        return self


class TemplateStepInput(BaseModel):
    """模板步骤输入：脚本内嵌，顺序即数组顺序（TPL-02）。"""

    name: str = Field(min_length=1, max_length=128)
    script_type: Literal["shell", "playbook"]
    content: str = Field(min_length=1)
    params_schema: list[TemplateParam] = Field(default_factory=list)
    credential_id: int
    timeout: int = Field(default=600, ge=1, le=86400)

    @field_validator("params_schema")
    @classmethod
    def validate_unique_names(cls, v: list[TemplateParam]) -> list[TemplateParam]:
        """同一步骤内参数名不允许重复；跨步骤同名在提交表单中合并。"""
        names = [p.name for p in v]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"参数名重复: {sorted(dup)}")
        return v


class ApprovalNodeInput(BaseModel):
    """审批节点输入：节点=角色；V1 仅 any（或签）生效，其余仅存配置（FLOW-01）。"""

    node_order: int = Field(ge=1, le=10)
    role_id: int
    approve_mode: Literal["any", "all", "seq"] = "any"


class NotifyRuleInput(BaseModel):
    """通知规则行：receivers 支持 creator / approver_role / role:<id>（TPL-05）。"""

    event: str
    receivers: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)

    @field_validator("event")
    @classmethod
    def validate_event(cls, v: str) -> str:
        """事件限定 NotifyEvent 枚举值。"""
        if v not in {e.value for e in NotifyEvent}:
            raise ValueError(f"未知通知事件: {v}")
        return v

    @field_validator("receivers")
    @classmethod
    def validate_receivers(cls, v: list[str]) -> list[str]:
        """接收人表达式格式校验。"""
        for r in v:
            if r in ("creator", "approver_role"):
                continue
            if re.fullmatch(r"role:\d+", r):
                continue
            raise ValueError(f"非法接收人表达式: {r}（仅支持 creator/approver_role/role:<id>）")
        return v


class TemplateUpsertRequest(BaseModel):
    """模板新建/编辑共用全量配置体（04-API §5）。

    规则（步骤/策略/审批/通知/参数/权限范围）任一变更自动升版（TPL-06）；
    仅改名称/说明不升版；changelog 仅在升版时记录到新版本行。
    """

    name: str = Field(min_length=1, max_length=128)
    type: Literal["release", "change", "ops", "other"]
    description: str | None = Field(default=None, max_length=512)
    app_id: int
    steps: list[TemplateStepInput] = Field(min_length=1, max_length=20)
    exec_strategy: ExecStrategy = Field(default_factory=ExecStrategy)
    approval_enabled: bool = True
    approval_nodes: list[ApprovalNodeInput] = Field(default_factory=list, max_length=10)
    allow_withdraw: bool = True
    allow_transfer: bool = False
    allow_countersign: bool = False
    notify_rules: list[NotifyRuleInput] = Field(default_factory=list)
    visible_role_ids: list[int] = Field(default_factory=list, description="空=所有具备 ticket:write 的角色")
    changelog: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_approval(self) -> "TemplateUpsertRequest":
        """审批开启时节点必须从 1 连续且至少一个；关闭时忽略节点配置。"""
        if self.approval_enabled:
            orders = sorted(n.node_order for n in self.approval_nodes)
            if not orders:
                raise ValueError("已开启审批，至少配置一个审批节点")
            if orders != list(range(1, len(orders) + 1)):
                raise ValueError("审批节点序号必须从 1 开始连续")
        else:
            self.approval_nodes = []
        return self


class TemplateStatusRequest(BaseModel):
    """启用/禁用：禁用后不可被提交，不影响已提交工单（TPL-03）。"""

    status: Literal["enabled", "disabled"]
