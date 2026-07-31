"""全局搜索接口测试（SEARCH-02/03，04-API设计 §12）。

覆盖：未登录 40101 / 空 keyword 40001 / 四段聚合命中 /
每段限 5 条 + total / 无权限段为 null（auditor 无 ticket:read、template:read）。
"""
import pytest

from app.models.auth import User, UserRole
from app.models.cmdb import Application, Host
from app.models.job import TicketTemplate
from app.models.ticket import Ticket
from tests.conftest import TEST_PASSWORD_HASH, auth_header, login_for_tokens

pytestmark = pytest.mark.asyncio


async def _seed_data(db_factory, admin_id: int) -> None:
    """搜索用测试数据：主机/应用/模板/工单各含关键词 web 的记录。"""
    async with db_factory() as session:
        session.add(Host(hostname="web-01", ip="10.0.0.1", environment="prod"))
        session.add(Host(hostname="db-01", ip="10.0.0.2", environment="prod"))
        session.add(Application(name="web-portal", deploy_type="shell"))
        session.add(TicketTemplate(name="web发布模板", type="release", job_host_id=1))
        session.add(Ticket(
            ticket_no="T20260727-0001", template_id=1, template_version_snap=1,
            title="web发布模板", type="release", job_host_id=1,
            job_host_snap={"id": 1, "name": "web-agent"},
            creator_id=admin_id,
        ))
        await session.commit()


class TestSearchApi:
    async def test_requires_login(self, client):
        resp = await client.get("/api/v1/search", params={"keyword": "web"})
        assert resp.json()["code"] == 40101

    async def test_empty_keyword(self, client):
        tokens = await login_for_tokens(client, "admin")
        for kw in ["", "   "]:
            resp = await client.get(
                "/api/v1/search", params={"keyword": kw}, headers=auth_header(tokens)
            )
            assert resp.json()["code"] == 40001

    async def test_admin_all_segments(self, client, db_factory, seed):
        """admin 四段全出：keyword=web 命中主机名/应用名/工单标题/模板名。"""
        await _seed_data(db_factory, seed["users"]["admin"])
        tokens = await login_for_tokens(client, "admin")
        resp = await client.get(
            "/api/v1/search", params={"keyword": "web"}, headers=auth_header(tokens)
        )
        data = resp.json()["data"]
        assert data["hosts"]["total"] == 1
        assert data["hosts"]["items"][0]["hostname"] == "web-01"
        assert data["apps"]["total"] == 1
        assert data["apps"]["items"][0]["name"] == "web-portal"
        assert data["tickets"]["total"] == 1
        assert data["tickets"]["items"][0]["ticket_no"] == "T20260727-0001"
        assert data["templates"]["total"] == 1
        assert data["templates"]["items"][0]["type"] == "release"

    async def test_keyword_matches_host_ip(self, client, db_factory, seed):
        """主机段 keyword 同时匹配 IP。"""
        await _seed_data(db_factory, seed["users"]["admin"])
        tokens = await login_for_tokens(client, "admin")
        resp = await client.get(
            "/api/v1/search", params={"keyword": "10.0.0.2"}, headers=auth_header(tokens)
        )
        data = resp.json()["data"]
        assert data["hosts"]["total"] == 1
        assert data["hosts"]["items"][0]["hostname"] == "db-01"
        assert data["apps"]["total"] == 0

    async def test_segment_limit_five(self, client, db_factory):
        """单段命中超 5 条时 items 截断为 5、total 为真实总数。"""
        async with db_factory() as session:
            for i in range(7):
                session.add(Host(hostname=f"app-{i:02d}", ip=f"10.1.0.{i}", environment="prod"))
            await session.commit()
        tokens = await login_for_tokens(client, "admin")
        resp = await client.get(
            "/api/v1/search", params={"keyword": "app-"}, headers=auth_header(tokens)
        )
        hosts = resp.json()["data"]["hosts"]
        assert hosts["total"] == 7
        assert len(hosts["items"]) == 5

    async def test_perm_cut_segments_null(self, client, db_factory, seed):
        """auditor 无 ticket:read/template:read：对应段为 null，其余段正常返回。"""
        await _seed_data(db_factory, seed["users"]["admin"])
        async with db_factory() as session:
            u = User(
                username="aud1", password_hash=TEST_PASSWORD_HASH, display_name="aud1",
                source="local", status="active", must_change_password=False,
            )
            session.add(u)
            await session.flush()
            session.add(UserRole(user_id=u.id, role_id=seed["roles"]["auditor"]))
            await session.commit()
        tokens = await login_for_tokens(client, "aud1")
        resp = await client.get(
            "/api/v1/search", params={"keyword": "web"}, headers=auth_header(tokens)
        )
        data = resp.json()["data"]
        assert data["tickets"] is None
        assert data["templates"] is None
        assert data["hosts"]["total"] == 1
        assert data["apps"]["total"] == 1
