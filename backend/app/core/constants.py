"""业务常量与枚举：代码内为唯一事实来源，启动时幂等同步入库（见 03-数据库设计 §2.3）。"""
from enum import Enum


# ---------- 通用枚举（数据库存 VARCHAR，代码内 Enum 管理） ----------

class UserSource(str, Enum):
    LOCAL = "local"
    LDAP = "ldap"
    OIDC = "oidc"


class UserStatus(str, Enum):
    ACTIVE = "active"
    DISABLED = "disabled"


class HostEnvironment(str, Enum):
    DEMO = "demo"
    STAGE = "stage"
    PROD = "prod"


class HostStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    MAINTENANCE = "maintenance"


class DeployType(str, Enum):
    SHELL = "shell"
    DOCKER = "docker"
    K8S = "k8s"


class TemplateType(str, Enum):
    """工单模板类型（V2：模板=规则定义，类型描述工单业务属性）。"""
    RELEASE = "release"
    DAILY_OPS = "daily_ops"
    OTHER = "other"


class ScriptType(str, Enum):
    """模板步骤脚本类型。"""
    SHELL = "shell"
    PLAYBOOK = "playbook"


class TemplateStatus(str, Enum):
    """模板启用状态：仅启用模板可被提交工单。"""
    ENABLED = "enabled"
    DISABLED = "disabled"


class ApproveMode(str, Enum):
    """节点内审批方式：V1 仅 any（或签）生效，其余仅存配置。"""
    ANY = "any"
    ALL = "all"
    SEQ = "seq"


class CredentialAuthType(str, Enum):
    PASSWORD = "password"
    PRIVATE_KEY = "private_key"


class TicketStatus(str, Enum):
    """工单 9 态状态机（合法迁移见 03-数据库设计 §5.1；无草稿态，提交即生效）。

    过程态 approving/queued/running/paused，终态 success/failed/rejected/cancelled/interrupted。
    """
    APPROVING = "approving"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class InterruptReason(str, Enum):
    """工单终态为 interrupted 时的中断起因（03 §5.1；预留 infra_failure/unknown）。"""
    USER_ABORT = "user_abort"        # 人为中止（abort / force-abort）
    SYSTEM_CRASH = "system_crash"    # Worker 崩溃，running/paused 现场丢失
    WORKER_LOST = "worker_lost"      # queued 消息重试耗尽仍无 Worker 认领


class ExecutionStatus(str, Enum):
    """执行实例状态：terminated=人为中止（映射工单 interrupted+user_abort）；
    interrupted=系统崩溃/失联（映射工单 interrupted+system_crash/worker_lost）。"""
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCESS = "success"
    FAILED = "failed"
    TERMINATED = "terminated"
    INTERRUPTED = "interrupted"


class HostExecStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    TERMINATED = "terminated"


class NotifyEvent(str, Enum):
    """通知触发事件（PRD NOTIFY-02；execution.interrupted 为 M5 中断通知补充）。"""
    TICKET_PENDING_APPROVAL = "ticket.pending_approval"
    TICKET_APPROVED = "ticket.approved"
    TICKET_REJECTED = "ticket.rejected"
    EXECUTION_SUCCESS = "execution.success"
    EXECUTION_FAILED = "execution.failed"
    EXECUTION_INTERRUPTED = "execution.interrupted"


class NotifyChannelType(str, Enum):
    """V1 落地前三个，后三个仅预留（PRD NOTIFY-01）。"""
    EMAIL = "email"
    WEBHOOK = "webhook"
    TEAMS = "teams"
    DINGTALK = "dingtalk"
    FEISHU = "feishu"
    WECOM = "wecom"


# ---------- 权限点全集（04-API设计 §12） ----------
# (code, 显示名, 所属模块)
PERMISSIONS: list[tuple[str, str, str]] = [
    ("user:read", "用户查看", "user"),
    ("user:write", "用户管理", "user"),
    ("user:mfa", "用户MFA管理", "user"),
    ("role:read", "角色查看", "user"),
    ("role:write", "角色管理", "user"),
    ("cmdb:read", "CMDB查看", "cmdb"),
    ("cmdb:write", "CMDB管理", "cmdb"),
    ("credential:read", "凭据查看", "job"),
    ("credential:write", "凭据管理", "job"),
    ("template:read", "模板查看", "job"),
    ("template:write", "模板管理", "job"),
    ("job_host:read", "作业主机查看", "job"),
    ("job_host:write", "作业主机管理", "job"),
    ("ticket:read", "工单查看", "ticket"),
    ("ticket:write", "工单创建", "ticket"),
    ("ticket:approve", "工单审批", "ticket"),
    ("execution:read", "执行记录查看", "execution"),
    ("execution:control", "执行控制", "execution"),
    ("audit:read", "审计查看", "audit"),
    ("audit:export", "审计导出", "audit"),
    ("notify:config", "通知配置", "system"),
    ("system:config", "系统配置", "system"),
]

# ---------- 内置角色与权限矩阵（PRD §2.2 / 04-API设计 §12） ----------
BUILTIN_ROLES: dict[str, dict] = {
    "admin": {
        "name": "管理员",
        "description": "系统内置：全部权限",
        "permissions": [p[0] for p in PERMISSIONS],
    },
    "ops": {
        "name": "运维",
        "description": "系统内置：CMDB/模板/工单/执行",
        "permissions": [
            "cmdb:read", "cmdb:write",
            "credential:read",
            "template:read", "template:write",
            "ticket:read", "ticket:write",
            "execution:read", "execution:control",
        ],
    },
    "approver": {
        "name": "审批人",
        "description": "系统内置：工单审批",
        "permissions": ["cmdb:read", "ticket:read", "ticket:approve", "execution:read"],
    },
    "auditor": {
        "name": "审计员",
        "description": "系统内置：审计查看与导出",
        "permissions": ["cmdb:read", "execution:read", "audit:read", "audit:export"],
    },
}

# ---------- system_config 默认值（03-数据库设计 §9.1） ----------
SYSTEM_CONFIG_DEFAULTS: dict[str, dict] = {
    "mfa.policy": {"value": "off"},  # off / optional / required
    # 登录令牌有效期（分钟/天）；字段为空时回退环境变量默认值
    "token.policy": {"value": {"access_minutes": None, "refresh_days": None}},
    "password.policy": {
        "value": {"min_length": 8, "require_complex": True, "max_fail": 5, "lock_minutes": 15}
    },
    "ldap.config": {"value": None},
    "oidc.config": {"value": None},
    "ldap.default_role": {"value": "ops"},
    "oidc.default_role": {"value": "ops"},
    "exec.global_concurrency": {"value": 50},
}

# ---------- 通知事件默认渠道映射（03-数据库设计 §10：全部落地渠道，渠道默认禁用） ----------
DEFAULT_EVENT_CHANNELS: list[str] = [
    NotifyChannelType.EMAIL.value,
    NotifyChannelType.WEBHOOK.value,
    NotifyChannelType.TEAMS.value,
]
