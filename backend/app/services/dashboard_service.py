"""工作台聚合服务：概览计数 / 工单状态分布 / 提单数趋势。

设计约束：
- 只读聚合，不落审计（读操作不记审计与其他列表接口一致）；
- summary 按调用者权限点裁剪返回段，无权限段返回 None，前端据此隐藏分区；
- 趋势分桶在 Python 内存完成（仅取时间戳列），兼容 MySQL 与测试用 SQLite，
  避免 DATE_FORMAT/strftime 方言分裂。
"""
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.models.audit import AuditLog
from app.models.auth import User
from app.models.cmdb import Application, Host
from app.models.execution import Execution
from app.models.ticket import Ticket
from app.services import execution_service, rbac_service, ticket_service

# 趋势粒度 → (桶数量, 桶宽描述)；口径与前端 segmented 选项一致
TREND_GRANULARITIES = ("day", "week", "month", "year")
_TREND_BUCKETS = {"day": 30, "week": 12, "month": 12, "year": 5}

# 执行"进行中"口径：非终态三态
_EXEC_ACTIVE = ("queued", "running", "paused")
# 本月成功率分母：实际进入执行且已出结果的终态（rejected/cancelled 未执行不计入）
_RATE_FINISHED = ("success", "failed", "interrupted")


async def _count_by(session: AsyncSession, column, model) -> dict[str, int]:
    """按单列 group by 计数，返回 {值: 数量}。"""
    rows = await session.execute(select(column, func.count()).select_from(model).group_by(column))
    return {k: v for k, v in rows}


async def get_summary(session: AsyncSession, user: User) -> dict:
    """工作台概览：登录即可调用，各数据段按权限点裁剪。"""
    perms = await rbac_service.get_user_perms(session, user.id)
    now = datetime.now()
    today_start = datetime.combine(now.date(), datetime.min.time())
    month_start = datetime.combine(now.date().replace(day=1), datetime.min.time())

    data: dict = {"cmdb": None, "ticket": None, "todo_total": None,
                  "execution": None, "audit_today": None}

    if "cmdb:read" in perms:
        host_status = await _count_by(session, Host.status, Host)
        app_total = (await session.execute(select(func.count()).select_from(Application))).scalar_one()
        data["cmdb"] = {
            "host_total": sum(host_status.values()),
            "host_status": host_status,
            "app_total": app_total,
        }

    if "ticket:read" in perms:
        status_dist = await _count_by(session, Ticket.status, Ticket)
        today_total = (await session.execute(
            select(func.count()).select_from(Ticket).where(Ticket.created_at >= today_start)
        )).scalar_one()
        month_rows = await session.execute(
            select(Ticket.status, func.count()).where(Ticket.created_at >= month_start)
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

    if "ticket:approve" in perms:
        _, todo_total = await ticket_service.todo_tickets(
            session, user_id=user.id, page=1, page_size=1
        )
        data["todo_total"] = todo_total

    if "execution:read" in perms:
        active = await _count_by(session, Execution.status, Execution)
        recent, _ = await execution_service.list_executions(session, page=1, page_size=8)
        data["execution"] = {
            "active": {s: active.get(s, 0) for s in _EXEC_ACTIVE},
            "recent": recent,
        }

    if "audit:read" in perms:
        data["audit_today"] = (await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= today_start)
        )).scalar_one()

    return data


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
    starts = _bucket_starts(granularity, datetime.now().date())
    range_start = datetime.combine(starts[0], datetime.min.time())
    rows = await session.execute(
        select(Ticket.created_at).where(Ticket.created_at >= range_start)
    )
    counts = [0] * len(starts)
    for (created,) in rows:
        d = created.date()
        # 从后向前找所属桶：第一个起始日期 <= d 的桶即命中
        for i in range(len(starts) - 1, -1, -1):
            if d >= starts[i]:
                counts[i] += 1
                break
    return {
        "granularity": granularity,
        "items": [
            {"period": _bucket_label(granularity, s), "count": c}
            for s, c in zip(starts, counts)
        ],
    }
