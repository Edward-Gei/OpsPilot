# -*- coding: utf-8 -*-
"""Webhook/Teams 通知模拟端点（M8 冲突声明 C1）。

- POST 任意路径：记录一次调用（路径/时间/请求体摘要），返回 200 {"ok": true}
- GET /stats：返回各路径累计计数与最近 10 条记录，供 DoD-3 验证"通知已落端点"
标准库实现，无第三方依赖，可直接跑在 python:3.11-slim 容器内。
"""
import json
from collections import Counter, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 各路径命中计数 + 最近 10 条请求明细（内存态，容器重启归零即可）
COUNTS: Counter = Counter()
RECENT: deque = deque(maxlen=10)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code: int, body: dict) -> None:
        """统一 JSON 应答。"""
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802
        """接收通知：记录后固定返回 200（Webhook/Teams 渠道以 2xx 判成功）。"""
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        COUNTS[self.path] += 1
        RECENT.append({
            "path": self.path,
            "time": datetime.now().isoformat(timespec="seconds"),
            "body": raw.decode("utf-8", "replace")[:500],
        })
        print(f"[webhook-echo] {self.path} <- {raw[:200]!r}", flush=True)
        self._reply(200, {"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        """GET /stats 查询计数；其余路径 200 探活。"""
        if self.path.startswith("/stats"):
            self._reply(200, {"counts": dict(COUNTS), "recent": list(RECENT)})
        else:
            self._reply(200, {"ok": True})

    def log_message(self, *args) -> None:  # 静默默认访问日志，避免刷屏
        return


if __name__ == "__main__":
    print("[webhook-echo] listening :9000", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
