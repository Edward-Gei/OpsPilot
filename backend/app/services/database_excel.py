"""数据库台账 Excel 只携带登记字段，不复制连接权限或凭据。"""
from datetime import datetime
from io import BytesIO
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.response import BizError, Errors
from app.models.database_management import DatabaseInstance
from app.schemas.database_management import InstanceWrite
from app.services.database_authorization_service import require_permission
from app.services.database_instance_service import instance_query
from app.services.database_quota_service import bucket

COLUMNS = [
    ("实例名称*", "name", 24), ("引擎*", "engine", 18), ("连接地址(host)*", "host", 30),
    ("端口*", "port", 12), ("环境*", "environment", 14), ("描述", "description", 36),
    ("业务线", "business_line", 18), ("集群名称", "cluster_name", 24),
    ("集群角色", "cluster_role", 18), ("版本", "version", 18),
]
MAX_IMPORT_BYTES = 10 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_IMPORT_ROWS = 5000


def _save(book):
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()


def build_import_template():
    """固定表头、受控枚举下拉与可删除示例行。"""
    book = Workbook()
    sheet = book.active
    sheet.title = "数据库实例导入"
    for index, (title, _, width) in enumerate(COLUMNS, 1):
        cell = sheet.cell(1, index, title)
        cell.fill = PatternFill("solid", fgColor="1E40AF")
        cell.font = Font(color="FFFFFF", bold=True)
        sheet.column_dimensions[get_column_letter(index)].width = width
    for field, choices, optional in (
        ("engine", "mysql,postgresql,mongodb,clickhouse,redis", False),
        ("environment", "demo,stage,prod", False), ("business_line", "mitrade,tradingkey", True),
    ):
        column = get_column_letter(next(i for i, (_, key, _) in enumerate(COLUMNS, 1) if key == field))
        validation = DataValidation(type="list", formula1=f'"{choices}"', allow_blank=optional)
        validation.errorTitle, validation.error, validation.showErrorMessage = "选项不正确", "请选择下拉列表中的值", True
        sheet.add_data_validation(validation)
        validation.add(f"{column}2:{column}{MAX_IMPORT_ROWS+1}")
    sheet.freeze_panes = "A2"
    sheet.append(["mysql-demo", "mysql", "db.example.internal", 3306, "demo", "示例行，导入前请删除",
                  "mitrade", "mysql-demo", "primary", "8.0"])
    return _save(book)


def _worksheet_rows(sheet, **options):
    """仅包裹第三方解码，yield 后的业务校验和写库异常保持原样。"""
    rows = sheet.iter_rows(**options)
    while True:
        try:
            row = next(rows)
        except StopIteration:
            return
        except Exception as exc:
            raise Errors.param("Excel 单元格格式损坏，请重新另存为 .xlsx 后导入") from exc
        yield row


def parse_import_rows(content):
    """先限制压缩包和工作表规模，再收集有界行，任何文件级错误都不写库。"""
    if len(content) > MAX_IMPORT_BYTES:
        raise Errors.param("Excel 文件不能超过 10 MiB")
    try:
        with ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(item.file_size for item in entries) > MAX_UNCOMPRESSED_BYTES:
                raise Errors.param("Excel 解压后体积或文件数量超出限制")
        book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    except BizError:
        raise
    except Exception as exc:
        raise Errors.param("Excel 文件解析失败，仅支持有效的 .xlsx 文件") from exc
    parsed, failures, seen = [], [], set()
    try:
        sheet = book.active
        if sheet.max_row is not None and sheet.max_row > MAX_IMPORT_ROWS + 1:
            raise Errors.param(f"单次导入最多 {MAX_IMPORT_ROWS} 行")
        if sheet.max_column is not None and sheet.max_column != len(COLUMNS):
            raise Errors.param("Excel 表头不匹配，请使用最新导入模板")
        header = next(_worksheet_rows(sheet, max_row=1, max_col=len(COLUMNS), values_only=True), ())
        if list(header) != [title for title, _, _ in COLUMNS]:
            raise Errors.param("Excel 表头不匹配，请使用最新导入模板")
        for row_number, cells in enumerate(_worksheet_rows(sheet, min_row=2, max_row=MAX_IMPORT_ROWS+2, max_col=len(COLUMNS)), 2):
            if row_number > MAX_IMPORT_ROWS+1 and any(cell.value is not None for cell in cells):
                raise Errors.param(f"单次导入最多 {MAX_IMPORT_ROWS} 行")
            if all(cell.value is None or str(cell.value).strip() == "" for cell in cells):
                continue
            if any(cell.data_type in ("f", "e") for cell in cells):
                failures.append({"row": row_number, "reason": "不支持公式或错误单元格，请填写文本或数值"})
                continue
            values = {key: (str(cell.value).strip() or None if cell.value is not None else None)
                      for (_, key, _), cell in zip(COLUMNS, cells)}
            try:
                # Excel 端口允许整数数值或十进制整数字符串，禁止截断小数。
                raw_port = values["port"] or ""
                if not raw_port.isascii() or not raw_port.isdecimal():
                    raise ValueError("端口必须为 1 至 65535 的整数")
                values["port"] = int(raw_port)
                req = InstanceWrite(**values, enabled=False)
            except (ValueError, ValidationError) as exc:
                reason = "导入字段格式不正确"
                if isinstance(exc, ValidationError):
                    error = exc.errors()[0]
                    field = error["loc"][0]
                    title = next((title.rstrip("*") for title, key, _ in COLUMNS if key == field), "字段")
                    reason = f"{title}格式不正确或超出允许范围"
                else:
                    reason = str(exc)
                failures.append({"row": row_number, "reason": reason})
                continue
            if req.name in seen:
                failures.append({"row": row_number, "reason": "文件内实例名称重复"})
                continue
            seen.add(req.name)
            parsed.append((row_number, {key: getattr(req, key) for _, key, _ in COLUMNS}))
    finally:
        book.close()
    if not parsed and not failures:
        raise Errors.param("文件无数据行")
    return parsed, failures


async def import_instances(session, actor_id, content, *, upsert=False):
    """解析与写入分离；每行仅持有短事务，失败行不回滚此前成功行。"""
    await require_permission(session, actor_id, "database:import")
    await require_permission(session, actor_id, "database:write")
    rows, failures = parse_import_rows(content)
    result = {"success_count": 0, "created_count": 0, "updated_count": 0, "failed_rows": failures}
    await session.commit()
    for row_number, fields in rows:
        try:
            await require_permission(session, actor_id, "database:import")
            await require_permission(session, actor_id, "database:write")
            await bucket(session, "global", "all")
            existing = (await session.execute(select(DatabaseInstance).where(DatabaseInstance.name == fields["name"])
                .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            if existing is not None:
                if not upsert:
                    raise Errors.conflict("实例名称已存在，可选择存在即更新")
                if existing.engine != fields["engine"]:
                    raise Errors.param("已有实例不能通过导入修改引擎")
                if existing.host != fields["host"] or existing.port != fields["port"]:
                    existing.config_revision += 1
                for key, value in fields.items():
                    setattr(existing, key, value)
                existing.updated_at = datetime.now()
                counter = "updated_count"
            else:
                session.add(DatabaseInstance(**fields, credential_id=None, enabled=False,
                    connection_options={}, last_probe_status="untested", created_by=actor_id))
                counter = "created_count"
            await session.commit()
            result[counter] += 1
            result["success_count"] += 1
        except (BizError, IntegrityError) as exc:
            await session.rollback()
            result["failed_rows"].append({"row": row_number, "reason": exc.message if isinstance(exc, BizError)
                                          else "实例名称冲突，请检查后重试"})
    result["failed_rows"].sort(key=lambda item: item["row"])
    return result


async def export_instances(session, actor_id, **filters):
    """导出列表范围内全部登记字段；文本强制为字符串，Excel 不解释公式。"""
    await require_permission(session, actor_id, "database:read")
    query, _ = await instance_query(session, actor_id, **filters)
    book = Workbook(write_only=True)
    sheet = book.create_sheet("数据库实例")
    sheet.append([title for title, _, _ in COLUMNS])
    count = 0
    async for row in (await session.stream_scalars(query)).unique():
        cells = []
        for _, field, _ in COLUMNS:
            value = getattr(row, field)
            cell = WriteOnlyCell(sheet, value=value)
            if isinstance(value, str):
                cell.data_type = "s"
            cells.append(cell)
        sheet.append(cells)
        count += 1
    return _save(book), count
