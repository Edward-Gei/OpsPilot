# 作业主机关联凭据 + 步骤脚本调用凭据 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 作业主机以 `credential_id` 引用凭据管理中的凭据（不再直填密文）；模板可声明"引用凭据"，步骤脚本执行时以环境变量 `$CRED_<别名大写>_USER/_SECRET/_PASSPHRASE` 读取。

**Architecture:** 后端删除 job_host 四个认证列改为 FK 引用 Credential（`open_connection` 鸭子类型第三参直接换成 Credential 行）；模板/工单新增 `credential_refs` JSON（模板声明→工单快照冻结→执行时实时解密），shell 步骤经 stdin 前置 `export` 行注入 env（不落盘/不进日志/不进快照）。设计规格见 `docs/superpowers/specs/2026-07-31-credential-integration-design.md`。

**Tech Stack:** FastAPI + SQLAlchemy(asyncmy) + Alembic（幂等迁移，head=0008，本次新增 0009）+ asyncssh；Vue 3 + TS + ant-design-vue 4.x。

**运行命令口径（Windows PowerShell）：**
- 后端测试：`cd d:\SourceCode\qoder\backend; python -m pytest tests/test_job_api.py -q`
- 前端构建：`cd d:\SourceCode\qoder\frontend; npm run build; npx vue-tsc --noEmit`
- 存量数据均为测试数据，迁移直接删列不做数据搬迁（用户已确认）。

---

### Task 1: 数据模型改造（JobHost / TicketTemplate / Ticket）

**Files:**
- Modify: `backend/app/models/cmdb.py`（JobHost，L70-95）
- Modify: `backend/app/models/job.py`（TicketTemplate，L36-68）
- Modify: `backend/app/models/ticket.py`（Ticket，L26-52）

- [ ] **Step 1: JobHost 删除认证四字段、新增 credential_id**

`backend/app/models/cmdb.py` 中 JobHost 的 `login_user/auth_type/secret_enc/passphrase_enc` 四个 mapped_column 整体替换为：

```python
    credential_id: Mapped[int | None] = mapped_column(
        UBIGINT, comment="关联凭据 credential.id（登录认证随凭据管理；API 层必填）"
    )
```

同步修改类 docstring 为：`"""作业主机（Jenkins agent）：替平台执行 Pipeline 步骤的通用执行节点；登录认证引用凭据管理（credential_id）。"""`。确认文件顶部 import 中已有 `UBIGINT`（原 created_by 已在用），并删除因此不再使用的 `Text` import（若同文件其他模型仍在用则保留）。

- [ ] **Step 2: TicketTemplate 新增 credential_refs**

`backend/app/models/job.py` TicketTemplate 中 `visible_role_ids` 字段之后插入：

```python
    credential_refs: Mapped[list | None] = mapped_column(
        JSON, comment="引用凭据 [{alias, credential_id}]；执行时以 CRED_<ALIAS>_* 环境变量注入 shell 步骤"
    )
```

- [ ] **Step 3: Ticket 新增 credential_refs 快照**

`backend/app/models/ticket.py` Ticket 中 `exec_strategy_snap` 字段之后插入：

```python
    credential_refs: Mapped[list | None] = mapped_column(
        JSON, comment="引用凭据快照 [{alias, credential_id, credential_name}]；执行时按 id 实时取密文"
    )
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models
git commit -m "feat(model): 作业主机引用凭据(credential_id)；模板/工单新增 credential_refs"
```

---

### Task 2: 迁移 0009

**Files:**
- Create: `backend/alembic/versions/0009_job_host_credential_ref.py`

- [ ] **Step 1: 编写幂等迁移**

```python
"""凭据打通：作业主机改为引用凭据；模板/工单新增引用凭据列。

变更内容（对齐设计文档 2026-07-31-credential-integration）：
    - job_host：+credential_id / -login_user -auth_type -secret_enc -passphrase_enc
      （存量为测试数据，直接删列不迁移；存量主机需重新关联凭据）
    - ticket_template：+credential_refs JSON（[{alias, credential_id}]）
    - ticket：+credential_refs JSON（提单快照 [{alias, credential_id, credential_name}]）

幂等策略：与 0007/0008 一致，先查 information_schema 判断列是否存在再执行，
全新库（0001 create_all 即新结构）与已升级库重复执行结果一致。
"""
from alembic import op
from sqlalchemy import text

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def _column_exists(bind, table: str, column: str) -> bool:
    """判断列是否存在（当前库）。"""
    return bool(
        bind.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()
    # 1. job_host：+credential_id，删认证四列
    if not _column_exists(bind, "job_host", "credential_id"):
        op.execute(
            "ALTER TABLE job_host ADD COLUMN credential_id BIGINT UNSIGNED NULL "
            "COMMENT '关联凭据 credential.id（登录认证随凭据管理；API 层必填）'"
        )
    for col in ("login_user", "auth_type", "secret_enc", "passphrase_enc"):
        if _column_exists(bind, "job_host", col):
            op.execute(f"ALTER TABLE job_host DROP COLUMN {col}")
    # 2. ticket_template：+credential_refs
    if not _column_exists(bind, "ticket_template", "credential_refs"):
        op.execute(
            "ALTER TABLE ticket_template ADD COLUMN credential_refs JSON NULL "
            "COMMENT '引用凭据 [{alias, credential_id}]'"
        )
    # 3. ticket：+credential_refs
    if not _column_exists(bind, "ticket", "credential_refs"):
        op.execute(
            "ALTER TABLE ticket ADD COLUMN credential_refs JSON NULL "
            "COMMENT '引用凭据快照 [{alias, credential_id, credential_name}]'"
        )


def downgrade() -> None:
    # 认证列数据已删除无法恢复，不提供回滚
    pass
```

- [ ] **Step 2: Commit**

```bash
git add backend/alembic/versions/0009_job_host_credential_ref.py
git commit -m "feat(migration): 0009 作业主机引用凭据 + credential_refs 列"
```

---

### Task 3: 作业主机 Schema 与 API 改造

**Files:**
- Modify: `backend/app/schemas/job.py`（L169-218 作业主机四个 schema）
- Modify: `backend/app/api/v1/job_hosts.py`（`_detail`、create、update 路由）

- [ ] **Step 1: 重写作业主机请求/响应 schema**

`backend/app/schemas/job.py` 中 `JobHostCreateRequest` / `JobHostUpdateRequest` / `JobHostDetailResponse` 整体替换为（`JobHostStatusRequest` 不动）：

```python
class JobHostCreateRequest(BaseModel):
    """新建作业主机：登录认证引用凭据管理（credential_id 必填）。"""

    name: str = Field(..., min_length=1, max_length=128)
    ip: str = Field(..., min_length=1, max_length=45)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    credential_id: int = Field(..., description="关联凭据 credential.id")
    workdir: str = Field(default="/opt/opspilot/workspace", max_length=255)


class JobHostUpdateRequest(BaseModel):
    """编辑作业主机：全字段可选，credential_id 传值即切换关联凭据。"""

    name: str | None = Field(default=None, max_length=128)
    ip: str | None = Field(default=None, max_length=45)
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    credential_id: int | None = Field(default=None)
    workdir: str | None = Field(default=None, max_length=255)
```

```python
class JobHostDetailResponse(BaseModel):
    """作业主机详情：认证信息只暴露关联凭据 id（名称由路由层 join 补充）。"""

    id: int
    name: str
    ip: str
    ssh_port: int
    credential_id: int | None = None
    workdir: str
    enabled: bool
    last_check_at: datetime | None = None
    last_check_ok: bool | None = None
    last_check_msg: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
```

- [ ] **Step 2: 路由层去掉加密逻辑，补充 credential_name**

`backend/app/api/v1/job_hosts.py`：
1. 删除 `from app.core.security import encrypt_text` import。
2. `_detail` 改为（需要凭据名映射，由调用方传入）：

```python
def _detail(jh, cred_names: dict[int, str] | None = None) -> dict:
    """作业主机统一序列化：认证信息只暴露关联凭据 id/名称，永无密文。"""
    d = JobHostDetailResponse.model_validate(jh).model_dump(mode="json")
    d["credential_name"] = (cred_names or {}).get(jh.credential_id)
    return d
```

3. 列表路由 `list_job_hosts` 中改为先取凭据名映射再序列化：

```python
    hosts, total = await job_host_service.list_job_hosts(
        session, keyword=keyword, enabled=enabled, page=page, page_size=page_size
    )
    cred_names = await job_host_service.credential_names(
        session, [jh.credential_id for jh in hosts]
    )
    return ok({"items": [_detail(jh, cred_names) for jh in hosts], "total": total,
               "page": page, "page_size": page_size})
```

4. `create_job_host` 路由主体改为（不再 pop secret/加密）：

```python
    jh = await job_host_service.create_job_host(
        session, data=req.model_dump(), created_by=actor.id
    )
    audit.log(module="job", action="job_host.create", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="job_host", target_id=str(jh.id), target_name=jh.name,
              detail={"ip": jh.ip, "ssh_port": jh.ssh_port,
                      "credential_id": jh.credential_id})
    return ok({"id": jh.id})
```

5. `update_job_host` 路由主体改为：

```python
    payload = req.model_dump(exclude_none=True)
    jh = await job_host_service.update_job_host(session, job_host_id, data=payload)
    audit.log(module="job", action="job_host.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="job_host", target_id=str(jh.id), target_name=jh.name,
              detail={"ip": jh.ip, "ssh_port": jh.ssh_port,
                      "credential_id": jh.credential_id})
    return ok(_detail(jh, await job_host_service.credential_names(session, [jh.credential_id])))
```

6. `get_job_host` 详情路由同样传映射：`return ok(_detail(jh, await job_host_service.credential_names(session, [jh.credential_id])))`；`set_enabled` 路由末尾同理。

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/job.py backend/app/api/v1/job_hosts.py
git commit -m "feat(api): 作业主机 API 改为 credential_id 关联，去除直填密文"
```

（此时代码尚不可运行——service 层在 Task 4 跟进，属同一逻辑变更的分步提交。）

---

### Task 4: 作业主机 Service 改造（校验 + 连通性测试）

**Files:**
- Modify: `backend/app/services/job_host_service.py`

- [ ] **Step 1: 新增凭据校验与名称映射工具**

文件顶部 import 增加 `from app.models.job import Credential, TicketTemplate`（替换原 `from app.models.job import TicketTemplate`）。模块 docstring 的安全约束段改为：

```python
"""作业主机业务服务：CRUD + 连通性测试 + 启用/禁用（执行范式改造）。

安全约束：
- 登录认证引用凭据管理（credential_id），本模块不再持有任何密文字段；
- 删除保护：被工单模板（ticket_template.job_host_id）引用时 42201。
"""
```

在 `create_job_host` 前新增两个函数：

```python
async def _ensure_credential_exists(session: AsyncSession, credential_id: int) -> None:
    """关联凭据存在性校验（40401）。"""
    if await session.get(Credential, credential_id) is None:
        raise Errors.not_found("关联凭据不存在")


async def credential_names(
    session: AsyncSession, credential_ids: list[int | None]
) -> dict[int, str]:
    """批量取凭据 id→名称映射（列表/详情序列化补充 credential_name 用）。"""
    ids = {i for i in credential_ids if i}
    if not ids:
        return {}
    rows = await session.execute(
        select(Credential.id, Credential.name).where(Credential.id.in_(ids))
    )
    return dict(rows.all())
```

- [ ] **Step 2: create/update 校验凭据**

`create_job_host` 中 `await _ensure_ip_port_unique(...)` 之后插入一行：

```python
    await _ensure_credential_exists(session, data["credential_id"])
```

docstring 改为 `"""新增作业主机；登录认证引用凭据（credential_id 已在 schema 层必填）。"""`。

`update_job_host` 中 IP/端口唯一性校验之后、`for field, value in data.items()` 之前插入：

```python
    if data.get("credential_id") and data["credential_id"] != jh.credential_id:
        await _ensure_credential_exists(session, data["credential_id"])
```

- [ ] **Step 3: test_connectivity 加载凭据**

`test_connectivity` 中 `jh = await get_job_host(session, job_host_id)` 之后、`ok, message = False, ""` 之前插入：

```python
    cred = await session.get(Credential, jh.credential_id) if jh.credential_id else None
    if cred is None:
        checked_at = datetime.now()
        jh.last_check_at = checked_at
        jh.last_check_ok = False
        jh.last_check_msg = "未关联凭据，请先在凭据管理中创建并关联"
        await session.flush()
        return {"ok": False, "message": jh.last_check_msg, "checked_at": checked_at}
```

并把 `_probe` 内的建连行改为 `conn = await open_connection(jh.ip, jh.ssh_port, cred)`。docstring 中"JobHost 与 Credential 凭据字段同构…"一段改为：`"""连通性测试：按关联凭据 SSH 连接作业主机执行 echo ok，结果落 last_check_* 字段。"""`。

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/job_host_service.py
git commit -m "feat(service): 作业主机凭据关联校验与连通性测试改造"
```

---

### Task 5: 凭据删除保护

**Files:**
- Modify: `backend/app/services/credential_service.py`

- [ ] **Step 1: delete_credential 增加引用保护**

import 区增加 `from app.models.cmdb import JobHost` 与 `from app.models.job import Credential, TicketTemplate`（合并原有 Credential import）。`delete_credential` 整体替换为：

```python
async def delete_credential(session: AsyncSession, credential_id: int) -> Credential:
    """删除凭据；被作业主机或模板引用时拒绝（42201）。"""
    cred = await get_credential_or_404(session, credential_id)
    jh_count = (
        await session.execute(
            select(func.count()).select_from(JobHost)
            .where(JobHost.credential_id == credential_id)
        )
    ).scalar_one()
    if jh_count:
        raise Errors.rejected(f"凭据被 {jh_count} 台作业主机引用，无法删除")
    # credential_refs 为 JSON 数组，MySQL 侧用 JSON_CONTAINS 精确匹配 credential_id
    tpl_count = (
        await session.execute(
            select(func.count()).select_from(TicketTemplate).where(
                func.json_contains(
                    func.coalesce(TicketTemplate.credential_refs, "[]"),
                    f'{{"credential_id": {credential_id}}}',
                )
            )
        )
    ).scalar_one()
    if tpl_count:
        raise Errors.rejected(f"凭据被 {tpl_count} 个工单模板引用，无法删除")
    await session.delete(cred)
    await session.flush()
    return cred
```

同时把模块 docstring 中"凭据为独立管理数据，删除无需引用保护"改为"凭据被作业主机 / 模板引用时删除保护（42201）"。`backend/app/api/v1/credentials.py` 中 delete 路由 docstring 改为 `"""删除凭据；被作业主机或模板引用时 42201。"""`。

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/credential_service.py backend/app/api/v1/credentials.py
git commit -m "feat(service): 凭据删除保护（被作业主机/模板引用时 42201）"
```

---

### Task 6: 模板 credential_refs（Schema + Service + API）

**Files:**
- Modify: `backend/app/schemas/job.py`（TemplateUpsertRequest 区域）
- Modify: `backend/app/services/template_service.py`（_validate_refs/_build_snapshot/_RULE_KEYS/_apply_basic）
- Modify: `backend/app/api/v1/templates.py`（详情响应）

- [ ] **Step 1: 新增 CredentialRefInput 并挂到 TemplateUpsertRequest**

`backend/app/schemas/job.py` 中 `TemplateUpsertRequest` 之前新增：

```python
class CredentialRefInput(BaseModel):
    """模板引用凭据行：执行时以 CRED_<ALIAS大写>_USER/_SECRET/_PASSPHRASE 注入 shell 步骤。"""

    alias: str = Field(min_length=1, max_length=32)
    credential_id: int

    @field_validator("alias")
    @classmethod
    def validate_alias(cls, v: str) -> str:
        """别名限标识符格式（映射为环境变量名的一部分）。"""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", v):
            raise ValueError(f"凭据别名 {v} 不合法：仅限字母/数字/下划线且以字母开头")
        return v
```

`TemplateUpsertRequest` 中 `visible_role_ids` 字段后新增字段与校验器：

```python
    credential_refs: list[CredentialRefInput] = Field(
        default_factory=list, max_length=10,
        description="引用凭据；脚本内以 $CRED_<别名大写>_USER/_SECRET 读取"
    )
```

在 `validate_approval` 校验器之后追加：

```python
    @field_validator("credential_refs")
    @classmethod
    def validate_ref_alias_unique(cls, v: list[CredentialRefInput]) -> list[CredentialRefInput]:
        """别名大写后不允许重复（环境变量名冲突）。"""
        names = [r.alias.upper() for r in v]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise ValueError(f"凭据别名重复: {sorted(dup)}")
        return v
```

- [ ] **Step 2: template_service 落库与升版**

`backend/app/services/template_service.py`：
1. import 区补 `from app.models.job import Credential`（合并到现有 job 模型 import 行）。
2. `_validate_refs` 末尾追加引用凭据校验：

```python
    ref_ids = {r["credential_id"] for r in data.get("credential_refs") or []}
    if ref_ids:
        found = set(
            (await session.execute(select(Credential.id).where(Credential.id.in_(ref_ids)))).scalars()
        )
        if ref_ids - found:
            raise Errors.param(f"引用凭据不存在: {sorted(ref_ids - found)}")
```

3. `_build_snapshot` 返回 dict 中 `"visible_role_ids"` 行后加 `"credential_refs": data.get("credential_refs") or [],`。
4. `_RULE_KEYS` 元组末尾加 `"credential_refs",`（引用凭据变更触发升版）。
5. `_apply_basic` 末尾加 `tpl.credential_refs = data.get("credential_refs") or []`。

- [ ] **Step 3: 模板详情响应返回 credential_refs（带名称）**

`backend/app/api/v1/templates.py` `get_template` 中 `data.update({...})` 的 dict 内加一项（import 区补 `from app.services import job_host_service`，若未导入）：

```python
        "credential_refs": [
            {**r, "credential_name": cred_names.get(r["credential_id"])}
            for r in (tpl.credential_refs or [])
        ],
```

并在 `data.update` 之前取映射：

```python
    cred_names = await job_host_service.credential_names(
        session, [r["credential_id"] for r in (tpl.credential_refs or [])]
    )
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/job.py backend/app/services/template_service.py backend/app/api/v1/templates.py
git commit -m "feat(template): 模板级引用凭据 credential_refs（声明/校验/升版/详情回显）"
```

---

### Task 7: 工单快照（ticket_service + 详情响应）

**Files:**
- Modify: `backend/app/services/ticket_service.py`（约 L194-217 提单快照）
- Modify: `backend/app/api/v1/tickets.py`（详情序列化）

- [ ] **Step 1: 提单时冻结 credential_refs、job_host_snap 去掉 login_user**

`ticket_service.py` 提单函数中，`flow_snap = await _flow_preview(session, tpl)` 附近插入引用凭据名称快照组装（import 区已有的模型 import 行补 `Credential`，来源 `app.models.job`）：

```python
    ref_ids = [r["credential_id"] for r in (tpl.credential_refs or [])]
    cred_names: dict[int, str] = {}
    if ref_ids:
        rows = await session.execute(
            select(Credential.id, Credential.name).where(Credential.id.in_(ref_ids))
        )
        cred_names = dict(rows.all())
    credential_refs_snap = [
        {"alias": r["alias"], "credential_id": r["credential_id"],
         "credential_name": cred_names.get(r["credential_id"], "")}
        for r in (tpl.credential_refs or [])
    ]
```

`Ticket(...)` 构造中：
1. `job_host_snap=` 行改为（登录账号已随凭据管理，快照不再含 login_user）：

```python
        job_host_snap={"id": jh.id, "name": jh.name, "ip": jh.ip,
                       "ssh_port": jh.ssh_port, "workdir": jh.workdir} if jh else {},
```

2. `exec_strategy_snap=tpl.exec_strategy or {},` 之后加 `credential_refs=credential_refs_snap,`。

- [ ] **Step 2: 工单详情返回 credential_refs**

`backend/app/api/v1/tickets.py` 详情路由的序列化处（找到输出 `job_host` / `exec_strategy_snap` 等快照字段的 dict），同级加一项：

```python
        "credential_refs": ticket.credential_refs or [],
```

（仅别名与凭据名称，无任何密文。）

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/ticket_service.py backend/app/api/v1/tickets.py
git commit -m "feat(ticket): 提单冻结 credential_refs 快照；job_host_snap 去除 login_user"
```

---

### Task 8: 执行链路（env 注入 + 凭据建连）

**Files:**
- Modify: `backend/app/engine/ssh_runner.py`
- Modify: `backend/app/engine/ansible_runner.py`
- Modify: `backend/app/engine/pipeline.py`
- Test: `backend/tests/test_execution_engine.py`

- [ ] **Step 1: 写失败的单元测试（env 前置与转义）**

`backend/tests/test_execution_engine.py` 末尾追加：

```python
# ---------- 凭据环境变量注入（设计 2026-07-31：CRED_<ALIAS>_* 前置 export） ----------

def test_env_prelude_quoting():
    """单引号安全转义：值含单引号/换行（私钥）也能原样还原。"""
    from app.engine.ssh_runner import _env_prelude

    prelude = _env_prelude({"CRED_DB_SECRET": "pa'ss\nline2", "CRED_DB_USER": "root"})
    assert "export CRED_DB_SECRET='pa'\\''ss\nline2'" in prelude
    assert "export CRED_DB_USER='root'" in prelude


def test_build_cred_env():
    """按别名组装 CRED_<ALIAS大写>_USER/_SECRET；无口令不注入 _PASSPHRASE。"""
    from types import SimpleNamespace
    from app.core.security import encrypt_text
    from app.engine.pipeline import build_cred_env

    cred = SimpleNamespace(login_user="root", secret_enc=encrypt_text("s3cret"),
                           passphrase_enc=None)
    env = build_cred_env("mysql", cred)
    assert env == {"CRED_MYSQL_USER": "root", "CRED_MYSQL_SECRET": "s3cret"}
```

- [ ] **Step 2: 运行确认失败**

Run: `cd d:\SourceCode\qoder\backend; python -m pytest tests/test_execution_engine.py -q -k "env_prelude or build_cred_env"`
Expected: FAIL（`ImportError: cannot import name '_env_prelude'`）

- [ ] **Step 3: ssh_runner 实现 env 注入**

`backend/app/engine/ssh_runner.py`：
1. 模块内（`open_connection` 之后）新增：

```python
def _env_prelude(env: dict[str, str]) -> str:
    """凭据 env 前置 export 段：单引号包裹 + `'` 转义为 `'\\''`。

    经 stdin 写入 bash -s，不落盘、不进执行日志；用户脚本内的 set -x
    在其后才生效，追踪不到这些 export 行。
    """
    lines = []
    for key, value in env.items():
        safe = value.replace("'", "'\\''")
        lines.append(f"export {key}='{safe}'")
    return "\n".join(lines)
```

2. `run_shell_on_job_host` 签名在 `job_host,` 后加两个仅限关键字参数：

```python
    credential,
    env: dict[str, str] | None = None,
```

3. 函数体两处调整：建连行改 `conn = await open_connection(job_host.ip, job_host.ssh_port, credential)`；`process.stdin.write(script + "\n")` 之前插入：

```python
            if env:
                process.stdin.write(_env_prelude(env) + "\n")
```

4. docstring 中"JobHost 凭据字段与 Credential 同构…"一段改为"登录认证来自作业主机关联的凭据（credential 形参）；env 为模板引用凭据的环境变量，经 stdin 前置 export 注入（不落盘不进日志）。"

- [ ] **Step 4: ansible_runner 传凭据（不注入 env）**

`backend/app/engine/ansible_runner.py`：`run_ansible_on_job_host` 签名 `job_host,` 后加 `credential,`；建连行改 `conn = await open_connection(job_host.ip, job_host.ssh_port, credential)`。docstring 追加一句："playbook 步骤不注入凭据环境变量（命令行内联 env 有 ps 泄露风险，凭据注入仅 shell 步骤支持）。"

- [ ] **Step 5: pipeline 加载凭据并组装 env**

`backend/app/engine/pipeline.py`：
1. import 区补 `from app.models.job import Credential`（并入现有 import）与 `from app.core.security import decrypt_text`。
2. 模块级（`render_script` 之后）新增：

```python
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
```

3. `__init__` 中 `self.job_host: JobHost | None = None` 之后加：

```python
        self.credential: Credential | None = None   # 作业主机登录凭据（建连用）
        self.cred_env: dict[str, str] = {}          # 模板引用凭据 env（shell 步骤注入）
        self.cred_error: str | None = None          # 凭据加载失败原因（步骤级失败归档）
```

4. `_load_context` 末尾 `return True` 之前加载凭据（加载失败不在此处终止，记入 cred_error 由步骤失败归档，保证用户可见）：

```python
        # 加载登录凭据与引用凭据；失败原因记 cred_error，由步骤按失败归档（用户可见）
        if self.job_host.credential_id:
            self.credential = await s.get(Credential, self.job_host.credential_id)
        if self.credential is None:
            self.cred_error = "作业主机未关联凭据或凭据已删除，请在系统设置中重新关联"
            return True
        for ref in self.ticket.credential_refs or []:
            cred = await s.get(Credential, ref["credential_id"])
            if cred is None:
                self.cred_error = f"引用凭据 {ref.get('credential_name') or ref['credential_id']} 已被删除"
                break
            try:
                self.cred_env.update(build_cred_env(ref["alias"], cred))
            except RuntimeError as exc:   # 密文损坏：decrypt_text 统一转译
                self.cred_error = str(exc)
                break
```

5. `_run_one_step` 中"渲染脚本参数"块之前插入凭据兜底：

```python
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
```

6. `_run_one_step` 中两处执行调用补参数：playbook 分支加 `credential=self.credential,`；shell 分支改为：

```python
            status, exit_code, summary = await ssh_runner.run_shell_on_job_host(
                job_host=jh, credential=self.credential, script=script,
                env=self.cred_env or None, timeout=tstep.timeout,
                step_order=step.step_order, log=self.log, control=self.control,
            )
```

- [ ] **Step 6: 运行单元测试确认通过**

Run: `cd d:\SourceCode\qoder\backend; python -m pytest tests/test_execution_engine.py -q`
Expected: 新增 2 个用例 PASS；存量引擎用例若因 `run_shell_on_job_host` 新签名失败，在本任务内一并修复（调用处补 `credential=...` 形参，用测试内现有 job_host 对象或 SimpleNamespace 凭据）。

- [ ] **Step 7: Commit**

```bash
git add backend/app/engine backend/tests/test_execution_engine.py
git commit -m "feat(engine): 建连凭据来自关联凭据；shell 步骤注入 CRED_* 环境变量"
```

---

### Task 9: 后端测试夹具全量适配 + API 测试补充

**Files:**
- Modify: `backend/tests/test_job_api.py`
- Modify: 其余引用作业主机创建的测试（`grep -l "job-hosts" backend/tests` 全部命中文件，含 test_execution_api / test_ticket_api / test_dashboard_api / test_search_api / test_smoke 等）

- [ ] **Step 1: 统一改造 JOB_HOST_PAYLOAD 形态**

`test_job_api.py` 中：

```python
JOB_HOST_PAYLOAD = {
    "name": "job-agent-01", "ip": "10.8.0.1", "ssh_port": 22,
    "workdir": "/opt/opspilot/workspace",
}
```

`_base_env` 改为先建凭据再建主机：

```python
async def _base_env(client) -> dict:
    """公共前置：admin/ops 登录 + 1 凭据 + 1 作业主机（主机关联凭据）。"""
    admin_h = auth_header(await login_for_tokens(client, "admin"))
    ops_h = auth_header(await login_for_tokens(client, "ops1"))
    cred_id = await _create_credential(client, admin_h)
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "credential_id": cred_id},
                             headers=admin_h)
    job_host_id = resp.json()["data"]["id"]
    return {"admin_h": admin_h, "ops_h": ops_h, "job_host_id": job_host_id,
            "credential_id": cred_id}
```

其余测试文件：全局搜索创建作业主机的 payload（含 `login_user`/`auth_type`/`secret` 键的 job-hosts POST），一律改成"先 POST /api/v1/credentials 建凭据 → POST /api/v1/job-hosts 传 credential_id"的两步形态；直接构造 `JobHost(...)` ORM 对象的测试删掉认证四字段、补 `credential_id=<已建凭据id>`。

- [ ] **Step 2: 补充新行为断言（test_job_api.py 追加）**

```python
async def test_job_host_credential_binding(client):
    """作业主机凭据关联：必填校验 / 不存在 40401 / 响应带 credential_name / 删除保护。"""
    env = await _base_env(client)
    admin_h = env["admin_h"]
    # credential_id 缺失 → 422 参数校验失败（pydantic 必填）
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "name": "no-cred", "ip": "10.8.0.9"},
                             headers=admin_h)
    assert resp.json()["code"] != 0
    # 凭据不存在 → 40401
    resp = await client.post("/api/v1/job-hosts",
                             json={**JOB_HOST_PAYLOAD, "name": "bad-cred", "ip": "10.8.0.8",
                                   "credential_id": 99999}, headers=admin_h)
    assert resp.json()["code"] == 40401
    # 列表响应带 credential_name 且无密文字段
    resp = await client.get("/api/v1/job-hosts", headers=admin_h)
    row = next(r for r in resp.json()["data"]["items"] if r["id"] == env["job_host_id"])
    assert row["credential_name"] == CRED_PAYLOAD["name"]
    assert "secret_enc" not in row and "auth_type" not in row
    # 被作业主机引用的凭据不可删除 → 42201
    resp = await client.delete(f"/api/v1/credentials/{env['credential_id']}", headers=admin_h)
    assert resp.json()["code"] == 42201


async def test_template_credential_refs(client):
    """模板引用凭据：alias 校验 / 详情回显 / 变更升版 / 工单快照冻结。"""
    env = await _base_env(client)
    admin_h = env["admin_h"]
    ref = [{"alias": "mysql", "credential_id": env["credential_id"]}]
    # alias 非法 → 参数错误
    bad = _tpl_payload(env, credential_refs=[{"alias": "1bad", "credential_id": env["credential_id"]}])
    resp = await client.post("/api/v1/templates", json=bad, headers=admin_h)
    assert resp.json()["code"] != 0
    # 正常创建 + 详情回显（含 credential_name）
    resp = await client.post("/api/v1/templates",
                             json=_tpl_payload(env, credential_refs=ref), headers=admin_h)
    tpl_id = resp.json()["data"]["id"]
    detail = (await client.get(f"/api/v1/templates/{tpl_id}", headers=admin_h)).json()["data"]
    assert detail["credential_refs"][0]["alias"] == "mysql"
    assert detail["credential_refs"][0]["credential_name"] == CRED_PAYLOAD["name"]
    # 引用变更触发升版
    resp = await client.put(f"/api/v1/templates/{tpl_id}",
                            json=_tpl_payload(env, credential_refs=[]), headers=admin_h)
    assert resp.json()["data"]["version_bumped"] is True
```

（`_tpl_payload` 已支持 override 顶层键，无需改动。）

- [ ] **Step 3: 全量后端测试跑通**

Run: `cd d:\SourceCode\qoder\backend; python -m pytest -q`
Expected: 全部 PASS（凡因作业主机 payload 形态未适配的失败逐一修复）。

- [ ] **Step 4: Commit**

```bash
git add backend/tests
git commit -m "test: 作业主机凭据关联与模板引用凭据测试夹具全量适配"
```

---

### Task 10: 前端 API 类型层

**Files:**
- Modify: `frontend/src/api/jobHost.ts`
- Modify: `frontend/src/api/job.ts`
- Modify: `frontend/src/api/ticket.ts`（工单详情类型，若有 job_host_snap/详情接口类型定义）

- [ ] **Step 1: jobHost.ts 类型替换**

`JobHost` 接口删除 `login_user` / `auth_type` / `has_passphrase` 三行，改为：

```typescript
  credential_id: number | null
  credential_name: string | null
```

`JobHostCreate` 改为：

```typescript
export interface JobHostCreate {
  name: string
  ip: string
  ssh_port?: number
  credential_id: number
  workdir?: string
}
```

`JobHostUpdate` 删除 login_user/auth_type/secret/passphrase 四行、加 `credential_id?: number`，注释改为 `/** 编辑表单：credential_id 传值即切换关联凭据 */`。

- [ ] **Step 2: job.ts 模板类型补充**

在 `TemplateParam` 附近新增并挂到表单/详情类型：

```typescript
/** 模板引用凭据行：脚本内以 $CRED_<别名大写>_USER / _SECRET / _PASSPHRASE 读取 */
export interface CredentialRef {
  alias: string
  credential_id: number
  credential_name?: string | null
}
```

`TemplateForm`（提交体）与模板详情类型各加 `credential_refs?: CredentialRef[]`。

- [ ] **Step 3: ticket.ts 详情类型**

工单详情类型（含 `job_host` 快照的接口）加 `credential_refs?: { alias: string; credential_id: number; credential_name: string }[]`；若 job_host 快照类型里声明了 `login_user`，删除该行。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api
git commit -m "feat(api-types): 作业主机/模板/工单凭据关联类型"
```

---

### Task 11: JobHostPanel.vue 凭据下拉

**Files:**
- Modify: `frontend/src/views/system/JobHostPanel.vue`

- [ ] **Step 1: script 区改造**

1. import 增加 `import * as jobApi from '@/api/job'`。
2. 凭据选项加载（挂载时与打开抽屉时刷新）：

```typescript
// 凭据选项：凭据管理中的凭据（登录认证随凭据走，主机侧只选不填）
const credOptions = ref<{ label: string; value: number }[]>([])
async function loadCredOptions() {
  const data = await jobApi.listCredentials({ page: 1, page_size: 100 })
  credOptions.value = data.items.map((c) => ({
    value: c.id,
    label: `${c.name}（${c.login_user} · ${c.auth_type === 'password' ? '密码' : '私钥'}）`,
  }))
}
```

3. `editForm` 中删除 `login_user/auth_type/secret/passphrase` 四键，新增 `credential_id: undefined as number | undefined`；`openCreate`/`openEdit` 同步（`openEdit` 回填 `credential_id: row.credential_id ?? undefined`），两函数内追加 `loadCredOptions()`。
4. `onSubmitEdit` 校验替换：删除密文相关三段校验，改为：

```typescript
  if (!editForm.name || !editForm.ip) {
    message.warning('请填写名称与 IP 地址')
    return
  }
  if (!editForm.credential_id) {
    message.warning('请选择关联凭据')
    return
  }
```

payload 组装删除 login_user/auth_type/secret/passphrase 键，创建/编辑均改传 `credential_id: editForm.credential_id`。
5. 列表 `columns` 中"登录账号""认证方式"两列（如有）替换为一列 `{ title: '关联凭据', key: 'credential', width: 160, ellipsis: true }`。

- [ ] **Step 2: template 区改造**

1. 表格 bodyCell 中原认证相关渲染替换为：

```vue
        <template v-if="column.key === 'credential'">
          <span v-if="record.credential_name">{{ record.credential_name }}</span>
          <a-tag v-else color="orange">未关联凭据</a-tag>
        </template>
```

2. 抽屉表单：删除"登录用户/认证方式/密码或私钥/私钥口令"四个 a-form-item，替换为：

```vue
        <a-form-item label="关联凭据" required>
          <a-select
            v-model:value="editForm.credential_id"
            :options="credOptions"
            show-search
            option-filter-prop="label"
            placeholder="选择凭据管理中的凭据"
          />
          <div class="form-tip">登录账号与密文随凭据管理维护；如需新增请先到 作业中心 → 凭据管理 创建</div>
        </a-form-item>
```

3. 删除不再使用的样式（如 `.secret-textarea`）与 import（若 `EyeInvisibleOutlined` 等仅密文控件在用）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/system/JobHostPanel.vue
git commit -m "feat(ui): 作业主机改为选择凭据管理中的凭据"
```

---

### Task 12: TemplateEditor.vue 引用凭据区域

**Files:**
- Modify: `frontend/src/views/job/TemplateEditor.vue`

- [ ] **Step 1: script 区**

1. 数据结构（`globalParams` 之后）：

```typescript
interface EditCredRef {
  alias: string
  credential_id: number | undefined
}
// 引用凭据：模板级声明，shell 步骤执行时以 CRED_<别名大写>_* 环境变量注入
const credentialRefs = ref<EditCredRef[]>([])
const credOptions = ref<{ label: string; value: number }[]>([])
async function loadCredOptions() {
  const data = await jobApi.listCredentials({ page: 1, page_size: 100 })
  credOptions.value = data.items.map((c) => ({ value: c.id, label: c.name }))
}
```

在既有初始化/详情加载逻辑处调用 `loadCredOptions()`；编辑回显时 `credentialRefs.value = (detail.credential_refs || []).map((r) => ({ alias: r.alias, credential_id: r.credential_id }))`；新建重置为 `[]`。
2. `validate()` 中追加：

```typescript
  for (const r of credentialRefs.value) {
    if (!/^[A-Za-z][A-Za-z0-9_]*$/.test(r.alias)) return `凭据别名 ${r.alias || '(空)'} 不合法：仅限字母/数字/下划线且以字母开头`
    if (!r.credential_id) return '请为每个引用凭据选择凭据'
  }
  const aliases = credentialRefs.value.map((r) => r.alias.toUpperCase())
  if (new Set(aliases).size !== aliases.length) return '凭据别名不允许重复'
```

3. `onSubmit` payload 中加：

```typescript
    credential_refs: credentialRefs.value.map((r) => ({ alias: r.alias, credential_id: r.credential_id! })),
```

- [ ] **Step 2: template 区（"全局参数定义" form-item 之后同级新增）**

```vue
            <a-form-item>
              <template #label>
                引用凭据
                <span class="label-tip">脚本中以 <code>$CRED_&lt;别名大写&gt;_USER</code> / <code>$CRED_&lt;别名大写&gt;_SECRET</code> 读取，明文不落日志与快照</span>
              </template>
              <div v-for="(r, ri) in credentialRefs" :key="ri" class="param-row">
                <a-input v-model:value="r.alias" placeholder="别名 * 如 mysql" class="param-name" />
                <a-select
                  v-model:value="r.credential_id"
                  :options="credOptions"
                  show-search
                  option-filter-prop="label"
                  placeholder="选择凭据"
                  class="param-label"
                />
                <a-button size="small" danger @click="credentialRefs.splice(ri, 1)"><DeleteOutlined /></a-button>
              </div>
              <a-button size="small" class="op-btn-green" @click="credentialRefs.push({ alias: '', credential_id: undefined })">
                <PlusOutlined />添加凭据引用
              </a-button>
            </a-form-item>
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/job/TemplateEditor.vue
git commit -m "feat(ui): 模板编辑器新增引用凭据声明区域"
```

---

### Task 13: 工单详情展示引用凭据

**Files:**
- Modify: `frontend/src/views/ticket/TicketDetailDrawer.vue`

- [ ] **Step 1: 在作业主机信息区之后展示引用凭据（仅存在时渲染）**

```vue
        <a-descriptions-item v-if="detail?.credential_refs?.length" label="引用凭据">
          <a-tag v-for="r in detail.credential_refs" :key="r.alias" color="purple">
            {{ r.alias }} → {{ r.credential_name }}
          </a-tag>
        </a-descriptions-item>
```

（按该文件实际布局挂到快照信息描述列表中；若快照信息不是 a-descriptions 结构，则以同样内容适配现有行式布局。）另外检查该文件如有展示 `job_host_snap.login_user` 的地方一并删除。

- [ ] **Step 2: 前端构建验证**

Run: `cd d:\SourceCode\qoder\frontend; npm run build; npx vue-tsc --noEmit`
Expected: 构建成功、类型检查零输出。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/ticket/TicketDetailDrawer.vue
git commit -m "feat(ui): 工单详情展示模板引用凭据"
```

---

### Task 14: docs 同步 + 部署验证 + 收尾

**Files:**
- Modify: `docs/02-技术架构设计.md`、`docs/03-数据库设计.md`、`docs/04-API设计.md`

- [ ] **Step 1: docs 同步（竣工口径）**

- 03-数据库设计：job_host 表结构更新（-认证四列 +credential_id）；ticket_template/ticket 补 credential_refs 列说明
- 04-API设计：作业主机创建/编辑请求体、模板 credential_refs 字段、工单详情响应
- 02-技术架构设计：执行链路补"凭据环境变量注入（stdin 前置 export，不落盘不进日志）"一节

- [ ] **Step 2: 全量回归**

Run: `cd d:\SourceCode\qoder\backend; python -m pytest -q`，`cd d:\SourceCode\qoder\frontend; npm run build; npx vue-tsc --noEmit`
Expected: 全部通过。

- [ ] **Step 3: 重建容器并实测**

```powershell
cd d:\SourceCode\qoder\deploy
docker compose build api worker nginx
docker compose up -d
docker logs opspilot-api --tail 30   # 确认 0009 迁移执行
docker exec opspilot-mysql mysql -uroot -popspilot-root-local opspilot -e "SHOW COLUMNS FROM job_host"
```

Expected: job_host 有 credential_id 列且无 login_user/auth_type/secret_enc/passphrase_enc；alembic_version=0009。随后页面冒烟：创建凭据 → 作业主机关联凭据 → 连通性测试 → 模板声明引用凭据（脚本 `echo "user=$CRED_MYSQL_USER"`）→ 提单执行，日志可见 env 值输出、快照/日志无密文。

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs: 凭据关联与脚本调用凭据 文档同步"
```

---

## Self-Review 结论

- 规格覆盖：设计文档 §一→Task 1/2/5，§二→Task 4/8，§三→Task 3/6/7，§四→Task 10-13，§五→Task 9/14，安全边界由 Task 8 的 stdin 注入与各响应序列化保证 ✅
- 类型一致：`credential_names`（Task 4 定义，Task 3/6 使用）、`_env_prelude`/`build_cred_env`（Task 8 定义并被其测试引用）、`credential_refs` 字段名全链路统一 ✅
- 已知联动点：`ticket_service.job_host_snap` 去掉 `login_user`（Task 7）；`test_connectivity`/`ansible_runner`/`run_shell_on_job_host` 三处 `open_connection` 调用方全部换为 Credential（Task 4/8）✅
