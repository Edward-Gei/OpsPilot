"""全局搜索服务：聚合主机/应用/工单/模板四段查询（SEARCH-02/03）。

登录即可调用，不挂独立权限点；按调用者权限集合决定各段返回与否，
无权段为 None（与 dashboard_service.get_summary 同惯例）。
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth import User
from app.services import cmdb_service, rbac_service, template_service, ticket_service

# 每段最多返回条数（顶栏下拉面板每组 5 条 + 总数）
SEGMENT_LIMIT = 5


async def search(session: AsyncSession, user: User, keyword: str) -> dict:
    """聚合搜索：复用各模块现有列表查询（keyword + page_size=5），无权段为 None。"""
    perms = await rbac_service.get_user_perms(session, user.id)
    data: dict = {"hosts": None, "apps": None, "tickets": None, "templates": None}

    if "cmdb:read" in perms:
        hosts, host_total = await cmdb_service.list_hosts(
            session, page=1, page_size=SEGMENT_LIMIT, keyword=keyword
        )
        data["hosts"] = {
            "items": [{"id": h.id, "hostname": h.hostname, "ip": h.ip} for h in hosts],
            "total": host_total,
        }
        apps, app_total, _ = await cmdb_service.list_apps(
            session, page=1, page_size=SEGMENT_LIMIT, keyword=keyword
        )
        data["apps"] = {
            "items": [{"id": a.id, "name": a.name} for a in apps],
            "total": app_total,
        }

    if "ticket:read" in perms:
        tickets, ticket_total = await ticket_service.list_tickets(
            session, page=1, page_size=SEGMENT_LIMIT, keyword=keyword
        )
        data["tickets"] = {
            "items": [{"id": t.id, "ticket_no": t.ticket_no, "title": t.title} for t in tickets],
            "total": ticket_total,
        }

    if "template:read" in perms:
        tpls, tpl_total = await template_service.list_templates(
            session, page=1, page_size=SEGMENT_LIMIT, keyword=keyword
        )
        data["templates"] = {
            "items": [{"id": t.id, "name": t.name, "type": t.type} for t in tpls],
            "total": tpl_total,
        }

    return data
