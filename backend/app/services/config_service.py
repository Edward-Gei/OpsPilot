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


async def get_config(session: AsyncSession, key: str):
    """读取单个配置值（解包 {"value": ...}）；库中缺键回退代码默认值。"""
    row = (
        await session.execute(select(SystemConfig).where(SystemConfig.cfg_key == key))
    ).scalar_one_or_none()
    if row is not None and row.cfg_value is not None:
        return row.cfg_value.get("value")
    default = SYSTEM_CONFIG_DEFAULTS.get(key)
    return default.get("value") if default else None


async def get_all_configs(session: AsyncSession) -> dict:
    """读取全部配置（库值覆盖默认值），供 GET /system/configs。"""
    merged = {k: v.get("value") for k, v in SYSTEM_CONFIG_DEFAULTS.items()}
    for row in (await session.execute(select(SystemConfig))).scalars():
        if row.cfg_value is not None:
            merged[row.cfg_key] = row.cfg_value.get("value")
    return merged


async def set_config(session: AsyncSession, key: str, value, updated_by: int | None) -> None:
    """更新单个配置键；仅允许预置键（防止任意键写入）。"""
    if key not in SYSTEM_CONFIG_DEFAULTS:
        raise Errors.param(f"未知配置键: {key}")
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
