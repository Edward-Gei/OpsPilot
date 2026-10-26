"""系统配置服务：system_config 键值读写（预置键见 constants.SYSTEM_CONFIG_DEFAULTS）。

存储格式：cfg_value 统一包 {"value": ...}；读取时缺键回退代码默认值，
保证新增配置键无需数据迁移即可生效。
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SYSTEM_CONFIG_DEFAULTS
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


async def set_config(session: AsyncSession, key: str, value, updated_by: int | None) -> None:
    """更新单个配置键；仅允许预置键（防止任意键写入）。"""
    if key not in SYSTEM_CONFIG_DEFAULTS:
        raise Errors.param(f"未知配置键: {key}")
    if key == "token.policy":
        if not isinstance(value, dict) or set(value) != {"session_hours"}:
            raise Errors.param("令牌策略格式不正确")
        hours = value["session_hours"]
        if type(hours) is not int or not 1 <= hours <= 168:
            raise Errors.param("令牌策略 session_hours 必须为 1-168 的整数")
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
