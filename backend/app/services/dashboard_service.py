"""工作台聚合服务：概览计数、业务分布与工单/完成执行趋势。

设计约束：
- 只读聚合，不落审计（读操作不记审计与其他列表接口一致）；
- summary 按调用者权限点裁剪返回段，无权限段返回 None，前端据此隐藏分区；
- 趋势先按自然日 SQL 聚合，再在 Python 分桶，兼容 MySQL 与测试用 SQLite，
  避免 DATE_FORMAT/strftime 方言分裂。
"""
from datetime import date, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import BizError, Errors
from app.models.application_config import ConfigFile
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.cmdb import Application, Host
from app.models.domain import DnsRecordSet, DnsZone
from app.models.execution import Execution
from app.models.ticket import Ticket
from app.services import application_config_service, execution_service, rbac_service, ticket_service

# 趋势粒度 → (桶数量, 桶宽描述)；口径与前端 segmented 选项一致
TREND_GRANULARITIES = ("day", "week", "month", "year")
_TREND_BUCKETS = {"day": 30, "week": 12, "month": 12, "year": 5}

# 执行"进行中"口径：非终态三态
_EXEC_ACTIVE = ("queued", "running", "paused")
# 本月成功率分母：实际进入执行且已出结果的终态（rejected/cancelled 未执行不计入）
_RATE_FINISHED = ("success", "failed", "interrupted")
_EXEC_FINISHED = ("success", "failed", "terminated", "interrupted")
_CONFIG_EXCEPTIONS = ("drifted", "remote_missing", "sync_failed")


async def _count_by(session: AsyncSession, column, model, *filters) -> dict[str, int]:
    """按单列 group by 计数，返回 {值: 数量}。"""
    rows = await session.execute(
        select(column, func.count()).select_from(model).where(*filters).group_by(column)
    )
    return {k: v for k, v in rows}


async def get_summary(session: AsyncSession, user: User) -> dict:
    """工作台概览：登录即可调用，各数据段按权限点裁剪。"""
    perms = await rbac_service.get_user_perms(session, user.id)
    now = datetime.now()
    today_start = datetime.combine(now.date(), datetime.min.time())
    month_start = datetime.combine(now.date().replace(day=1), datetime.min.time())

    data: dict = {"cmdb": None, "domain": None, "config": None, "ticket": None,
                  "todo_total": None, "todo_breakdown": {"ticket": 0, "config": 0},
                  "execution": None, "audit_today": None, "audit": None, "attention": []}

    if "cmdb:read" in perms:
        host_status = await _count_by(session, Host.status, Host)
        app_deploy_type = await _count_by(session, Application.deploy_type, Application)
        host_rows = await session.execute(
            select(Host.environment, Host.status, func.count())
            .group_by(Host.environment, Host.status).order_by(Host.environment, Host.status)
        )
        data["cmdb"] = {
            "host_total": sum(host_status.values()),
            "host_status": host_status,
            "app_total": sum(app_deploy_type.values()),
            "host_environment_status": [
                {"environment": environment, "status": status, "count": count}
                for environment, status, count in host_rows
            ],
            "app_deploy_type": app_deploy_type,
        }

    if "domain:read" in perms:
        provider_dist = await _count_by(session, DnsZone.provider, DnsZone)
        record_rows = await session.execute(
            select(DnsRecordSet.record_type, func.count())
            .join(DnsZone, DnsZone.id == DnsRecordSet.zone_id).group_by(DnsRecordSet.record_type)
        )
        record_type_dist = dict(record_rows.all())
        data["domain"] = {
            "zone_total": sum(provider_dist.values()), "record_total": sum(record_type_dist.values()),
            "provider_dist": provider_dist,
            "sync_status": await _count_by(session, DnsZone.sync_status, DnsZone),
            "record_type_dist": record_type_dist,
        }

    if "config:read" in perms:
        # 异常优先于发布状态，归档不进入分母，保证五种展示状态互斥。
        display_status = case(
            (ConfigFile.drift_status.in_(_CONFIG_EXCEPTIONS), ConfigFile.drift_status),
            (ConfigFile.current_version_id.is_(None), "unpublished"), else_="clean",
        )
        status_dist = await _count_by(session, display_status, ConfigFile, ConfigFile.status != "archived")
        data["config"] = {"file_total": sum(status_dist.values()), "status_dist": status_dist}

    if "ticket:read" in perms:
        status_dist = await _count_by(session, Ticket.status, Ticket)
        today_total = (await session.execute(
            select(func.count()).select_from(Ticket)
            .where(Ticket.created_at >= today_start, Ticket.created_at <= now)
        )).scalar_one()
        month_rows = await session.execute(
            select(Ticket.status, func.count()).where(Ticket.created_at >= month_start, Ticket.created_at <= now)
            .group_by(Ticket.status)
        )
        month_dist = {k: v for k, v in month_rows}
        month_finished = sum(month_dist.get(s, 0) for s in _RATE_FINISHED)
        data["ticket"] = {
            "today_total": today_total,
            "month_total": sum(month_dist.values()),
            "month_success": month_dist.get("success", 0),
            "month_finished": month_finished,
            "status_dist": status_dist,
        }

    _, config_todo_total = await application_config_service.list_approval_todo(
        session, user_id=user.id, page=1, page_size=1,
    )
    data["todo_total"] = config_todo_total
    data["todo_breakdown"]["config"] = config_todo_total
    if "ticket:approve" in perms:
        _, todo_total = await ticket_service.todo_tickets(
            session, user_id=user.id, page=1, page_size=1
        )
        data["todo_total"] += todo_total
        data["todo_breakdown"]["ticket"] = todo_total

    if "execution:read" in perms:
        active = await _count_by(session, Execution.status, Execution)
        recent, _ = await execution_service.list_executions(session, page=1, page_size=8)
        data["execution"] = {
            "active": {s: active.get(s, 0) for s in _EXEC_ACTIVE},
            "recent": recent,
        }

    if "audit:read" in perms:
        today_filter = (AuditLog.created_at >= today_start, AuditLog.created_at <= now)
        result_dist = await _count_by(session, AuditLog.result, AuditLog, *today_filter)
        data["audit_today"] = sum(result_dist.values())
        data["audit"] = {
            "today_total": data["audit_today"], "result_dist": result_dist,
            "module_dist": await _count_by(session, AuditLog.module, AuditLog, *today_filter),
        }

    data["attention"] = await _get_attention(session, perms)
    return data


async def _get_attention(session: AsyncSession, perms: set[str]) -> list[dict]:
    """各模块只取最新六条安全元信息，合并后按时间倒序截取，避免读取正文。"""
    items = []
    if "config:read" in perms:
        rows = await session.execute(
            select(ConfigFile.id, ConfigFile.name, ConfigFile.drift_status, ConfigFile.updated_at)
            .where(ConfigFile.status != "archived", ConfigFile.drift_status.in_(_CONFIG_EXCEPTIONS))
            .order_by(ConfigFile.updated_at.desc(), ConfigFile.id.desc()).limit(6)
        )
        items.extend({"module": "config", "id": id, "name": name, "status": status, "updated_at": updated}
                     for id, name, status, updated in rows)
    if "domain:read" in perms:
        rows = await session.execute(
            select(DnsZone.id, DnsZone.zone_name, DnsZone.sync_status, DnsZone.updated_at)
            .where(DnsZone.sync_status == "failed")
            .order_by(DnsZone.updated_at.desc(), DnsZone.id.desc()).limit(6)
        )
        items.extend({"module": "domain", "id": id, "name": name, "status": status, "updated_at": updated}
                     for id, name, status, updated in rows)
    if "execution:read" in perms:
        updated_at = func.coalesce(Execution.finished_at, Execution.created_at)
        rows = await session.execute(
            select(Execution.id, Ticket.title, Execution.status, updated_at)
            .join(Ticket, Ticket.id == Execution.ticket_id)
            .where(Execution.status.in_(("failed", "interrupted")))
            .order_by(updated_at.desc(), Execution.id.desc()).limit(6)
        )
        items.extend({"module": "execution", "id": id, "name": name, "status": status, "updated_at": updated}
                     for id, name, status, updated in rows)
    items.sort(key=lambda item: (item["updated_at"] or datetime.min, item["module"], item["id"]), reverse=True)
    for item in items[:6]:
        item["updated_at"] = item["updated_at"].isoformat() if item["updated_at"] else None
    return items[:6]


def _bucket_starts(granularity: str, today: date) -> list[date]:
    """生成各桶起始日期（升序）：day=自然日 / week=ISO 周一 / month=月初 / year=年初。"""
    n = _TREND_BUCKETS[granularity]
    if granularity == "day":
        return [today - timedelta(days=i) for i in range(n - 1, -1, -1)]
    if granularity == "week":
        monday = today - timedelta(days=today.weekday())
        return [monday - timedelta(weeks=i) for i in range(n - 1, -1, -1)]
    if granularity == "month":
        first = today.replace(day=1)
        starts: list[date] = []
        y, m = first.year, first.month
        for _ in range(n):
            starts.append(date(y, m, 1))
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return starts[::-1]
    return [date(today.year - i, 1, 1) for i in range(n - 1, -1, -1)]


def _bucket_label(granularity: str, start: date) -> str:
    """桶展示标签：day=MM-DD / week=YYYY-Www / month=YYYY-MM / year=YYYY。"""
    if granularity == "day":
        return start.strftime("%m-%d")
    if granularity == "week":
        iso = start.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if granularity == "month":
        return start.strftime("%Y-%m")
    return str(start.year)


async def get_ticket_trend(session: AsyncSession, granularity: str) -> dict:
    """提单数趋势：按粒度分桶补零（day=近30天 / week=近12周 / month=近12月 / year=近5年）。"""
    if granularity not in TREND_GRANULARITIES:
        raise Errors.param(f"granularity 仅支持 {'/'.join(TREND_GRANULARITIES)}")
    now = datetime.now()
    starts = _bucket_starts(granularity, now.date())
    counts = await _trend_counts(session, Ticket.created_at, starts, now)
    return {
        "granularity": granularity,
        "items": [
            {"period": _bucket_label(granularity, s), "count": c}
            for s, c in zip(starts, counts)
        ],
    }


async def _trend_counts(session: AsyncSession, column, starts: list[date], now: datetime, *filters) -> list[int]:
    """按自然日压缩数据库结果，排除未来数据后复用原有自然时间分桶。"""
    day = func.date(column)
    rows = await session.execute(
        select(day, func.count()).where(
            column >= datetime.combine(starts[0], datetime.min.time()), column <= now, *filters,
        ).group_by(day)
    )
    counts = [0] * len(starts)
    for observed, count in rows:
        observed = date.fromisoformat(observed) if isinstance(observed, str) else observed
        for i in range(len(starts) - 1, -1, -1):
            if observed >= starts[i]:
                counts[i] += count
                break
    return counts


async def get_activity_trend(session: AsyncSession, user: User, granularity: str) -> dict:
    """工单按创建时间、完成执行按终态完成时间统计；无读取权限的系列返回 null。"""
    perms = await rbac_service.get_user_perms(session, user.id)
    if not {"ticket:read", "execution:read"} & perms:
        raise BizError(Errors.NO_PERM, "缺少权限: ticket:read 或 execution:read", 403)
    if granularity not in TREND_GRANULARITIES:
        raise Errors.param(f"granularity 仅支持 {'/'.join(TREND_GRANULARITIES)}")
    now = datetime.now()
    starts = _bucket_starts(granularity, now.date())
    tickets = (await _trend_counts(session, Ticket.created_at, starts, now)
               if "ticket:read" in perms else [None] * len(starts))
    executions = (await _trend_counts(session, Execution.finished_at, starts, now,
                                     Execution.status.in_(_EXEC_FINISHED))
                  if "execution:read" in perms else [None] * len(starts))
    return {
        "granularity": granularity,
        "items": [{"period": _bucket_label(granularity, start), "tickets": tickets[i], "executions": executions[i]}
                  for i, start in enumerate(starts)],
    }
