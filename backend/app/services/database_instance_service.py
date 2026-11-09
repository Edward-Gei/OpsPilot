"""实例台账、显式授权与应用/承载主机关联。"""
from datetime import datetime

from sqlalchemy import String, case, cast, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BizError, Errors
from app.models.auth import Role
from app.models.cmdb import Application, Host
from app.models.database_management import (DatabaseInstance, DatabaseInstanceApplication,
    DatabaseInstanceHost, DatabaseRoleInstance, DatabaseTask)
from app.models.job import Credential
from app.schemas.database_management import InstanceWrite
from app.services.database_authorization_service import (MANAGEMENT_PERMS, current_permissions,
    granted_instance_ids, require_permission, require_query_instance)
from app.services.list_search import label_matches
from app.services.database_quota_service import bucket

ACTIVE_STATES = ("queued", "running", "stopping", "recovering")


async def get_instance(session, instance_id, *, lock=False):
    query = select(DatabaseInstance).where(DatabaseInstance.id == instance_id)
    if lock:
        query = query.with_for_update()
    row = (await session.execute(query.execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        raise Errors.not_found("数据库实例不存在")
    return row


async def _validate_ids(session, model, ids, label):
    if ids is None or len(ids) != len(set(ids)) or any(type(i) is not int or i <= 0 for i in ids):
        raise Errors.param(f"{label} ID 格式不正确")
    # API 鉴权可能已开启 RR 快照；短锁后的引用校验必须读取最新提交状态。
    found = set((await session.execute(select(model.id).where(model.id.in_(ids))
        .with_for_update())).scalars())
    if set(ids) != found:
        raise Errors.param(f"{label}不存在")


async def replace_application_instances(session, app_id: int, instance_ids: list[int]):
    """供 CMDB 同事务调用；只变关联，不修改实例内容。"""
    await bucket(session, "global", "all")
    await _validate_ids(session, Application, [app_id], "应用")
    await _validate_ids(session, DatabaseInstance, instance_ids, "数据库实例")
    await session.execute(delete(DatabaseInstanceApplication).where(DatabaseInstanceApplication.app_id == app_id))
    session.add_all([DatabaseInstanceApplication(app_id=app_id, instance_id=i) for i in instance_ids])
    await session.flush()


async def write_instance(session: AsyncSession, actor_id: int, req: InstanceWrite, instance_id=None):
    await require_permission(session, actor_id, "database:write")
    await bucket(session, "global", "all")
    fields = req.model_dump(exclude={"app_ids", "host_ids"})
    # 导入草稿允许暂不绑定账号，但不能让无凭据实例进入可执行状态。
    if req.credential_id is None:
        if req.enabled:
            raise Errors.param("请先配置数据库只读凭据后再启用实例")
    else:
        credential = (await session.execute(select(Credential).where(Credential.id == req.credential_id)
            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if credential is None or credential.auth_type != "username_password":
            raise Errors.param("请选择用户名密码类型的数据库只读凭据")
        if req.engine != "redis" and not credential.login_user:
            raise Errors.param("当前引擎的凭据必须填写登录用户")
    duplicate = (await session.execute(select(DatabaseInstance.id).where(
        DatabaseInstance.name == req.name, DatabaseInstance.id != (instance_id or 0))
        .with_for_update())).scalar_one_or_none()
    if duplicate:
        raise Errors.conflict("实例名称已存在")
    if {"app_ids", "host_ids"} & req.model_fields_set:
        await require_permission(session, actor_id, "cmdb:read")
    if req.app_ids is not None:
        await _validate_ids(session, Application, req.app_ids, "应用")
    if req.host_ids is not None:
        await _validate_ids(session, Host, req.host_ids, "主机")
    if instance_id is None:
        row = DatabaseInstance(**fields, created_by=actor_id)
        session.add(row)
        await session.flush()
    else:
        row = await get_instance(session, instance_id, lock=True)
        identity = ("engine", "host", "port", "credential_id", "connection_options")
        if any(getattr(row, key) != fields[key] for key in identity):
            row.config_revision += 1
        for key, value in fields.items():
            setattr(row, key, value)
        row.updated_at = datetime.now()
    for model, key, ids in ((DatabaseInstanceApplication, "app_id", req.app_ids),
                             (DatabaseInstanceHost, "host_id", req.host_ids)):
        if ids is not None:
            await session.execute(delete(model).where(model.instance_id == row.id))
            session.add_all([model(instance_id=row.id, **{key: value}) for value in ids])
    await session.flush()
    return row


async def delete_instance(session, actor_id, instance_id):
    await require_permission(session, actor_id, "database:delete")
    await bucket(session, "global", "all")
    # 与 submit 的实例锁配合，删除成功后无法出现新排队任务。
    row = await get_instance(session, instance_id, lock=True)
    if row.enabled:
        raise Errors.rejected("请先停用实例")
    active = (await session.execute(select(DatabaseTask.id).where(
        DatabaseTask.instance_id == instance_id, DatabaseTask.status.in_(ACTIVE_STATES))
        .limit(1).with_for_update())).scalar_one_or_none()
    if active is not None:
        raise Errors.rejected("实例仍有排队、执行或恢复任务")
    for model in (DatabaseInstanceApplication, DatabaseInstanceHost, DatabaseRoleInstance):
        await session.execute(delete(model).where(model.instance_id == instance_id))
    await session.delete(row)
    await session.flush()


async def replace_roles(session, actor_id, instance_id, role_ids):
    await require_permission(session, actor_id, "database:grant")
    await bucket(session, "global", "all")
    await get_instance(session, instance_id, lock=True)
    await _validate_ids(session, Role, role_ids, "角色")
    await session.execute(delete(DatabaseRoleInstance).where(DatabaseRoleInstance.instance_id == instance_id))
    session.add_all([DatabaseRoleInstance(instance_id=instance_id, role_id=i, granted_by=actor_id) for i in role_ids])
    await session.flush()


async def role_items(session, instance_id=None):
    query = select(Role).order_by(Role.id)
    if instance_id is not None:
        query = query.join(DatabaseRoleInstance, DatabaseRoleInstance.role_id == Role.id).where(
            DatabaseRoleInstance.instance_id == instance_id)
    return [{"id": r.id, "name": r.name} for r in (await session.execute(query)).scalars()]


async def instance_item(session, row, *, detail=False, include_cmdb_details=False):
    apps = (await session.execute(select(Application).join(DatabaseInstanceApplication,
        DatabaseInstanceApplication.app_id == Application.id).where(DatabaseInstanceApplication.instance_id == row.id)
        .order_by(Application.name, Application.id))).scalars()
    hosts = (await session.execute(select(Host).join(DatabaseInstanceHost,
        DatabaseInstanceHost.host_id == Host.id).where(DatabaseInstanceHost.instance_id == row.id)
        .order_by(Host.hostname, Host.id))).scalars()
    result = {key: getattr(row, key) for key in (
        "id", "name", "description", "engine", "business_line", "cluster_name", "cluster_role", "version", "host", "port",
        "enabled", "created_at", "updated_at", "last_probe_at", "last_probe_status", "last_probe_message")}
    result["needs_configuration"] = row.credential_id is None
    result["apps"] = [{"id": a.id, "name": a.name, **({"language": a.language,
        "deploy_type": a.deploy_type} if detail and include_cmdb_details else {})} for a in apps]
    result["hosts"] = [{"id": h.id, "hostname": h.hostname, "ip": h.ip,
        **({"environment": h.environment} if detail and include_cmdb_details else {})} for h in hosts]
    if detail:
        result.update({key: getattr(row, key) for key in ("environment", "credential_id",
            "connection_options", "config_revision", "concurrency_limit")})
        result["app_ids"] = [a["id"] for a in result["apps"]]
        result["host_ids"] = [h["id"] for h in result["hosts"]]
        cred = await session.get(Credential, row.credential_id) if row.credential_id is not None else None
        result["credential"] = credential_item(cred) if cred else None
    return result


def credential_item(cred):
    return {key: getattr(cred, key) for key in ("id", "name", "login_user", "auth_type")}


async def read_instance(session, actor_id, instance_id):
    perms = await current_permissions(session, actor_id)
    row = await get_instance(session, instance_id) if perms & MANAGEMENT_PERMS else await require_query_instance(
        session, actor_id, instance_id, require_enabled=False)
    return await instance_item(session, row, detail=True, include_cmdb_details="cmdb:read" in perms)


async def instance_query(session, actor_id, *, scope="management", keyword=None,
                         engine=None, business_line=None, enabled=None, probe_status=None, cluster_name=None,
                         instance_id=None, app_id=None, host_id=None, sort_by=None, sort_order="asc"):
    perms = await current_permissions(session, actor_id)
    query = select(DatabaseInstance)
    if scope == "query":
        await require_permission(session, actor_id, "database:query")
        query = query.where(DatabaseInstance.id.in_(granted_instance_ids(actor_id)))
    elif not perms & MANAGEMENT_PERMS:
        raise BizError(Errors.NO_PERM, "缺少数据库台账权限", 403)
    # 候选仅取当前授权范围，独立于搜索、关联筛选和分页，避免漏项或越权披露。
    cluster_names = list((await session.execute(query.with_only_columns(DatabaseInstance.cluster_name).where(
        DatabaseInstance.cluster_name.is_not(None), DatabaseInstance.cluster_name != "")
        .distinct().order_by(DatabaseInstance.cluster_name))).scalars())
    if keyword:
        like = f"%{keyword}%"
        # 连接地址按页面格式检索，IPv6 地址用方括号与端口分隔。
        address = case((DatabaseInstance.host.startswith("["), DatabaseInstance.host),
                       (DatabaseInstance.host.contains(":"), "[" + DatabaseInstance.host + "]"),
                       else_=DatabaseInstance.host) + ":" + cast(DatabaseInstance.port, String)
        app_match = select(DatabaseInstanceApplication.id).join(Application,
            Application.id == DatabaseInstanceApplication.app_id).where(
                DatabaseInstanceApplication.instance_id == DatabaseInstance.id, Application.name.like(like)).correlate(DatabaseInstance).exists()
        host_match = select(DatabaseInstanceHost.id).join(Host, Host.id == DatabaseInstanceHost.host_id).where(
            DatabaseInstanceHost.instance_id == DatabaseInstance.id,
            or_(Host.hostname.like(like), Host.ip.like(like))).correlate(DatabaseInstance).exists()
        status_label = case((DatabaseInstance.enabled.is_(True), "启用"), else_="停用") + "/" + case(
            (DatabaseInstance.last_probe_status == "succeeded", "成功"),
            (DatabaseInstance.last_probe_status == "failed", "失败"), else_="未检测")
        query = query.where(or_(*[getattr(DatabaseInstance, key).like(like) for key in (
            "name", "description", "engine", "cluster_name", "cluster_role", "version", "last_probe_message", "host")],
            cast(DatabaseInstance.port, String).like(like), address.like(like),
            label_matches(DatabaseInstance.engine, keyword, {"mysql": "MySQL", "postgresql": "PostgreSQL", "mongodb": "MongoDB", "redis": "Redis", "clickhouse": "ClickHouse"}),
            label_matches(DatabaseInstance.business_line, keyword, {"mitrade": "Mitrade", "tradingkey": "TradingKey"}),
            label_matches(DatabaseInstance.last_probe_status, keyword, {"untested": "未检测", "succeeded": "连接正常", "failed": "连接失败"}),
            label_matches(DatabaseInstance.last_probe_status, keyword, {"untested": "未检测", "succeeded": "成功", "failed": "失败"}),
            status_label.like(like),
            DatabaseInstance.enabled.is_(True) if keyword in "已启用" else False,
            DatabaseInstance.enabled.is_(False) if keyword in "已停用" else False, app_match, host_match))
    for key, value in (("engine", engine), ("business_line", business_line), ("enabled", enabled),
                       ("last_probe_status", probe_status), ("cluster_name", cluster_name), ("id", instance_id)):
        if value is not None:
            query = query.where(getattr(DatabaseInstance, key) == value)
    for model, key, value in ((DatabaseInstanceApplication, "app_id", app_id), (DatabaseInstanceHost, "host_id", host_id)):
        if value is not None:
            query = query.where(select(model.id).where(model.instance_id == DatabaseInstance.id,
                getattr(model, key) == value).correlate(DatabaseInstance).exists())
    sort_fields = {
        "name": DatabaseInstance.name, "description": DatabaseInstance.description,
        "engine": DatabaseInstance.engine, "business_line": DatabaseInstance.business_line,
        "cluster_name": DatabaseInstance.cluster_name, "cluster_role": DatabaseInstance.cluster_role,
        "version": DatabaseInstance.version, "created_at": DatabaseInstance.created_at,
    }
    if sort_by in ("apps", "hosts"):
        model, target, field, key = (DatabaseInstanceApplication, Application, Application.name, "app_id") if sort_by == "apps" else (
            DatabaseInstanceHost, Host, Host.hostname, "host_id")
        sort_fields[sort_by] = select(func.min(field)).select_from(model).join(target, getattr(model, key) == target.id).where(
            model.instance_id == DatabaseInstance.id).correlate(DatabaseInstance).scalar_subquery()
    if sort_by == "address":
        sort_values = (DatabaseInstance.host, DatabaseInstance.port)
    elif sort_by == "status":
        sort_values = (DatabaseInstance.enabled, DatabaseInstance.last_probe_status)
    elif sort_by:
        field = sort_fields[sort_by]
        sort_values = (func.nullif(field, "") if sort_by not in ("created_at", "apps", "hosts") else field,)
    else:
        sort_values = ()
    if sort_values:
        # 排序在分页前完成，空值始终末尾，实例 ID 使同值记录顺序稳定。
        ordering = [item for value in sort_values for item in (
            value.is_(None).asc(), value.desc() if sort_order == "desc" else value.asc())]
        query = query.order_by(*ordering, DatabaseInstance.id.desc())
    else:
        query = query.order_by(DatabaseInstance.id.desc())
    return query, cluster_names


async def list_instances(session, actor_id, *, page=1, page_size=20, **filters):
    """列表与 Excel 导出共用筛选和稳定排序，仅列表应用分页。"""
    query, cluster_names = await instance_query(session, actor_id, **filters)
    total = (await session.execute(select(func.count()).select_from(query.order_by(None).subquery()))).scalar_one()
    rows = (await session.execute(query.offset((page-1)*page_size).limit(page_size))).scalars()
    return {"items": [await instance_item(session, row) for row in rows], "total": total, "page": page,
            "page_size": page_size, "cluster_names": cluster_names}
