"""audit_log 分区滚动维护（M7-3，02-技术架构 §5.2）。

职责：
- 滚动预建：确保「当月 + 未来 PREBUILD_MONTHS 个月」的月分区存在
  （REORGANIZE pmax 拆出新分区；初始迁移已预建至 2028-12，届时自动接续）；
- 过期清理：DROP 上界早于保留边界（today - AUDIT_RETENTION_DAYS）的整月分区。

调用入口 run_maintenance() 同时服务两种场景：
- Worker 内 APScheduler 每日定时调用；
- 手动触发验证（验收项）：
  docker exec opspilot-worker python -c \
    "import asyncio; from app.audit.partition import run_maintenance; print(asyncio.run(run_maintenance()))"

分区名与上界的换算是纯函数（compute_rollover），便于单测覆盖边界。
"""
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import text

from app.core.config import settings
from app.core.database import async_session_factory, engine
from app.models.audit import AuditLog

logger = logging.getLogger("opspilot.audit.partition")

AUDIT_TABLE = "audit_log"
# 滚动预建的未来月份数（当月之外再向前预留，避免月初写入落入 pmax）
PREBUILD_MONTHS = 3
# 月分区命名约定：pYYYYMM（与初始迁移一致）；pmax 为兜底分区不参与滚动
_PART_RE = re.compile(r"^p(\d{4})(\d{2})$")


def _next_month(year: int, month: int) -> tuple[int, int]:
    """计算下一个月的 (年, 月)。"""
    return (year + 1, 1) if month == 12 else (year, month + 1)


def partition_name(year: int, month: int) -> str:
    """月分区名：p202601 这类固定格式。"""
    return f"p{year}{month:02d}"


def partition_upper_bound(name: str) -> date | None:
    """由分区名反推其上界（次月 1 日）；非 pYYYYMM 格式（如 pmax）返回 None。"""
    m = _PART_RE.match(name)
    if not m:
        return None
    ny, nm = _next_month(int(m.group(1)), int(m.group(2)))
    return date(ny, nm, 1)


def compute_rollover(
    existing: list[str],
    today: date,
    retention_days: int,
    prebuild: int = PREBUILD_MONTHS,
) -> tuple[list[str], list[str]]:
    """纯函数：计算 (需预建分区名, 需 DROP 分区名)，供维护执行与单测共用。

    - 预建：当月起共 prebuild+1 个月中不存在的分区（升序，保证 REORGANIZE 顺序合法）；
    - DROP：分区上界 <= today - retention_days，即整个分区数据都已过保留期
      （只按整月清理，不做行级删除；pmax 与非常规命名分区永不 DROP）。
    """
    to_create: list[str] = []
    year, month = today.year, today.month
    for _ in range(prebuild + 1):
        name = partition_name(year, month)
        if name not in existing:
            to_create.append(name)
        year, month = _next_month(year, month)
    cutoff = today - timedelta(days=retention_days)
    to_drop = sorted(
        name for name in existing
        if (ub := partition_upper_bound(name)) is not None and ub <= cutoff
    )
    return to_create, to_drop


async def list_partitions() -> list[str]:
    """查询 audit_log 当前全部分区名（information_schema，按序返回）。"""
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT partition_name FROM information_schema.partitions "
            "WHERE table_schema = DATABASE() AND table_name = :t "
            "AND partition_name IS NOT NULL ORDER BY partition_ordinal_position"
        ), {"t": AUDIT_TABLE})
        return [r[0] for r in rows]


async def apply_rollover(to_create: list[str], to_drop: list[str]) -> None:
    """执行分区 DDL：REORGANIZE pmax 拆出新分区 + DROP 过期分区。

    DDL 在 MySQL 中隐式提交，走 AUTOCOMMIT 连接；分区名来自受控的
    pYYYYMM 生成规则（非用户输入），可安全拼接 SQL。
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        for name in to_create:
            ub = partition_upper_bound(name)
            await conn.execute(text(
                f"ALTER TABLE {AUDIT_TABLE} REORGANIZE PARTITION pmax INTO ("
                f"PARTITION {name} VALUES LESS THAN ('{ub:%Y-%m-%d}'), "
                f"PARTITION pmax VALUES LESS THAN (MAXVALUE))"
            ))
            logger.info("审计分区已预建：%s（上界 %s）", name, ub)
        for name in to_drop:
            await conn.execute(text(f"ALTER TABLE {AUDIT_TABLE} DROP PARTITION {name}"))
            logger.info("审计分区已清理：%s（超出保留期 %d 天）", name, settings.audit_retention_days)


async def run_maintenance(today: date | None = None) -> dict:
    """执行一次分区维护并留痕，返回摘要（定时任务与手动触发共用入口）。

    留痕采用直接落库而非 audit.log 队列：手动触发场景（docker exec 独立进程）
    没有运行中的 audit_writer，入队会随进程退出丢失。
    """
    today = today or date.today()
    existing = await list_partitions()
    to_create, to_drop = compute_rollover(existing, today, settings.audit_retention_days)
    if to_create or to_drop:
        await apply_rollover(to_create, to_drop)
    summary = {"created": to_create, "dropped": to_drop,
               "retention_days": settings.audit_retention_days}
    # 有实际动作才写审计（每日空跑不刷屏）
    if to_create or to_drop:
        async with async_session_factory() as session:
            session.add(AuditLog(
                created_at=datetime.now(), module="audit",
                action="audit.partition_maintain", result="success",
                target_type="table", target_name=AUDIT_TABLE, detail=summary,
            ))
            await session.commit()
    logger.info("审计分区维护完成：%s", summary)
    return summary
