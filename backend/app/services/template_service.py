"""工单模板业务服务：全量规则配置 CRUD + 版本化 + 启停 + 引用保护（V2）。

版本化规则（TPL-06 / 03-数据库设计 §4.5）：
- 新建模板 = 主表 + 步骤 + 审批节点 + 版本表 v1（全量配置快照）；
- 编辑时规则（应用/步骤/策略/审批/通知/参数/权限范围）任一变更 → 插入新版本并推进
  current_version；仅改名称/说明不升版；
- 版本行永不修改，主表存续期间保留全量历史（工单快照引用版本号）。
"""
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import Errors
from app.models.auth import Role
from app.models.cmdb import JobHost
from app.models.job import Credential, TemplateApprovalNode, TemplateStep, TemplateVersion, TicketTemplate
from app.models.ticket import Ticket

# 进行中工单状态集合（引用保护判定用；原定义在 cmdb_service，执行范式改造后本地维护）
ACTIVE_TICKET_STATUSES = ("approving", "queued", "running", "paused")


async def get_template_or_404(session: AsyncSession, template_id: int) -> TicketTemplate:
    """按 ID 取模板，不存在抛 40401。"""
    tpl = await session.get(TicketTemplate, template_id)
    if tpl is None:
        raise Errors.not_found("模板不存在")
    return tpl


async def list_templates(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
    type_: str | None = None,
    status: str | None = None,
) -> tuple[list[TicketTemplate], int]:
    """分页查模板；keyword 模糊匹配名称。"""
    query = select(TicketTemplate)
    if keyword:
        query = query.where(TicketTemplate.name.like(f"%{keyword}%"))
    if type_:
        query = query.where(TicketTemplate.type == type_)
    if status:
        query = query.where(TicketTemplate.status == status)
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(
        query.order_by(TicketTemplate.id.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return list(rows.scalars()), total


async def get_template_steps(session: AsyncSession, template_id: int) -> list[TemplateStep]:
    """模板步骤列表（按序号升序）。"""
    rows = await session.execute(
        select(TemplateStep)
        .where(TemplateStep.template_id == template_id)
        .order_by(TemplateStep.step_order)
    )
    return list(rows.scalars())


async def get_template_nodes(session: AsyncSession, template_id: int) -> list[TemplateApprovalNode]:
    """模板审批节点列表（按节点序号升序）。"""
    rows = await session.execute(
        select(TemplateApprovalNode)
        .where(TemplateApprovalNode.template_id == template_id)
        .order_by(TemplateApprovalNode.node_order)
    )
    return list(rows.scalars())


async def get_version_row(
    session: AsyncSession, template_id: int, version: int
) -> TemplateVersion:
    """取模板指定版本行，不存在抛 40401。"""
    row = (
        await session.execute(
            select(TemplateVersion).where(
                TemplateVersion.template_id == template_id,
                TemplateVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise Errors.not_found(f"模板版本 v{version} 不存在")
    return row


async def list_versions(session: AsyncSession, template_id: int) -> list[TemplateVersion]:
    """版本历史列表（新版本在前）。"""
    await get_template_or_404(session, template_id)
    rows = await session.execute(
        select(TemplateVersion)
        .where(TemplateVersion.template_id == template_id)
        .order_by(TemplateVersion.version.desc())
    )
    return list(rows.scalars())


async def _ensure_name_unique(session: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    """模板名唯一性校验（工单标题直接使用模板名），冲突抛 40901。"""
    query = select(TicketTemplate.id).where(TicketTemplate.name == name)
    if exclude_id is not None:
        query = query.where(TicketTemplate.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.conflict(f"模板名 {name} 已存在")


async def _validate_refs(session: AsyncSession, data: dict) -> None:
    """引用校验：作业主机存在且启用、审批节点/可见范围角色存在（40001/40401）。"""
    jh = await session.get(JobHost, data["job_host_id"])
    if jh is None:
        raise Errors.not_found("作业主机不存在")
    if not jh.enabled:
        raise Errors.param("作业主机已禁用，不可选用")
    role_ids = {n["role_id"] for n in data["approval_nodes"]} | set(data["visible_role_ids"])
    if role_ids:
        found_roles = set(
            (await session.execute(select(Role.id).where(Role.id.in_(role_ids)))).scalars()
        )
        if role_ids - found_roles:
            raise Errors.param(f"角色不存在: {sorted(role_ids - found_roles)}")
    ref_ids = {r["credential_id"] for r in data.get("credential_refs") or []}
    if ref_ids:
        found = set(
            (await session.execute(select(Credential.id).where(Credential.id.in_(ref_ids)))).scalars()
        )
        if ref_ids - found:
            raise Errors.param(f"引用凭据不存在: {sorted(ref_ids - found)}")


def _build_snapshot(data: dict) -> dict:
    """全量配置快照（版本行 snapshot 字段；步骤含 step_order 便于回看）。"""
    return {
        "name": data["name"],
        "type": data["type"],
        "description": data["description"],
        "job_host_id": data["job_host_id"],
        "steps": [{"step_order": i + 1, **s} for i, s in enumerate(data["steps"])],
        "exec_strategy": data["exec_strategy"],
        "approval_enabled": data["approval_enabled"],
        "approval_nodes": data["approval_nodes"],
        "allow_withdraw": data["allow_withdraw"],
        "allow_transfer": data["allow_transfer"],
        "allow_countersign": data["allow_countersign"],
        "notify_rules": data["notify_rules"],
        "visible_role_ids": data["visible_role_ids"],
        "credential_refs": data.get("credential_refs") or [],
    }


# 升版判定范围：快照中除名称/说明以外的全部规则字段（TPL-06）
_RULE_KEYS = (
    "job_host_id", "steps", "exec_strategy", "approval_enabled", "approval_nodes",
    "allow_withdraw", "allow_transfer", "allow_countersign", "notify_rules", "visible_role_ids",
    "credential_refs",
)


def _rules_of(snapshot: dict) -> dict:
    """提取快照中的规则部分（升版比较用）。"""
    return {k: snapshot.get(k) for k in _RULE_KEYS}


async def _replace_children(session: AsyncSession, template_id: int, data: dict) -> None:
    """全量替换步骤与审批节点行（先删后插；工单用快照，不受影响）。"""
    for row in await get_template_steps(session, template_id):
        await session.delete(row)
    for row in await get_template_nodes(session, template_id):
        await session.delete(row)
    await session.flush()
    for i, s in enumerate(data["steps"]):
        session.add(
            TemplateStep(
                template_id=template_id,
                step_order=i + 1,
                name=s["name"],
                script_type=s["script_type"],
                content=s["content"],
                params_schema=s["params_schema"],
                timeout=s["timeout"],
            )
        )
    for n in data["approval_nodes"]:
        session.add(
            TemplateApprovalNode(
                template_id=template_id,
                node_order=n["node_order"],
                role_id=n["role_id"],
                approve_mode=n["approve_mode"],
            )
        )
    await session.flush()


def _apply_basic(tpl: TicketTemplate, data: dict) -> None:
    """主表字段赋值（不含 status/current_version）。"""
    tpl.name = data["name"]
    tpl.type = data["type"]
    tpl.description = data["description"]
    tpl.job_host_id = data["job_host_id"]
    tpl.exec_strategy = data["exec_strategy"]
    tpl.approval_enabled = data["approval_enabled"]
    tpl.allow_withdraw = data["allow_withdraw"]
    tpl.allow_transfer = data["allow_transfer"]
    tpl.allow_countersign = data["allow_countersign"]
    tpl.notify_rules = data["notify_rules"]
    tpl.visible_role_ids = data["visible_role_ids"]
    tpl.credential_refs = data.get("credential_refs") or []


async def create_template(
    session: AsyncSession, *, created_by: int, data: dict
) -> TicketTemplate:
    """新建模板：主表 + 步骤 + 审批节点 + v1 版本行一并落库。"""
    await _ensure_name_unique(session, data["name"])
    await _validate_refs(session, data)
    tpl = TicketTemplate(current_version=1, status="enabled", created_by=created_by)
    _apply_basic(tpl, data)
    session.add(tpl)
    await session.flush()
    await _replace_children(session, tpl.id, data)
    session.add(
        TemplateVersion(
            template_id=tpl.id,
            version=1,
            snapshot=_build_snapshot(data),
            changelog=data.get("changelog") or "初始版本",
            created_by=created_by,
        )
    )
    await session.flush()
    return tpl


async def update_template(
    session: AsyncSession, template_id: int, *, updated_by: int, data: dict
) -> tuple[TicketTemplate, bool]:
    """编辑模板；规则任一变更自动升版。返回 (模板, 是否升版)。"""
    tpl = await get_template_or_404(session, template_id)
    if data["name"] != tpl.name:
        await _ensure_name_unique(session, data["name"], exclude_id=template_id)
    await _validate_refs(session, data)
    current = await get_version_row(session, template_id, tpl.current_version)
    snapshot = _build_snapshot(data)
    changed = _rules_of(snapshot) != _rules_of(current.snapshot or {})
    _apply_basic(tpl, data)
    await _replace_children(session, template_id, data)
    if changed:
        tpl.current_version += 1
        session.add(
            TemplateVersion(
                template_id=template_id,
                version=tpl.current_version,
                snapshot=snapshot,
                changelog=data.get("changelog"),
                created_by=updated_by,
            )
        )
    await session.flush()
    return tpl, changed


async def set_status(session: AsyncSession, template_id: int, *, status: str) -> TicketTemplate:
    """启用/禁用：禁用后不可被提交，不影响已提交工单（TPL-03）；不升版。"""
    tpl = await get_template_or_404(session, template_id)
    tpl.status = status
    await session.flush()
    return tpl


async def delete_template(session: AsyncSession, template_id: int) -> TicketTemplate:
    """删除模板；被进行中工单引用时拒绝（42201），子表随主表一并清理。

    已完结工单不阻止删除：工单五重快照自包含，不依赖模板存活。
    """
    tpl = await get_template_or_404(session, template_id)
    ticket_nos = (
        await session.execute(
            select(Ticket.ticket_no).where(
                Ticket.template_id == template_id,
                Ticket.status.in_(ACTIVE_TICKET_STATUSES),
            )
        )
    ).scalars().all()
    if ticket_nos:
        raise Errors.rejected(f"模板被进行中工单引用，无法删除：{'、'.join(ticket_nos[:5])}")
    for row in await get_template_steps(session, template_id):
        await session.delete(row)
    for row in await get_template_nodes(session, template_id):
        await session.delete(row)
    for row in (
        await session.execute(
            select(TemplateVersion).where(TemplateVersion.template_id == template_id)
        )
    ).scalars():
        await session.delete(row)
    await session.delete(tpl)
    await session.flush()
    return tpl
