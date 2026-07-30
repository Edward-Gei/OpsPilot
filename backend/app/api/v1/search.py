"""全局搜索路由：顶栏搜索框聚合查询（登录即可访问，数据段按权限点裁剪）。

无独立权限点：search 内部按调用者 perms 决定各段返回与否，
与 dashboard summary 同惯例（04-API设计 §12）。
"""
from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession
from app.core.response import Errors, ok
from app.services import search_service

router = APIRouter(prefix="/search", tags=["全局搜索"])


@router.get("", summary="全局聚合搜索")
async def global_search(
    session: DbSession,
    user: CurrentUser,
    keyword: str = Query("", description="搜索关键词，不可为空"),
) -> dict:
    """主机/应用/工单/模板四段聚合；每段限 5 条 + total，无权段为 null。"""
    keyword = keyword.strip()
    if not keyword:
        raise Errors.param("keyword 不能为空")
    return ok(await search_service.search(session, user, keyword))
