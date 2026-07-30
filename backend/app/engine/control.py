"""执行控制信号：工单控制面（API）与 Pipeline 调度器之间的通信通道。

设计（02-技术架构 §4.2，已确认决策）：
- API 侧写 Redis String `ops:ctrl:{eid}` 并 PUBLISH 同名通道，信号值
  pause / resume / abort / force_abort；
- Worker 侧每个执行启动一个 watcher 协程轮询信号（0.5s），把最新信号翻译成
  ControlState 内存标志，调度器只在"四检查点"读标志——不依赖 queued 瞬间窗口；
- force_abort 额外对在跑 SSH 连接 best-effort `conn.abort()` 强杀（掐断会话）；
- watcher 只对"信号值变化"生效，避免残留的旧值（如上一轮 resume）干扰
  批间暂停等由调度器自身发起的暂停。
"""
import asyncio
import logging

from app.core import redis as redis_mod

logger = logging.getLogger("opspilot.engine.control")

# 信号值（04-API §6 工单控制面四操作）
SIG_PAUSE = "pause"
SIG_RESUME = "resume"
SIG_ABORT = "abort"
SIG_FORCE_ABORT = "force_abort"

# 信号键 TTL：7 天，覆盖"worker 长时间不在线期间下发 abort"的场景
_CTRL_TTL = 7 * 24 * 3600
# watcher 轮询间隔（秒）
_POLL_INTERVAL = 0.5


def _ctrl_key(execution_id: int) -> str:
    return redis_mod.KEY_EXEC_CTRL.format(eid=execution_id)


async def send_signal(execution_id: int, signal: str) -> None:
    """下发控制信号：写 String（worker 可能稍后才认领）并 PUBLISH（实时性）。"""
    key = _ctrl_key(execution_id)
    await redis_mod.redis_client.set(key, signal, ex=_CTRL_TTL)
    await redis_mod.redis_client.publish(key, signal)


async def read_signal(execution_id: int) -> str | None:
    """读取当前信号值（无信号返回 None）。"""
    return await redis_mod.redis_client.get(_ctrl_key(execution_id))


async def clear_signal(execution_id: int) -> None:
    """执行结束后清理信号键，避免残留影响后续（理论上一工单一执行，防御性清理）。"""
    await redis_mod.redis_client.delete(_ctrl_key(execution_id))


class ControlState:
    """单次执行的运行期控制状态：watcher 写入，调度器检查点读取。"""

    def __init__(self, execution_id: int) -> None:
        self.execution_id = execution_id
        self.abort = False              # 中止：停止派发，未派发目标置 skipped
        self.force_abort = False        # 强制中止：额外强杀在跑连接
        self.pause_requested = False    # 暂停请求（用户信号或批间暂停策略）
        # 在跑的 asyncssh 连接注册表（force_abort 强杀用）
        self._active_conns: set = set()

    def register_conn(self, conn) -> None:
        """执行器建立连接后注册，供 force_abort 强杀。"""
        self._active_conns.add(conn)

    def unregister_conn(self, conn) -> None:
        """连接正常关闭后注销。"""
        self._active_conns.discard(conn)

    def kill_active_conns(self) -> None:
        """best-effort 掐断全部在跑 SSH 会话（force-abort 语义，02 §4.2）。"""
        for conn in list(self._active_conns):
            try:
                conn.abort()
            except Exception:  # noqa: BLE001 强杀失败不影响中止流程
                logger.warning("force-abort 关闭连接失败", exc_info=True)
        self._active_conns.clear()

    @property
    def stopping(self) -> bool:
        """是否已收到中止类信号（检查点快捷判断）。"""
        return self.abort


async def watch_signals(state: ControlState, stop_event: asyncio.Event) -> None:
    """信号监视协程：轮询 Redis 信号值，仅在值变化时更新 ControlState。

    与调度器同进程同 loop，调度器在检查点读取标志即可感知；
    force_abort 在此处立刻强杀在跑连接（不等检查点）。
    """
    last: str | None = None
    while not stop_event.is_set():
        try:
            sig = await read_signal(state.execution_id)
            if sig and sig != last:
                last = sig
                if sig == SIG_ABORT:
                    state.abort = True
                elif sig == SIG_FORCE_ABORT:
                    state.abort = True
                    state.force_abort = True
                    state.kill_active_conns()
                elif sig == SIG_PAUSE:
                    state.pause_requested = True
                elif sig == SIG_RESUME:
                    state.pause_requested = False
        except Exception:  # noqa: BLE001 Redis 抖动不能中断监视循环
            logger.warning("控制信号轮询异常，继续重试", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
