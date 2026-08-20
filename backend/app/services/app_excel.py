"""应用 Excel 导出。"""
from io import BytesIO

from openpyxl import Workbook

from app.models.cmdb import Application


def export_apps(apps: list[Application], host_stats: dict[int, dict]) -> bytes:
    """导出应用及其关联主机摘要。"""
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("应用列表")
    ws.append(["应用名", "开发语言", "部署方式", "项目类型", "关联主机数", "主机 IP", "说明", "创建时间"])
    for app in apps:
        stats = host_stats.get(app.id, {})
        ws.append([
            app.name,
            app.language,
            app.deploy_type,
            {"frontend": "前端", "backend": "后端"}.get(app.project_type, app.project_type),
            stats.get("host_count", 0),
            ", ".join(stats.get("host_ips", [])),
            app.description,
            app.created_at.strftime("%Y-%m-%d %H:%M:%S") if app.created_at else None,
        ])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
