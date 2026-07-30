"""审计查询服务（04-API设计 §9）：组合筛选、分页与导出取数。

设计要点：
- 查询与导出共用同一套筛选条件构建（_conditions），保证「导出内容与筛选一致」；
- 导出分批取数（EXPORT_BATCH）拼装行数据，总量受 EXPORT_MAX_ROWS=10 万行上限保护；
- audit_log 只 SELECT，排序固定 created_at DESC, id DESC（利用分区裁剪 + 复合主键）。
"""
import json
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog

# 导出上限（契约：流式下载，上限 10 万行）
EXPORT_MAX_ROWS = 100_000
# 导出分批取数大小（控制单次查询内存占用）
EXPORT_BATCH = 2_000

# 导出表头（与 export_row 列序一一对应，CSV/xlsx 共用）
EXPORT_HEADERS = [
    "时间", "操作人", "来源IP", "模块", "动作",
    "对象类型", "对象ID", "对象名称", "结果", "详情",
]


def _conditions(
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    actor: str | None = None,
    module: str | None = None,
    action: str | None = None,
    result: str | None = None,
    keyword: str | None = None,
) -> list:
    """构建组合筛选条件（列表查询与导出共用，保证两者语义一致）。

    - start/end：created_at 闭区间（分区键，命中分区裁剪）；
    - actor：操作人名称模糊匹配；keyword：对象名称模糊匹配（契约 keyword→target_name）；
    - module/action/result：精确匹配。
    """
    conds = []
    if start:
        conds.append(AuditLog.created_at >= start)
    if end:
        conds.append(AuditLog.created_at <= end)
    if actor:
        conds.append(AuditLog.actor_name.like(f"%{actor}%"))
    if module:
        conds.append(AuditLog.module == module)
    if action:
        conds.append(AuditLog.action == action)
    if result:
        conds.append(AuditLog.result == result)
    if keyword:
        conds.append(AuditLog.target_name.like(f"%{keyword}%"))
    return conds


async def list_logs(
    session: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    **filters,
) -> tuple[list[AuditLog], int]:
    """分页查询审计日志：返回 (当前页记录, 总数)，按时间倒序。"""
    conds = _conditions(**filters)
    total = (await session.execute(
        select(func.count()).select_from(AuditLog).where(*conds)
    )).scalar_one()
    rows = list((await session.execute(
        select(AuditLog).where(*conds)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )).scalars())
    return rows, total


def export_row(log: AuditLog) -> list[str]:
    """单条审计记录 → 导出行（列序与 EXPORT_HEADERS 对应，CSV/xlsx 共用）。"""
    return [
        log.created_at.strftime("%Y-%m-%d %H:%M:%S") if log.created_at else "",
        log.actor_name or "",
        log.source_ip or "",
        log.module,
        log.action,
        log.target_type or "",
        log.target_id or "",
        log.target_name or "",
        log.result,
        json.dumps(log.detail, ensure_ascii=False) if log.detail else "",
    ]


async def collect_export_rows(session: AsyncSession, **filters) -> list[list[str]]:
    """按筛选分批取数拼装导出行（含 10 万行上限截断），CSV/xlsx 两种格式共用。"""
    conds = _conditions(**filters)
    rows: list[list[str]] = []
    offset = 0
    while len(rows) < EXPORT_MAX_ROWS:
        batch = list((await session.execute(
            select(AuditLog).where(*conds)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset).limit(min(EXPORT_BATCH, EXPORT_MAX_ROWS - len(rows)))
        )).scalars())
        if not batch:
            break
        rows.extend(export_row(log) for log in batch)
        offset += len(batch)
    return rows
