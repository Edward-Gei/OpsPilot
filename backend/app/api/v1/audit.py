"""审计路由（04-API设计 §9）：audit:read 查询 / audit:export 导出。

导出实现说明：CSV 按行块流式输出（StreamingResponse）；xlsx 因格式本身需
整体打包，用 openpyxl write_only 模式构建后一次性下发。两者共用
audit_service.collect_export_rows 取数（10 万行上限），保证与列表筛选语义一致。
"""
import csv
import io
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import Errors, ok
from app.models.audit import AuditLog
from app.models.auth import User
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["审计"])

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# CSV 流式输出的行块大小（攒够一块 yield 一次，避免逐行响应开销）
_CSV_CHUNK_LINES = 2_000


def _log_brief(log: AuditLog) -> dict:
    """审计记录统一序列化（列表接口输出）。"""
    return {
        "id": log.id,
        "created_at": log.created_at.isoformat() if log.created_at else None,
        "actor_id": log.actor_id,
        "actor_name": log.actor_name,
        "source_ip": log.source_ip,
        "module": log.module,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "target_name": log.target_name,
        "result": log.result,
        "detail": log.detail,
    }


@router.get("/logs", summary="审计日志检索")
async def list_audit_logs(
    session: DbSession,
    _: User = Depends(require_perm("audit:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    start: datetime | None = None,
    end: datetime | None = None,
    actor: str | None = None,
    module: str | None = None,
    action: str | None = None,
    result: str | None = None,
    keyword: str | None = Query(None, description="对象名称模糊匹配"),
) -> dict:
    """组合检索：时间范围 / 操作人 / 模块 / 动作 / 结果 / 对象名称（AUDIT-02）。"""
    rows, total = await audit_service.list_logs(
        session, page=page, page_size=page_size,
        start=start, end=end, actor=actor, module=module,
        action=action, result=result, keyword=keyword,
    )
    return ok({
        "items": [_log_brief(r) for r in rows],
        "total": total, "page": page, "page_size": page_size,
    })


def _csv_stream(rows: list[list[str]]):
    """CSV 行块生成器：BOM 头（Excel 中文兼容）+ 表头 + 数据行分块 yield。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    buf.write("\ufeff")
    writer.writerow(audit_service.EXPORT_HEADERS)
    for i, row in enumerate(rows, 1):
        writer.writerow(row)
        if i % _CSV_CHUNK_LINES == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    yield buf.getvalue()


def _xlsx_bytes(rows: list[list[str]]) -> bytes:
    """xlsx 构建：write_only 模式逐行追加（10 万行上限内内存可控）。"""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("审计日志")
    ws.append(audit_service.EXPORT_HEADERS)
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


@router.get("/logs/export", summary="审计日志导出")
async def export_audit_logs(
    request: Request,
    session: DbSession,
    operator: User = Depends(require_perm("audit:export")),
    format: str = Query("csv", description="csv | xlsx"),
    start: datetime | None = None,
    end: datetime | None = None,
    actor: str | None = None,
    module: str | None = None,
    action: str | None = None,
    result: str | None = None,
    keyword: str | None = None,
):
    """按当前筛选导出 CSV/xlsx（上限 10 万行）；导出行为自身写审计（AUDIT-03）。"""
    if format not in ("csv", "xlsx"):
        raise Errors.param("format 仅支持 csv / xlsx")
    filters = dict(start=start, end=end, actor=actor, module=module,
                   action=action, result=result, keyword=keyword)
    rows = await audit_service.collect_export_rows(session, **filters)
    # 导出行为自身写审计：记录筛选条件与导出行数
    audit.log(
        module="audit", action="audit.export",
        actor_id=operator.id, actor_name=operator.username,
        source_ip=get_client_ip(request), target_type="audit_log",
        detail={"format": format, "count": len(rows),
                "filters": {k: (v.isoformat() if isinstance(v, datetime) else v)
                            for k, v in filters.items() if v}},
    )
    filename = f"审计日志_{datetime.now():%Y%m%d%H%M%S}.{format}"
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    if format == "csv":
        return StreamingResponse(
            _csv_stream(rows), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": disposition},
        )
    return Response(
        content=_xlsx_bytes(rows), media_type=XLSX_MIME,
        headers={"Content-Disposition": disposition},
    )
