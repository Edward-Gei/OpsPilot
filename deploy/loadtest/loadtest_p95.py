# -*- coding: utf-8 -*-
"""M8-2 列表接口 P95 压测（在 api 容器内执行，冲突声明 C4）。

用法：docker cp deploy/loadtest/loadtest_p95.py opspilot-api:/tmp/ \
      && docker exec opspilot-api python /tmp/loadtest_p95.py

方法：对代表性列表接口各发 REQUESTS 次请求（CONCURRENCY 并发协程），统计
P50/P95/P99/max；验收线 P95 < 500ms（PRD §5 非功能需求）。
数据前提：先跑 seed_loadtest.py（主机 1000 台、审计已有历史数据）。
"""
import asyncio
import time

import httpx

from app.core.security import create_token

BASE = "http://localhost:8000/api/v1"
TOKEN = create_token(1, "admin", "access")[0]
REQUESTS = 200       # 每接口总请求数
CONCURRENCY = 10     # 并发协程数（模拟多用户同时刷列表）

# 代表性列表接口：主表分页 + 模糊筛选 + 大表（审计分区表）
TARGETS: list[tuple[str, str]] = [
    ("主机列表(千行库)", "/cmdb/hosts?page=1&page_size=20"),
    ("主机列表+keyword", "/cmdb/hosts?page=1&page_size=20&keyword=lt-real"),
    ("主机深分页", "/cmdb/hosts?page=25&page_size=20"),
    ("应用列表", "/cmdb/apps?page=1&page_size=20"),
    ("用户列表", "/users?page=1&page_size=20"),
    ("模板列表", "/templates?page=1&page_size=20"),
    ("工单列表", "/tickets?page=1&page_size=20"),
    ("执行列表", "/executions?page=1&page_size=20"),
    ("审计日志(分区表)", "/audit/logs?page=1&page_size=20"),
    ("审计+组合筛选", "/audit/logs?page=1&page_size=20&module=auth&result=success"),
]


async def bench_one(client: httpx.AsyncClient, path: str) -> list[float]:
    """单接口压测：CONCURRENCY 协程共摊 REQUESTS 次请求，返回耗时毫秒列表。"""
    latencies: list[float] = []
    lock = asyncio.Lock()
    remaining = REQUESTS

    async def worker() -> None:
        nonlocal remaining
        while True:
            async with lock:
                if remaining <= 0:
                    return
                remaining -= 1
            t0 = time.perf_counter()
            resp = await client.get(BASE + path)
            elapsed = (time.perf_counter() - t0) * 1000
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"
            latencies.append(elapsed)

    await asyncio.gather(*[worker() for _ in range(CONCURRENCY)])
    return latencies


def pct(data: list[float], p: float) -> float:
    """百分位（最近秩法）。"""
    s = sorted(data)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s)) - 1))]


async def main() -> None:
    """逐接口压测并输出报表；任一接口 P95 超线则退出码 1。"""
    failed = False
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30
    ) as client:
        # 预热：建连接池 + 触发首次查询计划缓存
        for _, path in TARGETS:
            await client.get(BASE + path)
        print(f"{'接口':<24}{'P50':>8}{'P95':>8}{'P99':>8}{'MAX':>8}  结论")
        print("-" * 66)
        for name, path in TARGETS:
            lat = await bench_one(client, path)
            p50, p95, p99 = pct(lat, 50), pct(lat, 95), pct(lat, 99)
            verdict = "PASS" if p95 < 500 else "FAIL"
            if verdict == "FAIL":
                failed = True
            print(f"{name:<24}{p50:>7.1f}ms{p95:>6.1f}ms{p99:>6.1f}ms{max(lat):>6.1f}ms  {verdict}")
        print("-" * 66)
        print(f"每接口 {REQUESTS} 次 / 并发 {CONCURRENCY}；验收线 P95 < 500ms")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
