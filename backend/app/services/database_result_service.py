"""有界结果编码、分块加密与所有者读取。"""
import base64
import json
import math
from dataclasses import asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.response import BizError, Errors
from app.core.security import encrypt_text, decrypt_text
from app.models.database_management import DatabaseResult, DatabaseResultChunk
from app.services.database_authorization_service import require_permission, require_query_instance


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def encode_value(value):
    """JS 无损显示大整数、Decimal、日期、二进制及非有限浮点。"""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return {"$type": "int", "value": str(value)} if abs(value) > 9007199254740991 else value
    if isinstance(value, Decimal):
        return {"$type": "decimal", "value": str(value)}
    if isinstance(value, (datetime, date, time)):
        return {"$type": type(value).__name__, "value": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$type": "bytes", "value": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$type": "float", "value": str(value).replace("inf", "Infinity").replace("nan", "NaN")}
    if isinstance(value, (tuple, list)):
        return [encode_value(x) for x in value]
    if isinstance(value, dict):
        return {str(k): encode_value(v) for k, v in value.items()}
    # BSON ObjectId / UUID 等无 JSON 原生表示的值采用显式字符串。
    return str(value)


class ResultBuilder:
    """只缓存策略上限以内的已编码行；实际驱动仍需独立限制协议缓冲。"""
    def __init__(self, max_rows, max_bytes):
        self.max_rows, self.max_bytes = max_rows, max_bytes
        self.columns = []
        self.rows = []
        self.row_bytes = 0
        self.truncated = False
        self.truncate_reason = None

    def set_columns(self, columns):
        encoded = [asdict(c) if not isinstance(c, dict) else c for c in columns]
        if self.columns and self.columns != encoded:
            raise ValueError("结果列定义发生变化")
        if len(canonical({"columns": encoded, "rows": []}).encode()) > min(self.max_bytes, 65536):
            raise ValueError("结果列定义过大")
        self.columns = encoded

    def add(self, row):
        if len(self.rows) >= self.max_rows:
            self.truncated, self.truncate_reason = True, "row_limit"
            return False
        row = [encode_value(v) for v in row]
        size = len(canonical(row).encode())
        next_bytes = self.row_bytes + size + (1 if self.rows else 0)
        overhead = len(canonical({"columns": self.columns, "rows": []}).encode())
        if overhead + next_bytes > self.max_bytes:
            self.truncated, self.truncate_reason = True, "byte_limit"
            return False
        self.rows.append(row)
        self.row_bytes = next_bytes
        return True

    @property
    def returned_bytes(self):
        return len(canonical({"columns": self.columns, "rows": []}).encode()) + self.row_bytes


async def persist_result(session, task, builder):
    """与任务终态、审计、预算收缩同事务原子发布。"""
    now = datetime.now()
    manifest = encrypt_text(canonical({"columns": builder.columns}))
    result = DatabaseResult(task_id=task.id, owner_id=task.owner_id, instance_id=task.instance_id,
        manifest_enc=manifest, returned_rows=len(builder.rows), returned_bytes=builder.returned_bytes,
        truncated=builder.truncated, truncate_reason=builder.truncate_reason, finished_at=now,
        expires_at=now + timedelta(minutes=task.policy_snapshot["result_ttl_minutes"]),
        reserved_bytes=task.result_reserved_bytes, ready=False)
    session.add(result)
    await session.flush()
    groups, current, size = [], [], 2
    for row in builder.rows:
        row_size = len(canonical(row).encode()) + (1 if current else 0)
        if current and size + row_size > 262144:
            groups.append(current)
            current, size = [], 2
        current.append(row)
        size += row_size
    if current:
        groups.append(current)
    stored, start = len(manifest.encode()), 0
    for number, rows in enumerate(groups):
        cipher = encrypt_text(canonical(rows))
        session.add(DatabaseResultChunk(result_id=result.id, chunk_no=number, start_row=start,
            row_count=len(rows), payload_enc=cipher, stored_bytes=len(cipher.encode())))
        stored += len(cipher.encode())
        start += len(rows)
    if stored + len(canonical(task.result_summary or {}).encode()) > task.result_reserved_bytes:
        raise RuntimeError("结果密文超出已预留预算")
    result.stored_bytes = stored
    result.ready = True
    await session.flush()
    return result


async def read_result_page(session, actor, task, page, page_size):
    """结果无管理员/审计员后门；撤权与到期立即生效。"""
    if task.owner_id != actor.user_id:
        raise BizError(Errors.OBJECT_DENIED, "只能读取本人的结果", 403)
    if task.kind == "probe":
        await require_permission(session, actor.user_id, "database:write")
    else:
        await require_query_instance(session, actor.user_id, task.instance_id)
    row = (await session.execute(select(DatabaseResult).where(DatabaseResult.task_id == task.id))).scalar_one_or_none()
    recorded_expiry = (task.result_summary or {}).get("expires_at")
    if row is None and recorded_expiry and datetime.fromisoformat(recorded_expiry) <= datetime.now():
        raise BizError(Errors.CONFLICT, "查询结果已过期", 410, {"reason": "result_expired"})
    if row is None or not row.ready:
        raise Errors.conflict("结果尚未就绪")
    if row.expires_at is None or row.expires_at <= datetime.now():
        raise BizError(Errors.CONFLICT, "查询结果已过期", 410, {"reason": "result_expired"})
    start, end = (page-1)*page_size, page*page_size
    chunks = (await session.execute(select(DatabaseResultChunk).where(DatabaseResultChunk.result_id == row.id,
        DatabaseResultChunk.start_row < end,
        DatabaseResultChunk.start_row + DatabaseResultChunk.row_count > start).order_by(DatabaseResultChunk.chunk_no))).scalars()
    rows = []
    for chunk in chunks:
        contents = json.loads(decrypt_text(chunk.payload_enc))
        rows.extend(contents[max(0, start-chunk.start_row):min(len(contents), end-chunk.start_row)])
    return dict(columns=json.loads(decrypt_text(row.manifest_enc))["columns"], rows=rows,
        returned_rows=row.returned_rows, returned_bytes=row.returned_bytes, truncated=row.truncated,
        truncate_reason=row.truncate_reason, expires_at=row.expires_at, saved_total=row.returned_rows,
        page=page, page_size=page_size)
