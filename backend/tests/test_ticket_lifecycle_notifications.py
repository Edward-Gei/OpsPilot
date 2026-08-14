"""工单生命周期通知回归测试。"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.constants import HostExecStatus, NotifyEvent, TicketStatus
from app.core.security import encrypt_text
from app.core.response import BizError
from app.engine import pipeline
from app.models.auth import User, UserRole
from app.models.cmdb import JobHost
from app.models.execution import Execution, ExecutionStep
from app.models.job import Credential, ProcessStep, ProcessTemplate, TicketTemplate
from app.models.notify import NotificationRecord, NotifyChannelEvent
from app.models.ticket import Ticket, TicketParameterPrepare, TicketStep
from app.services import parameter_prepare_service, seed as seed_service, ticket_service


@pytest.mark.asyncio
async def test_generator_renders_fixed_values_before_remote_execution(monkeypatch):
    """动态脚本执行前应渲染已准备好的固定值，避免占位符原样进入 Bash。"""
    class _Result:
        exit_status = 0
        stdout = "{}"

    class _Connection:
        def __init__(self):
            self.script = None

        async def run(self, command, input):
            self.script = input
            return _Result()

        def close(self):
            pass

    connection = _Connection()

    async def fake_open_connection(*args):
        return connection

    monkeypatch.setattr(parameter_prepare_service, "open_connection", fake_open_connection)
    await parameter_prepare_service._run_generator(
        object(), object(), "SERVICE={{ SERVICE_NAME }}", 60, {"SERVICE_NAME": "mt-web"},
    )

    assert connection.script == "SERVICE=mt-web"


async def _template(session, *, creator_id: int, role_id: int | None = None) -> TicketTemplate:
    """创建最小可执行模板，避免回归测试依赖 HTTP 层模板接口。"""
    credential = Credential(
        name="notify-test-credential", login_user="root", auth_type="password",
        secret_enc=encrypt_text("secret"),
    )
    session.add(credential)
    await session.flush()
    host = JobHost(
        name="notify-test-host", ip="10.0.0.10", credential_id=credential.id,
        workdir="/tmp/opspilot", enabled=True, created_by=creator_id,
    )
    session.add(host)
    await session.flush()
    process = ProcessTemplate(
        name="notify-test-process", description="notify", status="enabled",
        exec_strategy={"fail_fast": True}, created_by=creator_id,
    )
    session.add(process)
    await session.flush()
    session.add(ProcessStep(
        process_template_id=process.id, step_order=1, name="run",
        script_type="shell", content="echo ok", timeout=60,
        approval_role_id=role_id,
    ))
    template = TicketTemplate(
        name="notify-test-template", type="daily_ops", description="notify",
        job_host_id=host.id, process_template_id=process.id, allow_withdraw=True,
        params_schema=[], generator_script=None, generator_timeout=None,
        notify_rules=[], visible_role_ids=[], status="enabled", created_by=creator_id,
    )
    session.add(template)
    await session.flush()
    return template


async def _notification_events(session) -> list[str]:
    rows = await session.execute(
        select(NotificationRecord.event).order_by(NotificationRecord.id)
    )
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_ticket_approval_lifecycle_emits_pending_approved_and_rejected(
    db_factory, seed,
):
    """创建、通过和驳回必须分别产生对应的生命周期事件。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        approver_id = seed["roles"]["approver"]
        session.add(UserRole(user_id=creator.id, role_id=approver_id))
        await session.flush()
        template = await _template(session, creator_id=creator.id, role_id=approver_id)

        ticket = await ticket_service.create_ticket(
            session, creator=creator, template_id=template.id, params={},
        )
        await session.commit()
        assert await _notification_events(session) == [NotifyEvent.TICKET_PENDING_APPROVAL.value]

        await ticket_service.approve_ticket(
            session, ticket.id, actor=creator, action="approve", comment="ok",
        )
        await session.commit()
        assert await _notification_events(session) == [
            NotifyEvent.TICKET_PENDING_APPROVAL.value,
            NotifyEvent.TICKET_APPROVED.value,
        ]

        rejected = await ticket_service.create_ticket(
            session, creator=creator, template_id=template.id, params={},
        )
        await ticket_service.approve_ticket(
            session, rejected.id, actor=creator, action="reject", comment="blocked",
        )
        await session.commit()
        assert await _notification_events(session) == [
            NotifyEvent.TICKET_PENDING_APPROVAL.value,
            NotifyEvent.TICKET_APPROVED.value,
            NotifyEvent.TICKET_PENDING_APPROVAL.value,
            NotifyEvent.TICKET_REJECTED.value,
        ]


@pytest.mark.asyncio
async def test_pipeline_emits_pending_when_reaching_later_approval_step(db_factory, seed):
    """审批配置在后续步骤时，Pipeline 到达该步骤才发待审批事件。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        approver_role_id = seed["roles"]["approver"]
        session.add(UserRole(user_id=creator.id, role_id=approver_role_id))
        await session.flush()
        template = await _template(session, creator_id=creator.id)
        session.add(ProcessStep(
            process_template_id=template.process_template_id, step_order=2,
            name="approve-later", script_type="shell", content="echo later",
            timeout=60, approval_role_id=approver_role_id,
        ))
        await session.flush()
        ticket = await ticket_service.create_ticket(
            session, creator=creator, template_id=template.id, params={},
        )
        execution = (await session.execute(select(Execution))).scalar_one()
        first_step = (await session.execute(
            select(ExecutionStep).where(ExecutionStep.step_order == 1)
        )).scalar_one()
        first_step.status = "success"
        runner = pipeline.PipelineRunner(execution.id)
        runner.session = session
        runner.ticket = ticket
        runner.execution = execution
        runner.strategy = ticket.exec_strategy_snap or {}
        assert await runner._run_steps() is None
        await session.commit()
        assert await _notification_events(session) == [NotifyEvent.TICKET_PENDING_APPROVAL.value]


@pytest.mark.asyncio
async def test_prepared_parameters_allow_user_values_added_after_generation(db_factory, seed):
    """动态参数预生成不应锁死尚未填写的普通参数。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        template = await _template(session, creator_id=creator.id)
        template.params_schema = [
            {"name": "region", "source": "user", "input_type": "text", "required": True},
            {"name": "release", "source": "generated", "input_type": "enum", "required": True},
        ]
        row = TicketParameterPrepare(
            token="prepare-regression", template_id=template.id, input_params={},
            values={}, options={"release": ["blue", "green"]},
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        session.add(row)
        await session.flush()

        values = await parameter_prepare_service.consume_parameters(
            session, token=row.token, template_id=template.id,
            params={"region": "prod", "release": "green"},
        )
        assert values == {"release": "green"}


@pytest.mark.asyncio
async def test_reject_marks_queued_execution_and_skips_pending_steps(db_factory, seed):
    """审批驳回应关闭排队执行，但不影响已完成步骤。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        approver_role_id = seed["roles"]["approver"]
        session.add(UserRole(user_id=creator.id, role_id=approver_role_id))
        await session.flush()
        template = await _template(session, creator_id=creator.id, role_id=approver_role_id)
        ticket = await ticket_service.create_ticket(session, creator=creator, template_id=template.id, params={})
        execution = Execution(ticket_id=ticket.id, status="queued", total_steps=2)
        session.add(execution)
        await session.flush()
        ticket_steps = list((await session.execute(
            select(TicketStep).where(TicketStep.ticket_id == ticket.id).order_by(TicketStep.step_order)
        )).scalars())
        session.add_all([
            ExecutionStep(execution_id=execution.id, ticket_step_id=ticket_steps[0].id, step_order=1, status="success"),
            ExecutionStep(execution_id=execution.id, ticket_step_id=ticket_steps[0].id, step_order=2, status=HostExecStatus.PENDING.value),
        ])
        ticket.status = TicketStatus.APPROVING.value
        ticket.current_step = 1
        await session.flush()

        await ticket_service.approve_ticket(session, ticket.id, actor=creator, action="reject", comment="blocked")
        assert execution.status == "rejected"
        assert execution.finished_at is not None
        execution_steps = list((await session.execute(
            select(ExecutionStep).where(ExecutionStep.execution_id == execution.id).order_by(ExecutionStep.step_order)
        )).scalars())
        assert [step.status for step in execution_steps] == ["success", HostExecStatus.SKIPPED.value]


@pytest.mark.asyncio
async def test_paused_ticket_cannot_be_rejected_or_skip_steps(db_factory, seed):
    """暂停中的工单不属于审批状态，驳回操作不应改变执行步骤。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        template = await _template(session, creator_id=creator.id)
        ticket = Ticket(
            ticket_no="T-PAUSED-REGRESSION", template_id=template.id, title="paused",
            type="daily_ops", params={}, job_host_id=template.job_host_id,
            job_host_snap={"name": "notify-test-host"}, process_template_id_snap=template.process_template_id,
            process_name_snap="notify-test-process", status=TicketStatus.PAUSED.value,
            flow_snap={}, exec_strategy_snap={}, allow_withdraw_snap=True,
            creator_id=creator.id, current_step=1,
        )
        session.add(ticket)
        await session.flush()
        with pytest.raises(BizError):
            await ticket_service.approve_ticket(session, ticket.id, actor=creator, action="reject", comment="blocked")
        assert ticket.status == TicketStatus.PAUSED.value


@pytest.mark.asyncio
async def test_withdraw_queued_ticket_cancels_execution_and_skips_pending_steps(db_factory, seed):
    """撤回排队工单时关闭执行实例，Worker 后续不会继续处理步骤。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        template = await _template(session, creator_id=creator.id)
        ticket = await ticket_service.create_ticket(session, creator=creator, template_id=template.id, params={})
        execution = (await session.execute(select(Execution).where(Execution.ticket_id == ticket.id))).scalar_one()
        steps = list((await session.execute(
            select(ExecutionStep).where(ExecutionStep.execution_id == execution.id)
        )).scalars())
        assert ticket.status == TicketStatus.QUEUED.value
        assert steps and all(step.status == HostExecStatus.PENDING.value for step in steps)

        await ticket_service.cancel_ticket(session, ticket.id, actor=creator)
        assert ticket.status == TicketStatus.CANCELLED.value
        assert execution.status == "cancelled"
        assert execution.finished_at is not None
        assert all(step.status == HostExecStatus.SKIPPED.value for step in steps)


@pytest.mark.asyncio
async def test_withdraw_running_ticket_is_rejected(db_factory, seed):
    """运行中的工单不允许撤回，避免状态关闭但作业进程仍继续执行。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        template = await _template(session, creator_id=creator.id)
        ticket = Ticket(
            ticket_no="T-RUNNING-WITHDRAW", template_id=template.id, title="running",
            type="daily_ops", params={}, job_host_id=template.job_host_id,
            job_host_snap={"name": "notify-test-host"}, process_template_id_snap=template.process_template_id,
            process_name_snap="notify-test-process", status=TicketStatus.RUNNING.value,
            flow_snap={}, exec_strategy_snap={}, allow_withdraw_snap=True,
            creator_id=creator.id,
        )
        session.add(ticket)
        await session.flush()
        with pytest.raises(BizError):
            await ticket_service.cancel_ticket(session, ticket.id, actor=creator)
        assert ticket.status == TicketStatus.RUNNING.value


@pytest.mark.asyncio
async def test_notify_mapping_removal_survives_default_seed(db_factory):
    """用户删除默认事件渠道后，服务重启补种子不得恢复该映射。"""
    async with db_factory() as session:
        await seed_service._ensure_notify_defaults(session)
        await session.flush()
        target = (await session.execute(
            select(NotifyChannelEvent).where(
                NotifyChannelEvent.event == NotifyEvent.TICKET_APPROVED.value,
                NotifyChannelEvent.channel_type == "email",
            )
        )).scalar_one()
        await session.delete(target)
        await session.flush()

        await seed_service._ensure_notify_defaults(session)
        remaining = (await session.execute(
            select(NotifyChannelEvent).where(
                NotifyChannelEvent.event == NotifyEvent.TICKET_APPROVED.value,
                NotifyChannelEvent.channel_type == "email",
            )
        )).scalar_one_or_none()
        assert remaining is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ticket_status", "event"),
    [
        (TicketStatus.SUCCESS.value, NotifyEvent.EXECUTION_SUCCESS.value),
        (TicketStatus.FAILED.value, NotifyEvent.EXECUTION_FAILED.value),
        (TicketStatus.INTERRUPTED.value, NotifyEvent.EXECUTION_INTERRUPTED.value),
    ],
)
async def test_pipeline_terminal_lifecycle_emits_event(
    db_factory, seed, ticket_status, event,
):
    """Pipeline 收敛到三类终态时都必须创建通知记录。"""
    async with db_factory() as session:
        creator = await session.get(User, seed["users"]["ops1"])
        template = await _template(session, creator_id=creator.id)
        ticket = Ticket(
            ticket_no=f"T-NOTIFY-{ticket_status}", template_id=template.id,
            title="notify", type="daily_ops", params={}, job_host_id=template.job_host_id,
            job_host_snap={"name": "notify-test-host"}, process_template_id_snap=1,
            process_name_snap="notify-test-process", status="queued", flow_snap={},
            exec_strategy_snap={}, allow_withdraw_snap=True, creator_id=creator.id,
        )
        session.add(ticket)
        await session.flush()
        execution = Execution(ticket_id=ticket.id, status="queued", total_steps=1)
        session.add(execution)
        await session.flush()
        runner = pipeline.PipelineRunner(execution.id)
        runner.session = session
        runner.ticket = ticket
        runner.execution = execution
        await runner._finalize_ticket(ticket_status, "test-reason" if ticket_status == TicketStatus.INTERRUPTED.value else None)
        assert await _notification_events(session) == [event]
