"""系统配置服务：system_config 键值读写（预置键见 constants.SYSTEM_CONFIG_DEFAULTS）。

存储格式：cfg_value 统一包 {"value": ...}；读取时缺键回退代码默认值，
保证新增配置键无需数据迁移即可生效。
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import DATABASE_CONFIG_RANGES, SYSTEM_CONFIG_DEFAULTS
from app.core.response import Errors
from app.models.audit import SystemConfig


def _token_policy(value) -> dict:
    """旧策略只继承会话时长，弃用的令牌 TTL 不再对外返回或生效。"""
    hours = value.get("session_hours", 8) if isinstance(value, dict) else 8
    return {"session_hours": hours if type(hours) is int and 1 <= hours <= 168 else 8}


async def get_config(session: AsyncSession, key: str):
    """读取单个配置值（解包 {"value": ...}）；库中缺键回退代码默认值。"""
    row = (
        await session.execute(select(SystemConfig).where(SystemConfig.cfg_key == key))
    ).scalar_one_or_none()
    if row is not None and row.cfg_value is not None:
        value = row.cfg_value.get("value")
        return _token_policy(value) if key == "token.policy" else value
    default = SYSTEM_CONFIG_DEFAULTS.get(key)
    return default.get("value") if default else None


async def get_all_configs(session: AsyncSession) -> dict:
    """读取全部配置（库值覆盖默认值），供 GET /system/configs。"""
    merged = {k: v.get("value") for k, v in SYSTEM_CONFIG_DEFAULTS.items()}
    for row in (await session.execute(select(SystemConfig))).scalars():
        if row.cfg_value is not None:
            merged[row.cfg_key] = row.cfg_value.get("value")
    merged["token.policy"] = _token_policy(merged["token.policy"])
    return merged


def _validate_value(key: str, value) -> None:
    """写入前检查单键，防止类型强转扩大查询时限或资源分配。"""
    if key not in SYSTEM_CONFIG_DEFAULTS:
        raise Errors.param(f"未知配置键: {key}")
    if key in DATABASE_CONFIG_RANGES:
        low, high = DATABASE_CONFIG_RANGES[key]
        if type(value) is not int or not low <= value <= high:
            raise Errors.param(f"{key} 必须为 {low}-{high} 的整数")
    if key == "token.policy":
        if not isinstance(value, dict) or set(value) != {"session_hours"}:
            raise Errors.param("令牌策略格式不正确")
        hours = value["session_hours"]
        if type(hours) is not int or not 1 <= hours <= 168:
            raise Errors.param("令牌策略 session_hours 必须为 1-168 的整数")


async def set_configs(session: AsyncSession, configs: dict, updated_by: int | None) -> None:
    """整批验证合并后的策略后写库，不让键顺序造成部分生效或误拒绝。"""
    for key, value in configs.items():
        _validate_value(key, value)
    if any(key.startswith("database.") for key in configs):
        merged = {**await get_all_configs(session), **configs}
        for key in DATABASE_CONFIG_RANGES:
            _validate_value(key, merged[key])
        if merged["database.global_concurrency"] <= merged["database.metadata_reserved_concurrency"]:
            raise Errors.param("全局并发必须大于元数据保留并发")
        if merged["database.user_queue_max_tasks"] > merged["database.queue_max_tasks"]:
            raise Errors.param("用户排队上限不能超过全局排队上限")
        if merged["database.result_storage_budget_bytes"] < 2 * merged["database.max_result_bytes"] + 65536:
            raise Errors.param("结果存储预算不足以预留单次最大结果")
    for key, value in configs.items():
        await _write_config(session, key, value, updated_by)


async def set_config(session: AsyncSession, key: str, value, updated_by: int | None) -> None:
    """单键写入沿用整批策略验证。"""
    await set_configs(session, {key: value}, updated_by)


async def _write_config(session: AsyncSession, key: str, value, updated_by: int | None) -> None:
    """调用方完成整批验证后，复用同一事务保存。"""
    row = (
        await session.execute(select(SystemConfig).where(SystemConfig.cfg_key == key))
    ).scalar_one_or_none()
    if row is None:
        row = SystemConfig(cfg_key=key)
        session.add(row)
    row.cfg_value = {"value": value}
    row.updated_by = updated_by
    row.updated_at = datetime.now()
    await session.flush()
