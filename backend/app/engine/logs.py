"""执行日志通道：文件落盘 + 100ms 聚合 PubSub 推送（02-技术架构 §4.3）。

落盘路径约定（api/worker 共享 exec-logs 卷）：
    {exec_log_dir}/{execution_id}/step_{order}/{ip}.log
Ansible 步骤输出无法逐目标主机拆分，统一记在 ip="ansible" 的伪主机名下。

推送模型：写入方 write() 仅入内存缓冲（非阻塞），后台协程每 100ms 把缓冲
批量 append 到文件并 PUBLISH `ops:log:{eid}`，消息体：
    {"type":"log","step_order":n,"ip":"...","lines":[...]}
"""
import asyncio
import json
import logging
import os

from app.core import redis as redis_mod
from app.core.config import settings

logger = logging.getLogger("opspilot.engine.logs")

# 聚合刷新间隔（秒）：兼顾实时性与 PubSub/磁盘写入频率
_FLUSH_INTERVAL = 0.1
# Ansible 步骤伪主机名（日志无法按目标主机拆分）
ANSIBLE_LOG_IP = "ansible"


def log_file_path(execution_id: int, step_order: int, ip: str) -> str:
    """单个（步骤, 主机）日志文件绝对路径；REST 历史日志接口共用此约定。"""
    # ip 仅来自主机快照/固定伪名，做一次基本防御避免路径穿越
    safe_ip = ip.replace("/", "_").replace("\\", "_").replace("..", "_")
    return os.path.join(
        settings.exec_log_dir, str(execution_id), f"step_{step_order}", f"{safe_ip}.log"
    )


class LogChannel:
    """单次执行的日志通道：start() 启动聚合协程，stop() 刷完缓冲后退出。"""

    def __init__(self, execution_id: int) -> None:
        self.execution_id = execution_id
        # 缓冲：{(step_order, ip): [line, ...]}
        self._buffers: dict[tuple[int, str], list[str]] = {}
        self._task: asyncio.Task | None = None
        self._stopping = False

    def write(self, step_order: int, ip: str, text: str) -> None:
        """写入日志文本（可含多行）；仅入缓冲，不做 IO。"""
        lines = text.splitlines()
        if not lines:
            return
        self._buffers.setdefault((step_order, ip), []).extend(lines)

    def start(self) -> None:
        """启动后台聚合刷新协程。"""
        self._stopping = False
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        """停止通道：等待后台协程退出并兜底刷一次缓冲。"""
        self._stopping = True
        if self._task:
            await self._task
            self._task = None
        await self._flush()

    async def _run(self) -> None:
        """聚合循环：每 100ms 刷一次缓冲（文件 append + PubSub 推送）。"""
        while not self._stopping:
            await asyncio.sleep(_FLUSH_INTERVAL)
            await self._flush()

    async def _flush(self) -> None:
        """把全部缓冲落盘并推送；单个键失败不影响其余。"""
        if not self._buffers:
            return
        buffers, self._buffers = self._buffers, {}
        channel = redis_mod.KEY_EXEC_LOG.format(eid=self.execution_id)
        for (step_order, ip), lines in buffers.items():
            try:
                path = log_file_path(self.execution_id, step_order, ip)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                await redis_mod.redis_client.publish(
                    channel,
                    json.dumps(
                        {"type": "log", "step_order": step_order, "ip": ip, "lines": lines},
                        ensure_ascii=False,
                    ),
                )
            except Exception:  # noqa: BLE001 日志通道故障不能中断执行
                logger.warning("日志刷新失败 execution=%s step=%s ip=%s",
                               self.execution_id, step_order, ip, exc_info=True)


def read_log_lines(
    execution_id: int, step_order: int, ip: str, *, offset: int, limit: int,
) -> tuple[list[str], int, bool]:
    """读取历史日志（REST GET /executions/{id}/logs）。

    返回 (lines, next_offset, eof)；offset 为行号（从 0 开始）。
    """
    path = log_file_path(execution_id, step_order, ip)
    if not os.path.exists(path):
        return [], offset, True
    lines: list[str] = []
    eof = True
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i < offset:
                continue
            if len(lines) >= limit:
                eof = False  # 还有后续行
                break
            lines.append(line.rstrip("\n"))
    return lines, offset + len(lines), eof
