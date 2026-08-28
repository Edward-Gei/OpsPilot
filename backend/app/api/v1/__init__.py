"""API v1 路由聚合：各模块路由自 M1 起在此挂载。"""
from fastapi import APIRouter

from app.api.v1.audit import router as audit_router
from app.api.v1.auth import router as auth_router
from app.api.v1.cmdb import router as cmdb_router
from app.api.v1.credentials import router as credentials_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.executions import router as executions_router
from app.api.v1.job_hosts import router as job_hosts_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.notify import router as notify_router
from app.api.v1.roles import router as roles_router
from app.api.v1.search import router as search_router
from app.api.v1.templates import router as templates_router
from app.api.v1.process_templates import router as process_templates_router
from app.api.v1.system import router as system_router
from app.api.v1.tickets import router as tickets_router
from app.api.v1.tokens import router as tokens_router
from app.api.v1.users import router as users_router
from app.core.response import ok

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(tokens_router)
api_router.include_router(users_router)
api_router.include_router(roles_router)
api_router.include_router(cmdb_router)
api_router.include_router(credentials_router)
api_router.include_router(job_hosts_router)
api_router.include_router(templates_router)
api_router.include_router(process_templates_router)
api_router.include_router(tickets_router)
api_router.include_router(executions_router)
api_router.include_router(notify_router)
api_router.include_router(notifications_router)
api_router.include_router(audit_router)
api_router.include_router(system_router)
api_router.include_router(dashboard_router)
api_router.include_router(search_router)


@api_router.get("/ping", summary="连通性探测")
async def api_ping() -> dict:
    """前端联调用的最小接口，验证统一响应包裹格式。"""
    return ok({"pong": True})
