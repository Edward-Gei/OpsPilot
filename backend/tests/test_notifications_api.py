"""站内通知集成测试（NOTIFY-06，04-API 设计 §10.1）。

覆盖验收点：
- emit 挂钩：按收件人每人一行落 user_notification（去重、未知用户名静默跳过、
  不受渠道事件映射影响、与外发记录同事务共存）；
- /notifications 四接口：分页倒序、only_unread 筛选、未读数、
  单条已读（幂等 + 越权/不存在统一 40401）、全部已读（返回条数）；
- 登录即可访问（不挂权限点），未登录 40101。
"""
from sqlalchemy import select

from app.core.constants import NotifyEvent
from app.models.notify import NotificationRecord, UserNotification
from app.services import notify_service
from tests.conftest import auth_header, login_for_tokens


async def _emit_ticket_approved(db_factory, receiver: str, *, ref_id: int = 1):
    """测试辅助：触发一次 ticket.approved 事件（emit 内部会同步落站内信）。"""
    async with db_factory() as session:
        await notify_service.emit(
            session, NotifyEvent.TICKET_APPROVED,
            title=f"工单 TK{ref_id} 审批通过", content=f"示例工单 {ref_id}：审批通过",
            receiver=receiver, ref_type="ticket", ref_id=ref_id,
        )
        await session.commit()


# ---------- emit 挂钩（服务层） ----------

class TestEmitInApp:
    async def test_emit_one_row_per_receiver(self, db_factory, seed):
        """收件人串逐人落行：重复用户名去重，未知用户名静默跳过。"""
        await _emit_ticket_approved(db_factory, "admin,ops1,admin,ghost")
        async with db_factory() as session:
            rows = (await session.execute(select(UserNotification))).scalars().all()
        assert {r.user_id for r in rows} == {seed["users"]["admin"], seed["users"]["ops1"]}
        assert len(rows) == 2
        assert all(r.event == "ticket.approved" and r.is_read is False for r in rows)
        assert all(r.ref_type == "ticket" and r.ref_id == 1 for r in rows)
        assert rows[0].title == "工单 TK1 审批通过"

    async def test_emit_without_mapping_still_inapp(self, db_factory, seed):
        """站内信不受渠道事件映射影响：无映射事件外发兜底 email，站内信照常落库。"""
        async with db_factory() as session:
            await notify_service.emit(session, NotifyEvent.EXECUTION_FAILED,
                                      title="执行失败", receiver="ops1",
                                      ref_type="execution", ref_id=9)
            await session.commit()
            inapp = (await session.execute(select(UserNotification))).scalars().all()
            outbound = (await session.execute(select(NotificationRecord))).scalars().all()
        assert len(inapp) == 1 and inapp[0].user_id == seed["users"]["ops1"]
        assert inapp[0].ref_type == "execution" and inapp[0].ref_id == 9
        assert len(outbound) == 1  # 外发兜底 email，两套记录同事务共存

    async def test_emit_no_receiver_no_rows(self, db_factory, seed):
        """收件人为空：不落任何站内信（不报错）。"""
        async with db_factory() as session:
            await notify_service.emit(session, NotifyEvent.TICKET_PENDING_APPROVAL,
                                      title="工单待审批", receiver=None)
            await session.commit()
            rows = (await session.execute(select(UserNotification))).scalars().all()
        assert rows == []


# ---------- /notifications 四接口 ----------

class TestNotificationApi:
    async def test_requires_login(self, client):
        """未登录 → 40101。"""
        resp = await client.get("/api/v1/notifications")
        assert resp.json()["code"] == 40101

    async def test_list_desc_and_only_unread(self, client, db_factory, seed):
        """列表按创建时间倒序；only_unread 只返回未读；普通用户登录即可访问。"""
        for i in range(1, 4):
            await _emit_ticket_approved(db_factory, "ops1", ref_id=i)
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/notifications", headers=headers)
        data = resp.json()["data"]
        assert data["total"] == 3
        assert [i["ref_id"] for i in data["items"]] == [3, 2, 1]  # 倒序
        # 把最新一条置已读后，only_unread 过滤生效
        await client.put(f"/api/v1/notifications/{data['items'][0]['id']}/read", headers=headers)
        resp = await client.get("/api/v1/notifications?only_unread=true", headers=headers)
        data = resp.json()["data"]
        assert data["total"] == 2 and all(not i["is_read"] for i in data["items"])

    async def test_list_only_mine(self, client, db_factory, seed):
        """列表天然只含本人数据：admin 的站内信不出现在 ops1 列表。"""
        await _emit_ticket_approved(db_factory, "admin")
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/notifications", headers=headers)
        assert resp.json()["data"]["total"] == 0

    async def test_unread_count_and_read_one(self, client, db_factory, seed):
        """未读数随单条已读递减；重复已读幂等。"""
        await _emit_ticket_approved(db_factory, "ops1", ref_id=1)
        await _emit_ticket_approved(db_factory, "ops1", ref_id=2)
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.json()["data"]["count"] == 2
        nid = (await client.get("/api/v1/notifications", headers=headers)
               ).json()["data"]["items"][0]["id"]
        resp = await client.put(f"/api/v1/notifications/{nid}/read", headers=headers)
        assert resp.json()["code"] == 0
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.json()["data"]["count"] == 1
        # 幂等：重复置已读仍成功，计数不变
        resp = await client.put(f"/api/v1/notifications/{nid}/read", headers=headers)
        assert resp.json()["code"] == 0
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.json()["data"]["count"] == 1

    async def test_read_one_not_mine_or_missing(self, client, db_factory, seed):
        """越权防护：标别人的通知与不存在的 ID 统一 40401。"""
        await _emit_ticket_approved(db_factory, "admin")
        async with db_factory() as session:
            nid = (await session.execute(select(UserNotification.id))).scalar_one()
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.put(f"/api/v1/notifications/{nid}/read", headers=headers)
        assert resp.json()["code"] == 40401
        resp = await client.put("/api/v1/notifications/99999/read", headers=headers)
        assert resp.json()["code"] == 40401

    async def test_read_all(self, client, db_factory, seed):
        """全部已读：返回本次置已读条数，二次调用返回 0，不影响他人。"""
        await _emit_ticket_approved(db_factory, "ops1,admin", ref_id=1)
        await _emit_ticket_approved(db_factory, "ops1", ref_id=2)
        headers = auth_header(await login_for_tokens(client, "ops1"))
        resp = await client.put("/api/v1/notifications/read-all", headers=headers)
        assert resp.json()["data"]["count"] == 2
        resp = await client.get("/api/v1/notifications/unread-count", headers=headers)
        assert resp.json()["data"]["count"] == 0
        resp = await client.put("/api/v1/notifications/read-all", headers=headers)
        assert resp.json()["data"]["count"] == 0
        # admin 的未读不受影响
        admin_headers = auth_header(await login_for_tokens(client, "admin"))
        resp = await client.get("/api/v1/notifications/unread-count", headers=admin_headers)
        assert resp.json()["data"]["count"] == 1
