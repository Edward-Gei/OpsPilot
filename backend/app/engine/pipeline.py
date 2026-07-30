"""Pipeline 调度器：单次执行的完整生命周期（02-技术架构 §4.1/§4.2）。

调度模型（已确认决策）：
- 步骤严格串行；步骤内目标主机按 batch_size 分批，批内 asyncio 并发
  （批内并发 = min(策略 concurrency, 全局并发信号量)）；
- 四检查点响应控制信号：①认领开跑前 ②每主机派发前 ③每批结束后 ④每步骤开始前；
- 暂停/中止默认不打断在跑目标（等待跑完），force-abort 由 watcher 强杀连接；
- 失败策略：fail_fast=true 时批结束发现失败 → 剩余目标置 skipped 并中断后续步骤；
- 批间暂停：batch_pause=true 时每批结束自动进入 paused，等待 resume 放行；
- 终态映射（03 §5.1）：success→success / failed→failed /
  terminated→interrupted(user_abort)；引擎内异常→interrupted(system_crash)。

并发与会话：调度主流程用单一 AsyncSession 顺序读写；批内并发的主机行更新
走独立短会话（AsyncSession 非并发安全），避免跨协程共享会话。
"""
import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select, update

from app import audit

from app.core.constants import (
    ExecutionStatus,
    HostExecStatus,
    InterruptReason,
    NotifyEvent,
    ScriptType,
    TicketStatus,
)
from app.core.database import async_session_factory
from app.models.auth import User
from app.models.execution import Execution, ExecutionStep, ExecutionStepHost
from app.models.job import Credential
from app.models.ticket import Ticket, TicketStep
from app.engine import ansible_runner, control as ctrl, events, ssh_runner
from app.engine.logs import LogChannel
from app.services import config_service, notify_service

logger = logging.getLogger("opspilot.engine.pipeline")

# 全局 SSH 并发信号量（跨执行共享，worker 启动时按系统配置初始化）
_global_sem: asyncio.Semaphore | None = None
# 策略缺省值
_DEFAULT_CONCURRENCY = 5


def init_global_semaphore(value: int) -> None:
    """按系统配置 exec.global_concurrency 初始化全局并发信号量（worker 启动调用）。"""
    global _global_sem
    _global_sem = asyncio.Semaphore(max(1, value))


def _get_global_sem() -> asyncio.Semaphore:
    """取全局并发信号量；未初始化时惰性创建（单测/降级场景用默认值）。"""
    global _global_sem
    if _global_sem is None:
        from app.core.config import settings
        _global_sem = asyncio.Semaphore(settings.exec_global_concurrency_default)
    return _global_sem


def _chunk(items: list, size: int) -> list[list]:
    """按 batch_size 切批；size<=0 表示不分批（单批全量）。"""
    if size <= 0 or size >= len(items):
        return [items]
    return [items[i:i + size] for i in range(0, len(items), size)]


def render_script(content: str, params: dict) -> str:
    """用 Jinja2 沙箱渲染脚本参数（02 §4.4：SandboxedEnvironment 防模板注入）。"""
    env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=True)
    return env.from_string(content).render(**(params or {}))


async def _update_host_row(row_id: int, **values) -> None:
    """独立短会话更新主机行（批内并发协程调用，避免共享主会话）。"""
    async with async_session_factory() as s:
        await s.execute(
            update(ExecutionStepHost).where(ExecutionStepHost.id == row_id).values(**values)
        )
        await s.commit()


class PipelineRunner:
    """一次执行的调度器实例：worker 认领消息（已 XACK）后调用 run()。"""

    def __init__(self, execution_id: int) -> None:
        self.eid = execution_id
        self.control = ctrl.ControlState(execution_id)
        self.log = LogChannel(execution_id)
        self._watch_stop = asyncio.Event()
        self.session = None            # 主流程会话（顺序使用）
        self.execution: Execution = None
        self.ticket: Ticket = None
        self.strategy: dict = {}
        self.credentials: dict[int, Credential] = {}
        self.job_host_cfg: dict | None = None
        self.job_host_cred: Credential | None = None
        # 工单主机快照 id → ssh_port（ExecutionStepHost 不冗余端口，入口处预加载）
        self._port_map: dict[int, int] = {}

    # ---------- 入口 ----------

    async def run(self) -> None:
        """执行主流程：加载上下文 → 逐步骤调度 → 终态映射落库与通知。"""
        watcher = asyncio.get_running_loop().create_task(
            ctrl.watch_signals(self.control, self._watch_stop)
        )
        self.log.start()
        try:
            async with async_session_factory() as session:
                self.session = session
                if not await self._load_context():
                    return
                # 检查点①：认领开跑前（覆盖 queued 阶段下发的 abort）
                if self.control.abort:
                    await self._finalize_terminated()
                    return
                await self._mark_running()
                try:
                    aborted = await self._run_steps()
                except Exception:
                    # 引擎内异常：非业务失败，按系统崩溃口径中断（决策三）
                    logger.exception("执行 %s 引擎异常", self.eid)
                    await self._finalize_interrupted(InterruptReason.SYSTEM_CRASH.value)
                    return
                if aborted:
                    await self._finalize_terminated()
                else:
                    await self._finalize_normal()
        finally:
            self._watch_stop.set()
            await asyncio.gather(watcher, return_exceptions=True)
            await self.log.stop()
            await ctrl.clear_signal(self.eid)

    # ---------- 上下文加载 ----------

    async def _load_context(self) -> bool:
        """加载执行/工单/步骤/主机/凭据快照；非 queued 状态直接跳过（重复消息防御）。"""
        s = self.session
        self.execution = await s.get(Execution, self.eid)
        if self.execution is None:
            logger.error("执行 %s 不存在，丢弃消息", self.eid)
            return False
        if self.execution.status != ExecutionStatus.QUEUED.value:
            logger.warning("执行 %s 状态为 %s（非 queued），跳过", self.eid, self.execution.status)
            return False
        self.ticket = await s.get(Ticket, self.execution.ticket_id)
        self.strategy = self.ticket.exec_strategy_snap or {}
        # 步骤凭据一次性加载（快照步骤 → credential）
        cred_ids = set(
            (await s.execute(
                select(TicketStep.credential_id).where(TicketStep.ticket_id == self.ticket.id)
            )).scalars()
        )
        # Ansible 作业主机配置（含 playbook 步骤时需要）
        self.job_host_cfg = await config_service.get_config(s, "ansible.job_host")
        if self.job_host_cfg and self.job_host_cfg.get("credential_id"):
            cred_ids.add(int(self.job_host_cfg["credential_id"]))
        for cred in (await s.execute(select(Credential).where(Credential.id.in_(cred_ids)))).scalars():
            self.credentials[cred.id] = cred
        if self.job_host_cfg and self.job_host_cfg.get("credential_id"):
            self.job_host_cred = self.credentials.get(int(self.job_host_cfg["credential_id"]))
        return True

    # ---------- 状态迁移辅助 ----------

    async def _set_status(self, exec_status: str, ticket_status: str | None = None) -> None:
        """同步更新 execution 与工单状态并广播 execution_status 事件。"""
        self.execution.status = exec_status
        if ticket_status:
            self.ticket.status = ticket_status
        await self.session.commit()
        await events.publish_event(
            self.eid, events.KIND_EXECUTION,
            {"status": exec_status, "ticket_status": self.ticket.status},
        )

    async def _mark_running(self) -> None:
        """认领即开跑：execution/工单 → running（XACK 已由 worker 在认领时完成）。"""
        self.execution.started_at = datetime.now()
        await self._set_status(ExecutionStatus.RUNNING.value, TicketStatus.RUNNING.value)
        # 审计：执行开始（系统动作无操作人，与 recovery 口径一致；M7-1 埋点补全）
        audit.log(
            module="execution", action="execution.start",
            target_type="execution", target_id=str(self.eid),
            target_name=self.ticket.ticket_no,
            detail={"ticket_id": self.ticket.id, "ticket_title": self.ticket.title},
        )

    async def _checkpoint(self) -> bool:
        """检查点通用逻辑：处理暂停等待；返回 False 表示应中止调度。"""
        if self.control.abort:
            return False
        if self.control.pause_requested:
            await self._set_status(ExecutionStatus.PAUSED.value, TicketStatus.PAUSED.value)
            # 停住等待 resume / abort（在跑目标已各自跑完才会走到这里）
            while self.control.pause_requested and not self.control.abort:
                await asyncio.sleep(0.5)
            if self.control.abort:
                return False
            await self._set_status(ExecutionStatus.RUNNING.value, TicketStatus.RUNNING.value)
        return True

    # ---------- 步骤调度 ----------

    async def _run_steps(self) -> bool:
        """逐步骤串行调度；返回是否被中止（abort）。"""
        s = self.session
        steps = list((await s.execute(
            select(ExecutionStep).where(ExecutionStep.execution_id == self.eid)
            .order_by(ExecutionStep.step_order)
        )).scalars())
        ticket_steps = {
            t.step_order: t
            for t in (await s.execute(
                select(TicketStep).where(TicketStep.ticket_id == self.ticket.id)
            )).scalars()
        }
        fail_fast = bool(self.strategy.get("fail_fast", True))
        for step in steps:
            # 检查点④：每步骤开始前
            if not await self._checkpoint():
                return True
            tstep = ticket_steps[step.step_order]
            step_failed = await self._run_one_step(step, tstep)
            if self.control.abort:
                return True
            if step_failed and fail_fast:
                # 失败中断：剩余步骤不再执行（剩余 pending 主机在 finalize 统一置 skipped）
                logger.info("执行 %s 步骤 %s 失败且 fail_fast，开启中断后续步骤", self.eid, step.step_order)
                break
        return False

    async def _run_one_step(self, step: ExecutionStep, tstep: TicketStep) -> bool:
        """执行单个步骤（分批推进）；返回该步骤是否存在失败。"""
        s = self.session
        hosts = list((await s.execute(
            select(ExecutionStepHost)
            .where(ExecutionStepHost.execution_step_id == step.id)
            .order_by(ExecutionStepHost.id)
        )).scalars())
        batch_size = int(self.strategy.get("batch_size") or 0)
        batch_pause = bool(self.strategy.get("batch_pause"))
        fail_fast = bool(self.strategy.get("fail_fast", True))
        batches = _chunk(hosts, batch_size)

        step.status = ExecutionStatus.RUNNING.value
        step.started_at = datetime.now()
        step.total_batch = len(batches)
        # 落批次号（提交时默认 1，此处按实际分批修正）
        for bi, batch in enumerate(batches, start=1):
            for h in batch:
                h.batch_no = bi
        await s.commit()
        await self._publish_step(step)

        # 渲染脚本参数（渲染失败 = 整步骤失败，不派发任何主机）
        try:
            script = render_script(tstep.content_snap, tstep.params or {})
        except Exception as exc:  # noqa: BLE001 模板渲染错误属用户输入问题
            log_ip = "ansible" if tstep.script_type_snap == ScriptType.PLAYBOOK.value else (hosts[0].ip if hosts else "-")
            self.log.write(step.step_order, log_ip, f"[opspilot] 脚本参数渲染失败: {exc}")
            for h in hosts:
                h.status = HostExecStatus.FAILED.value
                h.error_summary = f"参数渲染失败: {exc}"[:500]
            step.status = ExecutionStatus.FAILED.value
            step.failed_count = len(hosts)
            step.finished_at = datetime.now()
            await s.commit()
            await self._publish_step(step)
            return True

        credential = self.credentials.get(tstep.credential_id)
        step_has_failure = False
        for bi, batch in enumerate(batches, start=1):
            # 检查点③（上一批结束）+ 暂停处理
            if not await self._checkpoint():
                break
            step.current_batch = bi
            await s.commit()
            await self._publish_step(step)
            if tstep.script_type_snap == ScriptType.PLAYBOOK.value:
                results = await self._run_batch_playbook(step, batch, credential, script, tstep.timeout)
            else:
                results = await self._run_batch_shell(step, batch, credential, script, tstep.timeout)
            # 汇总批结果（terminated 不计失败：人为停止不污染失败统计）
            batch_failed = sum(
                1 for st in results if st in (HostExecStatus.FAILED.value, HostExecStatus.TIMEOUT.value)
            )
            batch_success = sum(1 for st in results if st == HostExecStatus.SUCCESS.value)
            step.failed_count += batch_failed
            step.success_count += batch_success
            await s.commit()
            if batch_failed:
                step_has_failure = True
                if fail_fast:
                    break
            if self.control.abort:
                break
            # 批间暂停策略：非最后一批自动停住，等待 resume 放行（02 §4.2）
            if batch_pause and bi < len(batches):
                self.control.pause_requested = True

        # 步骤收尾：按中止/失败/成功归档
        if self.control.abort:
            step.status = ExecutionStatus.TERMINATED.value
        elif step_has_failure:
            step.status = ExecutionStatus.FAILED.value
        else:
            step.status = ExecutionStatus.SUCCESS.value
        step.finished_at = datetime.now()
        await s.commit()
        await self._publish_step(step)
        return step_has_failure

    async def _publish_step(self, step: ExecutionStep) -> None:
        """广播步骤状态事件。"""
        await events.publish_event(
            self.eid, events.KIND_STEP,
            {"step_order": step.step_order, "status": step.status,
             "current_batch": step.current_batch, "total_batch": step.total_batch,
             "success_count": step.success_count, "failed_count": step.failed_count},
        )

    async def _publish_host(self, row: ExecutionStepHost, step_order: int) -> None:
        """广播主机状态事件（批内并发协程使用，仅读 row 内存值）。"""
        await events.publish_event(
            self.eid, events.KIND_HOST,
            {"step_order": step_order, "ip": row.ip, "status": row.status,
             "exit_code": row.exit_code, "error_summary": row.error_summary},
        )

    # ---------- 批执行（Shell / Playbook） ----------

    async def _run_batch_shell(
        self, step: ExecutionStep, batch: list[ExecutionStepHost],
        credential: Credential | None, script: str, timeout: int,
    ) -> list[str]:
        """Shell 批：批内并发直连各目标主机；返回批内各主机最终状态列表。"""
        concurrency = int(self.strategy.get("concurrency") or _DEFAULT_CONCURRENCY)
        batch_sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(row: ExecutionStepHost) -> str:
            # 检查点②：派发前发现中止 → 不再启动，保持 pending（finalize 置 skipped）
            if self.control.abort:
                return HostExecStatus.PENDING.value
            async with _get_global_sem():
                async with batch_sem:
                    if self.control.abort:
                        return HostExecStatus.PENDING.value
                    row.status = HostExecStatus.RUNNING.value
                    row.started_at = datetime.now()
                    await _update_host_row(row.id, status=row.status, started_at=row.started_at)
                    await self._publish_host(row, step.step_order)
                    if credential is None:
                        status, exit_code, summary = (
                            HostExecStatus.FAILED.value, None, "步骤凭据不存在（可能已被删除）"
                        )
                    else:
                        # ssh_port 取自主机快照（ExecutionStepHost 不冗余端口，回查 ticket_host 快照见装配时冗余 ip）
                        status, exit_code, summary = await ssh_runner.run_shell_on_host(
                            ip=row.ip, port=self._host_port(row), credential=credential,
                            script=script, timeout=timeout, step_order=step.step_order,
                            log=self.log, control=self.control,
                        )
                    row.status, row.exit_code, row.error_summary = status, exit_code, summary
                    row.finished_at = datetime.now()
                    await _update_host_row(
                        row.id, status=status, exit_code=exit_code,
                        error_summary=summary, finished_at=row.finished_at,
                    )
                    await self._publish_host(row, step.step_order)
                    return status

        results = await asyncio.gather(*(_one(h) for h in batch))
        return list(results)

    def _host_port(self, row: ExecutionStepHost) -> int:
        """主机 SSH 端口：从工单主机快照映射（入口处预加载 self._port_map）。"""
        return self._port_map.get(row.ticket_host_id, 22)

    async def _run_batch_playbook(
        self, step: ExecutionStep, batch: list[ExecutionStepHost],
        credential: Credential | None, playbook: str, timeout: int,
    ) -> list[str]:
        """Playbook 批：整批一次提交作业主机执行，RECAP 逐主机归档。"""
        now = datetime.now()
        for row in batch:
            row.status = HostExecStatus.RUNNING.value
            row.started_at = now
        await self.session.commit()
        for row in batch:
            await self._publish_host(row, step.step_order)

        if not self.job_host_cfg or not self.job_host_cred:
            results = {row.ip: (HostExecStatus.FAILED.value, "未配置 Ansible 作业主机（系统设置）") for row in batch}
            self.log.write(step.step_order, "ansible", "[opspilot] 未配置 Ansible 作业主机，请联系管理员在系统设置中配置")
        elif credential is None:
            results = {row.ip: (HostExecStatus.FAILED.value, "步骤凭据不存在（可能已被删除）") for row in batch}
        else:
            # 构造带端口的轻量主机视图供 inventory 生成
            hosts_view = [
                SimpleNamespace(ip=row.ip, ssh_port=self._host_port(row))
                for row in batch
            ]
            results = await ansible_runner.run_playbook_step(
                job_host_cfg=self.job_host_cfg, job_host_credential=self.job_host_cred,
                step_credential=credential, hosts=hosts_view, playbook_content=playbook,
                timeout=timeout, execution_id=self.eid, step_order=step.step_order,
                log=self.log, control=self.control,
            )
        statuses: list[str] = []
        finished = datetime.now()
        for row in batch:
            status, summary = results.get(row.ip, (HostExecStatus.FAILED.value, "无执行结果"))
            row.status, row.error_summary, row.finished_at = status, summary, finished
            statuses.append(status)
        await self.session.commit()
        for row in batch:
            await self._publish_host(row, step.step_order)
        return statuses

    # ---------- 终态归档 ----------

    async def _skip_pending_hosts(self) -> None:
        """把全执行范围内仍 pending 的主机行置 skipped（中止/失败中断的目标级标记）。"""
        await self.session.execute(
            update(ExecutionStepHost)
            .where(ExecutionStepHost.execution_id == self.eid,
                   ExecutionStepHost.status == HostExecStatus.PENDING.value)
            .values(status=HostExecStatus.SKIPPED.value)
        )

    async def _finalize_ticket(self, ticket_status: str, reason: str | None) -> None:
        """工单终态落库 + 按事件通知创建人（映射表见 03 §5.1）。"""
        self.ticket.status = ticket_status
        self.ticket.interrupt_reason = reason
        self.ticket.finished_at = datetime.now()
        self.execution.finished_at = datetime.now()
        await self.session.commit()
        # 通知事件：success/failed/interrupted 三类（M6 前只落 record）
        creator = await self.session.get(User, self.ticket.creator_id)
        creator_name = creator.username if creator else str(self.ticket.creator_id)
        event_map = {
            TicketStatus.SUCCESS.value: (NotifyEvent.EXECUTION_SUCCESS, "执行成功"),
            TicketStatus.FAILED.value: (NotifyEvent.EXECUTION_FAILED, "执行失败"),
            TicketStatus.INTERRUPTED.value: (NotifyEvent.EXECUTION_INTERRUPTED, "执行已中止"),
        }
        event, label = event_map[ticket_status]
        # 审计：执行终态（成功记 success，失败/中断记 failed，中断起因进 detail）
        audit.log(
            module="execution", action="execution.finish",
            result="success" if ticket_status == TicketStatus.SUCCESS.value else "failed",
            target_type="execution", target_id=str(self.eid),
            target_name=self.ticket.ticket_no,
            detail={"ticket_status": ticket_status, "reason": reason},
        )
        # 复用工单通知规则装配（模板 notify_rules 优先，缺省通知创建人）
        from app.services.ticket_service import _emit_ticket_event
        await _emit_ticket_event(
            self.session, self.ticket, event,
            title=f"工单 {self.ticket.ticket_no} {label}",
            content=f"{self.ticket.title}（应用：{self.ticket.app_name_snap}）{label}"
                    + (f"，中断起因：{reason}" if reason else ""),
            default_receivers=[creator_name],
            # 模板变量：仅中断场景有 reason，成功/失败不传（模板占位符原样保留）
            variables={"reason": reason} if reason else None,
        )
        await self.session.commit()

    async def _finalize_terminated(self) -> None:
        """人为中止终态：execution=terminated → 工单 interrupted(user_abort)。"""
        await self._skip_pending_hosts()
        self.execution.status = ExecutionStatus.TERMINATED.value
        await self._finalize_ticket(TicketStatus.INTERRUPTED.value, InterruptReason.USER_ABORT.value)
        await events.publish_event(
            self.eid, events.KIND_EXECUTION,
            {"status": self.execution.status, "ticket_status": self.ticket.status},
        )

    async def _finalize_interrupted(self, reason: str) -> None:
        """系统性中断终态（引擎异常兜底）：execution=interrupted → 工单 interrupted(reason)。"""
        try:
            self.execution.status = ExecutionStatus.INTERRUPTED.value
            await self._finalize_ticket(TicketStatus.INTERRUPTED.value, reason)
            await events.publish_event(
                self.eid, events.KIND_EXECUTION,
                {"status": self.execution.status, "ticket_status": self.ticket.status},
            )
        except Exception:  # noqa: BLE001 兜底归档失败只能靠崩溃恢复扫描
            logger.exception("执行 %s 中断归档失败", self.eid)

    async def _finalize_normal(self) -> None:
        """正常跑完的终态映射：任一主机 failed/timeout → failed，否则 success。"""
        await self._skip_pending_hosts()
        failed_hosts = (await self.session.execute(
            select(ExecutionStepHost.id).where(
                ExecutionStepHost.execution_id == self.eid,
                ExecutionStepHost.status.in_(
                    [HostExecStatus.FAILED.value, HostExecStatus.TIMEOUT.value]
                ),
            ).limit(1)
        )).scalar_one_or_none()
        if failed_hosts is not None:
            self.execution.status = ExecutionStatus.FAILED.value
            await self._finalize_ticket(TicketStatus.FAILED.value, None)
        else:
            self.execution.status = ExecutionStatus.SUCCESS.value
            await self._finalize_ticket(TicketStatus.SUCCESS.value, None)
        await events.publish_event(
            self.eid, events.KIND_EXECUTION,
            {"status": self.execution.status, "ticket_status": self.ticket.status},
        )


async def run_execution(execution_id: int) -> None:
    """Worker 消费入口：认领（已 XACK）后跑完整个 Pipeline 生命周期。

    入队竞态防御：API 侧 XADD 发生在请求事务 commit 之前（get_db 收尾提交），
    worker 阻塞读可能先于提交可见拿到消息，此时回查 DB 会查不到执行记录；
    短暂重试等待事务提交（最长 5s），仍不存在才丢弃（孤儿消息口径）。
    """
    runner = PipelineRunner(execution_id)
    execution = None
    for attempt in range(10):
        if attempt:
            await asyncio.sleep(0.5)
        # 预加载主机端口映射（ticket_host 快照的 ssh_port，ExecutionStepHost 不冗余端口）
        async with async_session_factory() as s:
            execution = await s.get(Execution, execution_id)
            if execution is None:
                continue
            from app.models.ticket import TicketHost
            rows = await s.execute(
                select(TicketHost.id, TicketHost.ssh_port).where(TicketHost.ticket_id == execution.ticket_id)
            )
            runner._port_map = {tid: port for tid, port in rows}
            break
    if execution is None:
        logger.error("执行 %s 重试后仍不存在（API 事务可能提交失败），丢弃消息", execution_id)
        return
    await runner.run()
