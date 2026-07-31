"""M6 通知集成测试。

覆盖验收点（05-任务拆解 M6）：
- /notify/* 六接口：渠道列表/掩码回显、保存（掩码不覆盖真实密钥）、
  预留渠道禁止启用、事件映射全量提交、发送记录筛选、RBAC 拒绝路径；
- 分发器逻辑（mock 渠道发送）：
  * 渠道未启用/收件人无邮箱 → 直接 failed 留痕；
  * 发送失败 → 指数退避 1m/5m/15m，3 次耗尽置 failed（SMTP 故障重试验收）；
  * 发送成功 → success + sent_at；
- emit：按映射逐渠道落 pending 记录并 XADD ops:notify:queue（加速器）。
"""
from datetime import datetime, timedelta

import asyncio

import pytest
from sqlalchemy import select

from app.core.constants import NotifyEvent
from app.core.security import decrypt_text, encrypt_text
from app.models.auth import User
from app.models.notify import NotificationRecord, NotifyChannel, NotifyChannelEvent
from app.notify import ChannelSendError, get_channel
from app.notify import dispatcher as disp
from app.services import notify_service
from tests.conftest import auth_header, login_for_tokens


async def _admin_headers(client):
    """登录 admin 返回鉴权头（六接口均需 notify:config，仅 admin 角色持有）。"""
    return auth_header(await login_for_tokens(client, "admin"))


# ---------- 渠道注册表 ----------

class TestChannelRegistry:
    def test_get_channel_types(self):
        """六类型全部可取：落地渠道有实现，预留渠道为空实现占位。"""
        for t in ("email", "webhook", "teams", "dingtalk", "feishu", "wecom"):
            assert get_channel(t).type == t

    def test_unknown_channel(self):
        with pytest.raises(ChannelSendError):
            get_channel("sms")

    async def test_stub_channel_send_raises(self):
        """预留渠道 send 直接报未实现。"""
        from app.notify.base import NotifyMessage
        with pytest.raises(ChannelSendError, match="暂未实现"):
            await get_channel("dingtalk").send(NotifyMessage(event="x", title="t"), {}, None)


# ---------- /notify/channels ----------

class TestChannelApi:
    async def test_list_channels_six_types(self, client):
        """空库也按枚举输出六类型；未配置密钥时 secret 为 None。"""
        headers = await _admin_headers(client)
        resp = await client.get("/api/v1/notify/channels", headers=headers)
        data = resp.json()["data"]["items"]
        assert [c["type"] for c in data] == ["email", "webhook", "teams", "dingtalk", "feishu", "wecom"]
        assert all(c["enabled"] is False and c["secret"] is None for c in data)
        assert [c["implemented"] for c in data] == [True, True, True, False, False, False]

    async def test_update_channel_and_mask(self, client, db_factory):
        """保存后密钥加密落库、掩码回显；提交 ****** 不覆盖真实密钥。"""
        headers = await _admin_headers(client)
        payload = {"enabled": True, "config": {"host": "smtp.test", "port": 465,
                                               "username": "u@test", "from_addr": "u@test"},
                   "secret": "smtp-pass-1"}
        resp = await client.put("/api/v1/notify/channels/email", json=payload, headers=headers)
        assert resp.json()["code"] == 0
        resp = await client.get("/api/v1/notify/channels", headers=headers)
        email = next(c for c in resp.json()["data"]["items"] if c["type"] == "email")
        assert email["enabled"] is True and email["secret"] == "******"
        assert email["config"]["host"] == "smtp.test"
        # 原样提交掩码：库中密钥保持不变
        payload["secret"] = "******"
        await client.put("/api/v1/notify/channels/email", json=payload, headers=headers)
        async with db_factory() as session:
            row = (await session.execute(
                select(NotifyChannel).where(NotifyChannel.type == "email"))).scalar_one()
            assert decrypt_text(row.secret_enc) == "smtp-pass-1"

    async def test_stub_channel_cannot_enable(self, client):
        """预留渠道无发送实现，启用直接 40001。"""
        headers = await _admin_headers(client)
        resp = await client.put("/api/v1/notify/channels/dingtalk",
                                json={"enabled": True, "config": {}}, headers=headers)
        assert resp.json()["code"] == 40001

    async def test_unknown_channel_type(self, client):
        headers = await _admin_headers(client)
        resp = await client.put("/api/v1/notify/channels/sms",
                                json={"enabled": False, "config": {}}, headers=headers)
        assert resp.json()["code"] == 40001

    async def test_test_channel_config_missing(self, client):
        """测试接口：配置缺失同步返回 success=False，不抛 500。"""
        headers = await _admin_headers(client)
        resp = await client.post("/api/v1/notify/channels/email/test",
                                 json={"config": {}}, headers=headers)
        data = resp.json()["data"]
        assert data["success"] is False and "SMTP" in data["message"]

    async def test_rbac_denied(self, client):
        """ops 角色无 notify:config → 40301。"""
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/notify/channels", headers=headers)
        assert resp.json()["code"] == 40301


# ---------- /notify/events ----------

class TestEventMappingApi:
    async def test_list_events_full_six(self, client):
        """六事件全量输出（测试库无种子映射 → 空 channels）。"""
        headers = await _admin_headers(client)
        resp = await client.get("/api/v1/notify/events", headers=headers)
        items = resp.json()["data"]["items"]
        assert [i["event"] for i in items] == [e.value for e in NotifyEvent]

    async def test_update_events_full_replace(self, client):
        """全量提交：整表重建，未提交事件清空；重复渠道去重。"""
        headers = await _admin_headers(client)
        mappings = {"ticket.approved": ["email", "webhook", "email"],
                    "execution.failed": ["teams"]}
        resp = await client.put("/api/v1/notify/events", json={"mappings": mappings}, headers=headers)
        assert resp.json()["code"] == 0
        resp = await client.get("/api/v1/notify/events", headers=headers)
        by_event = {i["event"]: i["channels"] for i in resp.json()["data"]["items"]}
        assert by_event["ticket.approved"] == ["email", "webhook"]
        assert by_event["execution.failed"] == ["teams"]
        assert by_event["ticket.rejected"] == []

    async def test_update_events_invalid(self, client):
        headers = await _admin_headers(client)
        resp = await client.put("/api/v1/notify/events",
                                json={"mappings": {"bad.event": ["email"]}}, headers=headers)
        assert resp.json()["code"] == 40001


# ---------- /notify/records ----------

class TestRecordApi:
    async def test_list_records_filters(self, client, db_factory):
        """event/channel/status 组合筛选与分页总数。"""
        async with db_factory() as session:
            session.add_all([
                NotificationRecord(event="ticket.approved", channel_type="email",
                                   title="a", status="success"),
                NotificationRecord(event="ticket.approved", channel_type="webhook",
                                   title="b", status="failed", error="HTTP 500"),
                NotificationRecord(event="execution.failed", channel_type="email",
                                   title="c", status="pending"),
            ])
            await session.commit()
        headers = await _admin_headers(client)
        resp = await client.get("/api/v1/notify/records", headers=headers)
        assert resp.json()["data"]["total"] == 3
        resp = await client.get("/api/v1/notify/records?event=ticket.approved&status=failed",
                                headers=headers)
        data = resp.json()["data"]
        assert data["total"] == 1 and data["items"][0]["channel_type"] == "webhook"
        resp = await client.get("/api/v1/notify/records?channel=email", headers=headers)
        assert resp.json()["data"]["total"] == 2


# ---------- emit（服务层） ----------

class TestEmit:
    async def test_emit_by_mapping_and_xadd(self, db_factory, fake_redis, seed):
        """按映射逐渠道落 pending 记录，并 XADD 通知队列（加速器语义）。"""
        async with db_factory() as session:
            session.add_all([
                NotifyChannelEvent(event="ticket.approved", channel_type="email"),
                NotifyChannelEvent(event="ticket.approved", channel_type="teams"),
            ])
            await session.commit()
        async with db_factory() as session:
            await notify_service.emit(session, NotifyEvent.TICKET_APPROVED,
                                      title="工单已通过", receiver="ops1",
                                      ref_type="ticket", ref_id=1)
            await session.commit()
            rows = (await session.execute(select(NotificationRecord))).scalars().all()
        assert sorted(r.channel_type for r in rows) == ["email", "teams"]
        assert all(r.status == "pending" for r in rows)
        assert await fake_redis.xlen("ops:notify:queue") == 1

    async def test_emit_fallback_email(self, db_factory, fake_redis, seed):
        """无映射事件兜底 email 渠道。"""
        async with db_factory() as session:
            await notify_service.emit(session, NotifyEvent.EXECUTION_INTERRUPTED,
                                      title="执行中断", receiver="admin")
            await session.commit()
            rows = (await session.execute(select(NotificationRecord))).scalars().all()
        assert [r.channel_type for r in rows] == ["email"]


# ---------- 分发器逻辑（mock 渠道发送） ----------

def _record(**kw) -> NotificationRecord:
    """构造 pending 记录的测试辅助。"""
    defaults = dict(event="ticket.approved", channel_type="email",
                    receiver="admin", title="t", status="pending")
    defaults.update(kw)
    return NotificationRecord(**defaults)


class _FakeChannel:
    """可编程假渠道：按预设决定成功或抛错，记录调用载荷。"""

    def __init__(self, error: str | None = None):
        self.error = error
        self.calls: list = []

    async def send(self, message, config, secret):
        self.calls.append((message, config, secret))
        if self.error:
            raise ChannelSendError(self.error)


class TestDispatcher:
    async def _seed_channel(self, db_factory, *, enabled=True, secret: str | None = None):
        """种一行 email 渠道配置 + 给 admin 补邮箱。"""
        async with db_factory() as session:
            session.add(NotifyChannel(type="email", enabled=enabled,
                                      config={"host": "smtp.test"},
                                      secret_enc=encrypt_text(secret) if secret else None))
            admin = (await session.execute(
                select(User).where(User.username == "admin"))).scalar_one()
            admin.email = "admin@test.local"
            await session.commit()

    async def test_channel_disabled_failed(self, db_factory, seed):
        """渠道未启用：确定性失败，不进入重试。"""
        await self._seed_channel(db_factory, enabled=False)
        async with db_factory() as session:
            record = _record()
            session.add(record)
            await disp.dispatch_record(session, record)
            assert record.status == "failed" and record.error == "渠道未启用"
            assert record.retry_count == 0

    async def test_email_no_mailbox_failed(self, db_factory, seed):
        """收件人均无邮箱：直接 failed 留痕（冲突决策 C2）。"""
        await self._seed_channel(db_factory)
        async with db_factory() as session:
            record = _record(receiver="ops1,newbie")  # 两用户均未配置邮箱
            session.add(record)
            await disp.dispatch_record(session, record)
            assert record.status == "failed" and "邮箱" in record.error

    async def test_send_success(self, db_factory, seed, monkeypatch):
        """发送成功：success + sent_at；收件人解析为邮箱并解密透传 secret。"""
        await self._seed_channel(db_factory, secret="smtp-pass")
        fake = _FakeChannel()
        monkeypatch.setattr(disp, "get_channel", lambda _t: fake)
        async with db_factory() as session:
            record = _record(receiver="admin,ops1")
            session.add(record)
            await disp.dispatch_record(session, record)
            assert record.status == "success" and record.sent_at is not None
        message, _config, secret = fake.calls[0]
        assert message.receivers == ["admin@test.local"]  # ops1 无邮箱被跳过
        assert secret == "smtp-pass"

    async def test_retry_backoff_then_exhaust(self, db_factory, seed, monkeypatch):
        """失败退避 1m/5m/15m，第 3 次重试后仍失败置 failed（SMTP 故障验收）。"""
        await self._seed_channel(db_factory)
        fake = _FakeChannel(error="SMTP 连接超时")
        monkeypatch.setattr(disp, "get_channel", lambda _t: fake)
        async with db_factory() as session:
            record = _record()
            session.add(record)
            for i, backoff in enumerate(disp.RETRY_BACKOFF, start=1):
                record.next_retry_at = None  # 模拟到期
                before = datetime.now()
                await disp.dispatch_record(session, record)
                assert record.status == "pending" and record.retry_count == i
                assert record.error == "SMTP 连接超时"
                # 退避间隔按序放大（允许秒级误差）
                expect = before + timedelta(seconds=backoff)
                assert abs((record.next_retry_at - expect).total_seconds()) < 5
            # 第 4 次（重试耗尽）→ failed
            await disp.dispatch_record(session, record)
            assert record.status == "failed" and record.retry_count == disp.MAX_RETRY

    async def test_scan_once_picks_due_records(self, db_factory, seed, monkeypatch):
        """扫描兜底：只处理到期 pending（未到期跳过），成功后回写落库。"""
        await self._seed_channel(db_factory)
        fake = _FakeChannel()
        monkeypatch.setattr(disp, "get_channel", lambda _t: fake)
        monkeypatch.setattr(disp, "async_session_factory", db_factory)
        async with db_factory() as session:
            session.add(_record())  # 到期（next_retry_at 为空）
            session.add(_record(next_retry_at=datetime.now() + timedelta(hours=1)))  # 未到期
            await session.commit()
        assert await disp.scan_once() == 1
        async with db_factory() as session:
            rows = (await session.execute(
                select(NotificationRecord).order_by(NotificationRecord.id))).scalars().all()
            assert rows[0].status == "success"
            assert rows[1].status == "pending"

    async def test_concurrent_scan_no_duplicate_send(self, db_factory, seed, monkeypatch):
        """并发扫描不重复发送（重复邮件 bug 回归）。

        复现：_consume_loop 与 _scan_loop 同时触发 scan_once，旧逻辑下两轮都会取到
        同一条仍为 pending 的记录并各发一次；原子认领后仅首个扫描者拿到发送权，
        同一记录仅发一封。
        """
        await self._seed_channel(db_factory)
        fake = _FakeChannel()
        monkeypatch.setattr(disp, "get_channel", lambda _t: fake)
        monkeypatch.setattr(disp, "async_session_factory", db_factory)
        async with db_factory() as session:
            session.add(_record())
            await session.commit()
        # 两轮 scan_once 并发运行（模拟队列触发扫描与 30s 定时扫描重叠）
        results = await asyncio.gather(disp.scan_once(), disp.scan_once())
        assert sum(results) == 1  # 仅一轮真正认领并发送
        assert len(fake.calls) == 1  # 只发一封（不重复）
        async with db_factory() as session:
            record = (await session.execute(select(NotificationRecord))).scalar_one()
            assert record.status == "success"  # 终态回写正确（无 sending 残留）

    async def test_reset_orphaned_sending_on_start(self, db_factory, seed, monkeypatch):
        """启动兜底：上次中途崩溃遗留的 sending 记录重置回 pending，不会被扫描永久遗漏。"""
        monkeypatch.setattr(disp, "async_session_factory", db_factory)
        async with db_factory() as session:
            session.add(_record(status="sending"))
            await session.commit()
        await disp.NotifyDispatcher._reset_orphaned_sending()
        async with db_factory() as session:
            record = (await session.execute(select(NotificationRecord))).scalar_one()
            assert record.status == "pending"

    async def test_scan_once_retry_returns_to_pending(self, db_factory, seed, monkeypatch):
        """扫描路径下发送失败要重试：记录必须从 sending 写回 pending，下一轮能再被扫到。

        回归原子认领引入的隐患：_claim_record 已把 DB 置 sending，若 _schedule_retry
        不显式写回 pending，记录会卡在 sending 而被后续扫描永久遗漏。
        """
        await self._seed_channel(db_factory)
        fake = _FakeChannel(error="SMTP 连接超时")
        monkeypatch.setattr(disp, "get_channel", lambda _t: fake)
        monkeypatch.setattr(disp, "async_session_factory", db_factory)
        async with db_factory() as session:
            session.add(_record())
            await session.commit()
        assert await disp.scan_once() == 1
        async with db_factory() as session:
            record = (await session.execute(select(NotificationRecord))).scalar_one()
            # 未耗尽：回到 pending（不得卡在 sending）并排了下次重试
            assert record.status == "pending"
            assert record.retry_count == 1 and record.next_retry_at is not None
        # 模拟到期后下一轮扫描仍能捐到该记录（验证未漏扫）
        async with db_factory() as session:
            record = (await session.execute(select(NotificationRecord))).scalar_one()
            record.next_retry_at = None
            await session.commit()
        assert await disp.scan_once() == 1
        assert len(fake.calls) == 2  # 两轮都真正发起了发送（未因卡 sending 而跳过）

    async def test_webhook_receiver_passthrough(self, db_factory, seed, monkeypatch):
        """非 email 渠道不解析邮箱：receivers 为空列表，发往全局 URL。"""
        async with db_factory() as session:
            session.add(NotifyChannel(type="webhook", enabled=True,
                                      config={"url": "https://hook.test"}))
            await session.commit()
        fake = _FakeChannel()
        monkeypatch.setattr(disp, "get_channel", lambda _t: fake)
        async with db_factory() as session:
            record = _record(channel_type="webhook", receiver="admin")
            session.add(record)
            await disp.dispatch_record(session, record)
            assert record.status == "success"
        message, config, _ = fake.calls[0]
        assert message.receivers == [] and config["url"] == "https://hook.test"


# ---------- 消息模板 ----------

class TestTemplate:
    def test_render_template(self):
        """占位替换；缺失/None 变量原样保留；非占位花括号不受影响。"""
        variables = {"ticket_no": "TK1", "approver": "ops1", "comment": None}
        assert notify_service.render_template(
            "【{ticket_no}】{approver} 已处理", variables) == "【TK1】ops1 已处理"
        # 缺失变量 {reason} 与 None 值 {comment} 都原样保留
        assert notify_service.render_template(
            "{reason}|{comment}|{ticket_no}", variables) == "{reason}|{comment}|TK1"
        assert notify_service.render_template("无占位符文本", variables) == "无占位符文本"

    async def test_emit_renders_per_channel_template(self, db_factory, fake_redis, seed):
        """配了模板的渠道按变量渲染，未配模板的渠道保持业务默认文案（双渠道对照）。"""
        async with db_factory() as session:
            session.add_all([
                NotifyChannelEvent(event="ticket.approved", channel_type="email"),
                NotifyChannelEvent(event="ticket.approved", channel_type="teams"),
                # 仅 email 配模板：标题用事件/工单号，正文包装默认文案 + 业务变量
                NotifyChannel(type="email", enabled=True, config={
                    "title_template": "【{event}】{ticket_no}",
                    "content_template": "{default_content}（审批人：{approver}）",
                }),
            ])
            await session.commit()
        async with db_factory() as session:
            await notify_service.emit(
                session, NotifyEvent.TICKET_APPROVED,
                title="工单 TK1 审批通过", content="示例工单：审批通过",
                receiver="ops1", ref_type="ticket", ref_id=1,
                variables={"ticket_no": "TK1", "approver": "ops1"},
            )
            await session.commit()
            rows = {r.channel_type: r for r in
                    (await session.execute(select(NotificationRecord))).scalars()}
        assert rows["email"].title == "【审批通过】TK1"
        assert rows["email"].content == "示例工单：审批通过（审批人：ops1）"
        # teams 未配模板：默认文案原样落库
        assert rows["teams"].title == "工单 TK1 审批通过"
        assert rows["teams"].content == "示例工单：审批通过"

    async def test_update_channel_template_too_long(self, client):
        """模板超长：标题 >200 / 正文 >2000 均 40001。"""
        headers = await _admin_headers(client)
        resp = await client.put(
            "/api/v1/notify/channels/email",
            json={"enabled": False, "config": {"title_template": "x" * 201}}, headers=headers)
        assert resp.json()["code"] == 40001
        resp = await client.put(
            "/api/v1/notify/channels/email",
            json={"enabled": False, "config": {"content_template": "x" * 2001}}, headers=headers)
        assert resp.json()["code"] == 40001

    async def test_test_channel_renders_sample(self, client, monkeypatch):
        """测试发送用示例变量渲染表单模板：保存前即可预览效果。"""
        fake = _FakeChannel()
        monkeypatch.setattr(notify_service, "get_channel", lambda _t: fake)
        headers = await _admin_headers(client)
        resp = await client.post(
            "/api/v1/notify/channels/email/test",
            json={"config": {"title_template": "【{event}】{ticket_no} 由 {approver} 处理"},
                  "receiver": "you@test.local"},
            headers=headers)
        assert resp.json()["data"]["success"] is True
        message, _config, _secret = fake.calls[0]
        assert message.title == "【审批通过】TK20260001 由 ops1 处理"
