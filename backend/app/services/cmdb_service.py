"""CMDB 业务服务：主机/应用 CRUD、自动补全、删除保护、多对多关联。

删除保护规则（03-数据库设计 §3.3）：
    删主机：被应用关联 → 42201；V2 范式下工单引用的是作业主机（job_host），
    与 CMDB 主机已解耦，无工单侧删除保护
    删应用：V2 模型中工单不再引用应用，无工单侧删除保护
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.models.cmdb import AppHost, Application, Host


# ---------- 主机 ----------

async def get_host_or_404(session: AsyncSession, host_id: int) -> Host:
    """按 ID 取主机，不存在抛 40401。"""
    host = await session.get(Host, host_id)
    if host is None:
        raise Errors.not_found("主机不存在")
    return host


async def list_hosts(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    platform: str | None = None,
    region: str | None = None,
    environment: str | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> tuple[list[Host], int]:
    """分页查主机；keyword 模糊匹配主机名/IP，其余精确筛选。"""
    query = _host_filter_query(
        keyword=keyword, platform=platform, region=region, environment=environment, status=status
    )
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    order_by = (Host.id.desc(),)
    if sort_by == "created_at":
        order_by = (
            Host.created_at.asc() if sort_order == "asc" else Host.created_at.desc(),
            Host.id.desc(),
        )
    rows = await session.execute(query.order_by(*order_by).offset((page - 1) * page_size).limit(page_size))
    return list(rows.scalars()), total


def _host_filter_query(
    *,
    keyword: str | None = None,
    platform: str | None = None,
    region: str | None = None,
    environment: str | None = None,
    status: str | None = None,
):
    """主机筛选查询构造：列表与导出共用同一套过滤语义。"""
    query = select(Host)
    if keyword:
        like = f"%{keyword}%"
        query = query.where(Host.hostname.like(like) | Host.ip.like(like))
    if platform:
        query = query.where(Host.platform == platform)
    if region:
        query = query.where(Host.region == region)
    if environment:
        query = query.where(Host.environment == environment)
    if status:
        query = query.where(Host.status == status)
    return query


async def iter_hosts_filtered(session: AsyncSession, **filters) -> list[Host]:
    """按筛选条件取全量主机（导出用，按 id 升序保证行序稳定）。"""
    rows = await session.execute(_host_filter_query(**filters).order_by(Host.id.asc()))
    return list(rows.scalars())


async def create_host(session: AsyncSession, *, created_by: int, **fields) -> Host:
    """新增主机；IP 全局唯一，冲突抛 40901。"""
    await _ensure_ip_unique(session, fields["ip"])
    host = Host(created_by=created_by, **fields)
    session.add(host)
    await session.flush()
    return host


async def update_host(session: AsyncSession, host_id: int, **fields) -> Host:
    """编辑主机；变更 IP 时重新校验唯一性。"""
    host = await get_host_or_404(session, host_id)
    if fields.get("ip") and fields["ip"] != host.ip:
        await _ensure_ip_unique(session, fields["ip"])
    for key, value in fields.items():
        setattr(host, key, value)
    await session.flush()
    return host


async def _ensure_ip_unique(session: AsyncSession, ip: str) -> None:
    """IP 唯一性校验：已存在抛 40901（HOST-02）。"""
    exists = (await session.execute(select(Host.id).where(Host.ip == ip))).scalar_one_or_none()
    if exists is not None:
        raise Errors.conflict(f"IP {ip} 已存在（主机 #{exists}）")


async def delete_host(session: AsyncSession, host_id: int) -> Host:
    """删除主机；删除保护：被应用关联时拒绝（HOST-10）。

    V2 范式下工单的 job_host_id 引用 job_host 表，与 CMDB host 属不同 ID 空间
    且两者无外键关联，故不再做工单侧删除保护。"""
    host = await get_host_or_404(session, host_id)
    app_names = (
        await session.execute(
            select(Application.name)
            .join(AppHost, AppHost.app_id == Application.id)
            .where(AppHost.host_id == host_id)
        )
    ).scalars().all()
    if app_names:
        raise Errors.rejected(f"主机被应用引用，无法删除：{'、'.join(app_names[:5])}")
    await session.delete(host)
    await session.flush()
    return host


async def suggest_host_field(session: AsyncSession, field: str, q: str | None) -> list[str]:
    """平台/区域/主机系列自由文本自动补全：返回已有去重值。"""
    column = {
        "platform": Host.platform,
        "region": Host.region,
        "host_series": Host.host_series,
    }.get(field)
    if column is None:
        raise Errors.param("field 仅支持 platform / region / host_series")
    query = select(column).where(column.is_not(None)).distinct().order_by(column).limit(20)
    if q:
        query = query.where(column.like(f"%{q}%"))
    return [v for v in (await session.execute(query)).scalars() if v]


async def get_host_apps(session: AsyncSession, host_id: int) -> list[Application]:
    """主机详情反查关联应用（APP-05）。"""
    rows = await session.execute(
        select(Application)
        .join(AppHost, AppHost.app_id == Application.id)
        .where(AppHost.host_id == host_id)
        .order_by(Application.id)
    )
    return list(rows.scalars())


# ---------- 应用 ----------

async def get_app_or_404(session: AsyncSession, app_id: int) -> Application:
    """按 ID 取应用，不存在抛 40401。"""
    app = await session.get(Application, app_id)
    if app is None:
        raise Errors.not_found("应用不存在")
    return app


async def list_apps(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    language: str | None = None,
    deploy_type: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
) -> tuple[list[Application], int, dict[int, dict]]:
    """分页查应用；返回 (列表, 总数, {app_id: 关联主机数+IP 清单})。"""
    query = _app_filter_query(keyword=keyword, language=language, deploy_type=deploy_type)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    order_by = (Application.id.desc(),)
    if sort_by == "language":
        order_by = (
            Application.language.asc() if sort_order == "asc" else Application.language.desc(),
            Application.id.desc(),
        )
    elif sort_by == "created_at":
        order_by = (
            Application.created_at.asc() if sort_order == "asc" else Application.created_at.desc(),
            Application.id.desc(),
        )
    rows = await session.execute(
        query.order_by(*order_by).offset((page - 1) * page_size).limit(page_size)
    )
    apps = list(rows.scalars())
    # 关联主机明细一次查出，Python 侧聚合主机数和 IP 清单（列表展示用）
    return apps, total, await _app_host_stats(session, apps)


def _app_filter_query(
    *,
    keyword: str | None = None,
    language: str | None = None,
    deploy_type: str | None = None,
):
    """应用列表与导出共用筛选语义。"""
    query = select(Application)
    if keyword:
        query = query.where(Application.name.like(f"%{keyword}%"))
    if language:
        query = query.where(Application.language == language)
    if deploy_type:
        query = query.where(Application.deploy_type == deploy_type)
    return query


async def iter_apps_filtered(session: AsyncSession, **filters) -> tuple[list[Application], dict[int, dict]]:
    """按筛选条件取全量应用，供导出使用。"""
    rows = await session.execute(_app_filter_query(**filters).order_by(Application.id.asc()))
    apps = list(rows.scalars())
    return apps, await _app_host_stats(session, apps)


async def _app_host_stats(session: AsyncSession, apps: list[Application]) -> dict[int, dict]:
    """一次查询聚合应用关联主机数量与 IP 清单。"""
    host_stats: dict[int, dict] = {}
    if apps:
        detail_rows = await session.execute(
            select(AppHost.app_id, Host.ip)
            .join(Host, Host.id == AppHost.host_id)
            .where(AppHost.app_id.in_([a.id for a in apps]))
            .order_by(AppHost.app_id, Host.id)
        )
        for app_id, ip in detail_rows:
            stats = host_stats.setdefault(app_id, {"host_count": 0, "host_ips": []})
            stats["host_count"] += 1
            stats["host_ips"].append(ip)
    return host_stats


async def _ensure_app_name_unique(session: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    """应用名唯一性校验（APP-02），冲突抛 40901。"""
    query = select(Application.id).where(Application.name == name)
    if exclude_id is not None:
        query = query.where(Application.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.conflict(f"应用名 {name} 已存在")


async def _validate_host_ids(session: AsyncSession, host_ids: list[int]) -> None:
    """关联主机校验：存在性 + 仅限生产环境，不满足抛 40001。"""
    if not host_ids:
        return
    rows = (
        await session.execute(
            select(Host.id, Host.environment).where(Host.id.in_(host_ids))
        )
    ).all()
    found = {hid for hid, _ in rows}
    missing = set(host_ids) - found
    if missing:
        raise Errors.param(f"主机不存在: {sorted(missing)}")
    non_prod = sorted(hid for hid, env in rows if env != "prod")
    if non_prod:
        raise Errors.param(f"应用仅可关联生产环境主机，非生产主机: {non_prod}")


async def _replace_app_hosts(session: AsyncSession, app_id: int, host_ids: list[int]) -> None:
    """全量替换应用-主机关联（编辑页穿梭框语义）。"""
    existing = (
        await session.execute(select(AppHost).where(AppHost.app_id == app_id))
    ).scalars().all()
    target = set(host_ids)
    for link in existing:
        if link.host_id not in target:
            await session.delete(link)
    current = {link.host_id for link in existing}
    for hid in target - current:
        session.add(AppHost(app_id=app_id, host_id=hid))
    await session.flush()


async def create_app(
    session: AsyncSession, *, created_by: int, host_ids: list[int], **fields
) -> Application:
    """新增应用并建立主机关联。"""
    await _ensure_app_name_unique(session, fields["name"])
    await _validate_host_ids(session, host_ids)
    app = Application(created_by=created_by, **fields)
    session.add(app)
    await session.flush()
    await _replace_app_hosts(session, app.id, host_ids)
    return app


async def update_app(
    session: AsyncSession, app_id: int, *, host_ids: list[int], **fields
) -> Application:
    """编辑应用；host_ids 全量替换关联。"""
    app = await get_app_or_404(session, app_id)
    if fields.get("name") and fields["name"] != app.name:
        await _ensure_app_name_unique(session, fields["name"], exclude_id=app_id)
    await _validate_host_ids(session, host_ids)
    for key, value in fields.items():
        setattr(app, key, value)
    await _replace_app_hosts(session, app_id, host_ids)
    await session.flush()
    return app


async def delete_app(session: AsyncSession, app_id: int) -> Application:
    """删除应用；关联关系级联清理。V2 模型工单不引用应用，无需工单侧删除保护。"""
    app = await get_app_or_404(session, app_id)
    for link in (
        await session.execute(select(AppHost).where(AppHost.app_id == app_id))
    ).scalars():
        await session.delete(link)
    await session.delete(app)
    await session.flush()
    return app


async def get_app_hosts(session: AsyncSession, app_id: int) -> list[Host]:
    """应用详情：关联主机列表。"""
    rows = await session.execute(
        select(Host)
        .join(AppHost, AppHost.host_id == Host.id)
        .where(AppHost.app_id == app_id)
        .order_by(Host.id)
    )
    return list(rows.scalars())
