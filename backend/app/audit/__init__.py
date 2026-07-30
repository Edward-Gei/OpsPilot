"""审计模块：异步写入器（02-技术架构 §5.2）。

用法：service 层显式调用 `audit.log(...)`，事件先进内存队列，
后台任务批量落库，避免阻塞请求；进程退出前 flush。
"""
from app.audit.writer import audit_writer, log

__all__ = ["audit_writer", "log"]
