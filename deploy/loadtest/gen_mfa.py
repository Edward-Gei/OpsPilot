# -*- coding: utf-8 -*-
"""临时工具：生成 admin 的 MFA 动态码（当前起 10 个时间窗，浏览器走查用）。"""
import asyncio
import time

import pyotp
from sqlalchemy import text

from app.core.database import async_session_factory
from app.core.security import decrypt_text


async def main() -> None:
    async with async_session_factory() as s:
        row = (await s.execute(
            text("select mfa_secret_enc from user where username='admin'")
        )).scalar_one()
    totp = pyotp.TOTP(decrypt_text(row))
    now = int(time.time())
    for i in range(10):
        print(f"+{i*30:3d}s {totp.at(now + i * 30)}")


asyncio.run(main())
