"""pytest 公共夹具：SQLite 内存库 + fakeredis 替身 + 手工种子。

说明：
- seed.py 依赖 MySQL GET_LOCK 无法在 SQLite 跑，测试内手工插入权限/角色/用户种子；
- MySQL 专属的 server_default（CURRENT_TIMESTAMP(3) / ON UPDATE）SQLite 不支持，
  建表前统一替换为 CURRENT_TIMESTAMP；
- bcrypt cost 12 较慢，密码哈希在模块级只算一次全局复用。
"""
import fakeredis.aioredis
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger, DefaultClause, Integer, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import redis as redis_mod
from app.core.constants import BUILTIN_ROLES, PERMISSIONS
from app.core.database import get_db
from app.core.security import hash_password
from app.main import app
from app.models import Base
from app.models.auth import Permission, Role, RolePermission, User, UserRole

# ---- SQLite 兼容处理：替换 MySQL 专属默认值子句（仅测试进程内生效） ----
for _table in Base.metadata.tables.values():
    for _col in _table.columns:
        if _col.server_default is not None:
            _col.server_default = DefaultClause(text("CURRENT_TIMESTAMP"))

# audit_log 复合主键 (id, created_at) 是 MySQL 分区要求；SQLite 不支持复合主键
# 上的 autoincrement（测试不落审计行，关闭即可通过建表）
Base.metadata.tables["audit_log"].c.id.autoincrement = False

# SQLite 仅对 INTEGER PRIMARY KEY（rowid 别名）自增，BIGINT 主键插入不会生成 id，
# 统一把单列自增主键降级为 Integer
for _table in Base.metadata.tables.values():
    _pk_cols = list(_table.primary_key.columns)
    if len(_pk_cols) == 1 and isinstance(_pk_cols[0].type, BigInteger):
        _pk_cols[0].type = Integer()

# 测试统一密码（bcrypt 只哈希一次，全部种子用户复用）
TEST_PASSWORD = "Passw0rd123"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)


@pytest.fixture
async def db_factory():
    """每测试独立的 SQLite 内存库与会话工厂。"""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,  # 内存库须共享同一连接
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
async def fake_redis(monkeypatch):
    """fakeredis 替身：整个 app 统一经 redis_mod.redis_client 访问，直接替换模块属性。"""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(redis_mod, "redis_client", fake)
    yield fake
    await fake.aclose()


@pytest.fixture
async def seed(db_factory):
    """手工种子：权限全集 + 内置角色矩阵 + 三个测试用户。

    用户：
        admin   admin 角色（must_change=False，供 RBAC 测试直接登录）
        ops1    ops 角色（普通用户）
        newbie  ops 角色 + must_change_password=True（40105 流程用）
    返回 {角色code: id} 与用户 id 的映射。
    """
    async with db_factory() as session:
        perm_ids: dict[str, int] = {}
        for code, name, module in PERMISSIONS:
            p = Permission(code=code, name=name, module=module)
            session.add(p)
            await session.flush()
            perm_ids[code] = p.id
        role_ids: dict[str, int] = {}
        for code, meta in BUILTIN_ROLES.items():
            r = Role(code=code, name=meta["name"], is_builtin=True, description=meta["description"])
            session.add(r)
            await session.flush()
            role_ids[code] = r.id
            for pc in meta["permissions"]:
                session.add(RolePermission(role_id=r.id, permission_id=perm_ids[pc]))
        users = {}
        for username, role_code, must_change in [
            ("admin", "admin", False),
            ("ops1", "ops", False),
            ("newbie", "ops", True),
        ]:
            u = User(
                username=username,
                password_hash=TEST_PASSWORD_HASH,
                display_name=username,
                source="local",
                status="active",
                must_change_password=must_change,
            )
            session.add(u)
            await session.flush()
            session.add(UserRole(user_id=u.id, role_id=role_ids[role_code]))
            users[username] = u.id
        await session.commit()
    return {"roles": role_ids, "users": users}


@pytest.fixture
async def client(db_factory, seed):
    """带依赖覆盖的 ASGI 测试客户端（get_db 指向 SQLite 内存库）。"""

    async def _get_db():
        async with db_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def login_for_tokens(client: AsyncClient, username: str, password: str = TEST_PASSWORD) -> dict:
    """测试辅助：登录并断言成功，返回令牌载荷。"""
    resp = await client.post("/api/v1/auth/login", json={"username": username, "password": password})
    body = resp.json()
    assert body["code"] == 0, f"登录失败: {body}"
    return body["data"]


def auth_header(tokens: dict) -> dict:
    """测试辅助：构造 Bearer 头。"""
    return {"Authorization": f"Bearer {tokens['access_token']}"}
