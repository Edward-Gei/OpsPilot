"""模型聚合导出：确保 Base.metadata 收录全部 23 张表（Alembic 迁移依赖此处）。"""
from app.models.base import Base
from app.models.auth import ApiToken, Permission, Role, RolePermission, User, UserRole
from app.models.cmdb import AppHost, Application, Host, JobHost
from app.models.job import Credential, ProcessStep, ProcessTemplate, TicketTemplate
from app.models.ticket import (
    Ticket,
    TicketApproval,
    TicketParameterPrepare,
    TicketStep,
)
from app.models.execution import Execution, ExecutionStep
from app.models.notify import NotificationRecord, NotifyChannel, NotifyChannelEvent, UserNotification
from app.models.audit import AuditLog, SystemConfig

__all__ = [
    "Base",
    "User", "Role", "Permission", "UserRole", "RolePermission", "ApiToken",
    "Host", "Application", "AppHost", "JobHost",
    "Credential", "ProcessTemplate", "ProcessStep", "TicketTemplate",
    "Ticket", "TicketStep", "TicketApproval", "TicketParameterPrepare",
    "Execution", "ExecutionStep",
    "NotifyChannel", "NotifyChannelEvent", "NotificationRecord", "UserNotification",
    "AuditLog", "SystemConfig",
]
