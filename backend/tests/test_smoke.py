"""M0 冒烟测试：不依赖 MySQL/Redis 的纯逻辑校验。

覆盖：
    1. 响应包裹与业务异常约定
    2. 权限矩阵与常量的完整性（角色引用的权限点必须存在）
    3. ORM 元数据完整性（26 张表全部注册）
    4. FastAPI 应用可构建、路由已挂载
"""
import pytest

from app.core.constants import BUILTIN_ROLES, PERMISSIONS, SYSTEM_CONFIG_DEFAULTS
from app.core.response import BizError, Errors, ok


def test_ok_envelope():
    """成功响应必须是 {code:0, message, data} 结构。"""
    body = ok({"a": 1})
    assert body == {"code": 0, "message": "ok", "data": {"a": 1}}


def test_biz_error_factory():
    """异常工厂应携带正确的业务码与 HTTP 状态码。"""
    err = Errors.not_found()
    assert isinstance(err, BizError)
    assert err.code == Errors.NOT_FOUND
    assert err.http_status == 404


def test_permission_codes_unique():
    """权限点编码不允许重复。"""
    codes = [p[0] for p in PERMISSIONS]
    assert len(codes) == len(set(codes))


def test_builtin_roles_reference_valid_permissions():
    """内置角色矩阵引用的权限点必须全部存在于权限全集。"""
    all_codes = {p[0] for p in PERMISSIONS}
    for role_code, meta in BUILTIN_ROLES.items():
        unknown = set(meta["permissions"]) - all_codes
        assert not unknown, f"角色 {role_code} 引用了未定义权限点: {unknown}"


def test_admin_role_has_all_permissions():
    """admin 必须拥有全部权限点。"""
    assert set(BUILTIN_ROLES["admin"]["permissions"]) == {p[0] for p in PERMISSIONS}


def test_system_config_defaults_wrapped():
    """system_config 默认值统一使用 {value: ...} 包裹，便于前端/服务层统一读取。"""
    for key, val in SYSTEM_CONFIG_DEFAULTS.items():
        assert isinstance(val, dict) and "value" in val, f"配置 {key} 未按约定包裹"


def test_metadata_contains_all_tables():
    """ORM 元数据必须收录 03-数据库设计 定义的全部 26 张表（执行范式改造后）。"""
    from app.models import Base

    expected = {
        "user", "role", "permission", "user_role", "role_permission", "api_token",
        "host", "application", "app_host", "job_host",
        "credential", "ticket_template", "process_template", "process_step",
        "ticket", "ticket_step", "ticket_approval",
        "ticket_parameter_prepare",
        "execution", "execution_step",
        "notify_channel", "notify_channel_event", "notification_record",
        "user_notification",
        "audit_log", "system_config",
    }
    assert set(Base.metadata.tables.keys()) == expected


def test_app_routes_mounted():
    """应用可构建，healthz 与 /api/v1/ping 路由存在。"""
    pytest.importorskip("fastapi")
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
    assert "/api/v1/ping" in paths
