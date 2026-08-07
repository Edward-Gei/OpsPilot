"""Pipeline 调度器：单次执行的完整生命周期（02-技术架构 §4.1/§4.2）。

调度模型（执行范式改造后）：
- 步骤严格串行；每步 SSH→作业主机执行一次（不再有步骤×主机矩阵与批处理）；
- 三检查点响应控制信号：①认领开跑前 ②每步骤开始前 ③每步骤结束后；
- 暂停/中止默认不打断在跑步骤（等待跑完），force-abort 由 watcher 强杀连接；
- 失败策略：fail_fast=true 时步骤失败 → 中断后续步骤；
- Ansible 步骤 = Shell 特例：在作业主机执行 ansible-playbook 命令，
  仅取退出码判定成败，不解析 PLAY RECAP；
- 终态映射（03 §5.1）：success→success / failed→failed /
  terminated→interrupted(user_abort)；引擎内异常→interrupted(system_crash)。

会话：调度主流程用单一 AsyncSession 顺序读写（步骤串行，无跨协程共享）。
"""
import asyncio
import logging
from datetime import datetime

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select

from app import audit

from app.core.constants import (
    ExecutionStatus,
    InterruptReason,
    NotifyEvent,
    ScriptType,
    TicketStatus,
)
from app.core.database import async_session_factory
from app.core.security import decrypt_text
from app.models.auth import User
from app.models.cmdb import JobHost
from app.models.execution import Execution, ExecutionStep
from app.models.job import Credential
from app.models.ticket import Ticket, TicketApproval, TicketStep
from app.engine import ansible_runner, control as ctrl, events, ssh_runner
from app.engine.logs import LogChannel

logger = logging.getLogger("opspilot.engine.pipeline")


def render_script(content: str, params: dict) -> str:
    """用 Jinja2 沙箱渲染脚本参数（02 §4.4：SandboxedEnvironment 防模板注入）。"""
    env = SandboxedEnvironment(autoescape=False, keep_trailing_newline=True)
    return env.from_string(content).render(**(params or {}))


def build_cred_env(alias: str, cred) -> dict[str, str]:
    """按引用别名组装凭据环境变量（CRED_<ALIAS大写>_USER/_SECRET/_PASSPHRASE）。"""
    prefix = f"CRED_{alias.upper()}"
    env = {
        f"{prefix}_USER": cred.login_user,
        f"{prefix}_SECRET": decrypt_text(cred.secret_enc),
    }
    if cred.passphrase_enc:
        env[f"{prefix}_PASSPHRASE"] = decrypt_text(cred.passphrase_enc)
    return env


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
        # 作业主机对象（工单快照关联的 job_host 表行）
        self.job_host: JobHost | None = None
        self.credential: Credential | None = None   # 作业主机登录凭据（建连用）
        self.cred_env: dict[str, str] = {}          # 模板引用凭据 env（shell 步骤注入）
        self.cred_error: str | None = None          # 凭据加载失败原因（步骤级失败归档）

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
                    if aborted is None:
                        return
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
        """加载执行/工单/作业主机；非 queued 状态直接跳过（重复消息防御）。"""
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
        # 加载作业主机（登录认证随关联凭据）
        jh_id = self.ticket.job_host_id
        self.job_host = await s.get(JobHost, jh_id)
        if self.job_host is None:
            logger.error("工单 %s 关联的作业主机 %s 不存在", self.ticket.id, jh_id)
            return False
        # 加载登录凭据与引用凭据；失败原因记 cred_error，由步骤按失败归档（用户可见）
        if self.job_host.credential_id:
            self.credential = await s.get(Credential, self.job_host.credential_id)
        if self.credential is None:
            self.cred_error = "作业主机未关联凭据或凭据已删除，请在系统设置中重新关联"
            return True
        return True

    # ---------- 状态迁移辅助 ----------

    async def _safe_publish(self, kind: str, data: dict) -> None:
        """广播事件；失败仅告警不影响主流程（状态已 commit 落库为准，
        前端可通过长轮询/刷新兜底，不能因广播异常把正常执行误判为崩溃）。"""
        try:
            await events.publish_event(self.eid, kind, data)
        except Exception as exc:  # noqa: BLE001 广播失败不影响已持久化的状态
            logger.warning("执行 %s 事件广播失败（kind=%s, data=%s）: %s", self.eid, kind, data, exc)

    async def _set_status(self, exec_status: str, ticket_status: str | None = None) -> None:
        """同步更新 execution 与工单状态并广播 execution_status 事件。

        顺序：先 commit 保证状态持久化，再广播；广播失败不回滚。"""
        self.execution.status = exec_status
        if ticket_status:
            self.ticket.status = ticket_status
        await self.session.commit()
        await self._safe_publish(
            events.KIND_EXECUTION,
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
            # 停住等待 resume / abort（在跑步骤已跑完才会走到这里）
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
            if step.status == ExecutionStatus.SUCCESS.value:
                continue
            # 检查点②：每步骤开始前
            if not await self._checkpoint():
                return True
            tstep = ticket_steps[step.step_order]
            # 步骤前审批：未通过时保留 queued 执行实例，等待审批 API 重新入队。
            if tstep.approval_role_id_snap:
                approved = (await s.execute(
                    select(TicketApproval.id).where(
                        TicketApproval.ticket_id == self.ticket.id,
                        TicketApproval.step_order == step.step_order,
                        TicketApproval.action == "approve",
                    ).limit(1)
                )).scalar_one_or_none()
                if approved is None:
                    self.ticket.status = TicketStatus.APPROVING.value
                    self.ticket.current_step = step.step_order
                    self.execution.status = ExecutionStatus.QUEUED.value
                    await s.commit()
                    return None
            step_failed = await self._run_one_step(step, tstep)
            # 检查点③：每步骤结束后
            if self.control.abort:
                return True
            if step_failed and fail_fast:
                # 失败中断：剩余步骤不再执行
                logger.info("执行 %s 步骤 %s 失败且 fail_fast，中断后续步骤", self.eid, step.step_order)
                break
        return False

    async def _run_one_step(self, step: ExecutionStep, tstep: TicketStep) -> bool:
        """在作业主机执行一次步骤，返回是否失败。"""
        s = self.session
        step.status = ExecutionStatus.RUNNING.value
        step.started_at = datetime.now()
        await s.commit()
        await self._publish_step(step)

        # 凭据加载失败：不建立 SSH 会话，按步骤失败归档（错误对用户可见）
        if self.cred_error:
            self.log.write(step.step_order, f"[opspilot] {self.cred_error}")
            step.status = ExecutionStatus.FAILED.value
            step.exit_code = None
            step.error_summary = self.cred_error[:500]
            step.finished_at = datetime.now()
            await s.commit()
            await self._publish_step(step)
            return True

        # 渲染脚本参数（渲染失败 = 整步骤失败，不建立 SSH 会话）
        try:
            script = render_script(tstep.content_snap, tstep.params or {})
        except Exception as exc:  # noqa: BLE001 模板渲染错误属用户输入问题
            self.log.write(step.step_order, f"[opspilot] 脚本参数渲染失败: {exc}")
            step.status = ExecutionStatus.FAILED.value
            step.exit_code = None
            step.error_summary = f"参数渲染失败: {exc}"[:500]
            step.finished_at = datetime.now()
            await s.commit()
            await self._publish_step(step)
            return True

        # 在作业主机执行（Ansible = Shell 特例：跑 ansible-playbook 命令取退出码）
        jh = self.job_host
        if tstep.script_type_snap == ScriptType.PLAYBOOK.value:
            status, exit_code, summary = await ansible_runner.run_ansible_on_job_host(
                job_host=jh, credential=self.credential, playbook=script,
                timeout=tstep.timeout, step_order=step.step_order, log=self.log,
                control=self.control, execution_id=self.eid,
            )
        else:
            status, exit_code, summary = await ssh_runner.run_shell_on_job_host(
                job_host=jh, credential=self.credential, script=script,
                env=self.cred_env or None, timeout=tstep.timeout,
                step_order=step.step_order, log=self.log, control=self.control,
            )
        step.status = status
        step.exit_code = exit_code
        step.error_summary = summary
        step.finished_at = datetime.now()
        await s.commit()
        await self._publish_step(step)
        return status == ExecutionStatus.FAILED.value

    async def _publish_step(self, step: ExecutionStep) -> None:
        """广播步骤状态事件（失败仅告警，步骤状态已落库）。"""
        await self._safe_publish(
            events.KIND_STEP,
            {"step_order": step.step_order, "status": step.status,
             "exit_code": step.exit_code, "error_summary": step.error_summary},
        )

    # ---------- 终态归档 ----------

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
            content=f"{self.ticket.title}（作业主机：{(self.ticket.job_host_snap or {}).get('name', '')}）{label}"
                    + (f"，中断起因：{reason}" if reason else ""),
            default_receivers=[creator_name],
            # 模板变量：仅中断场景有 reason，成功/失败不传（模板占位符原样保留）
            variables={"reason": reason} if reason else None,
        )
        await self.session.commit()

    async def _finalize_terminated(self) -> None:
        """人为中止终态：execution=terminated → 工单 interrupted(user_abort)。"""
        self.execution.status = ExecutionStatus.TERMINATED.value
        await self._finalize_ticket(TicketStatus.INTERRUPTED.value, InterruptReason.USER_ABORT.value)
        await self._safe_publish(
            events.KIND_EXECUTION,
            {"status": self.execution.status, "ticket_status": self.ticket.status},
        )

    async def _finalize_interrupted(self, reason: str) -> None:
        """系统性中断终态（引擎异常兜底）：execution=interrupted → 工单 interrupted(reason)。"""
        try:
            self.execution.status = ExecutionStatus.INTERRUPTED.value
            await self._finalize_ticket(TicketStatus.INTERRUPTED.value, reason)
            await self._safe_publish(
                events.KIND_EXECUTION,
                {"status": self.execution.status, "ticket_status": self.ticket.status},
            )
        except Exception:  # noqa: BLE001 兜底归档失败只能靠崩溃恢复扫描
            logger.exception("执行 %s 中断归档失败", self.eid)

    async def _finalize_normal(self) -> None:
        """正常跑完：任一步骤 exit_code 非 0 → failed，否则 success。"""
        failed_step = (await self.session.execute(
            select(ExecutionStep.id).where(
                ExecutionStep.execution_id == self.eid,
                ExecutionStep.status == ExecutionStatus.FAILED.value,
            ).limit(1)
        )).scalar_one_or_none()
        if failed_step is not None:
            self.execution.status = ExecutionStatus.FAILED.value
            await self._finalize_ticket(TicketStatus.FAILED.value, None)
        else:
            self.execution.status = ExecutionStatus.SUCCESS.value
            await self._finalize_ticket(TicketStatus.SUCCESS.value, None)
        await self._safe_publish(
            events.KIND_EXECUTION,
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
        async with async_session_factory() as s:
            execution = await s.get(Execution, execution_id)
            if execution is not None:
                break
    if execution is None:
        logger.error("执行 %s 重试后仍不存在（API 事务可能提交失败），丢弃消息", execution_id)
        return
    await runner.run()
