"""工单模板路由（04-API设计 §5，V2）：template:read / template:write 权限点控制。

模板管理负责工单全部规则配置：基本信息/作业主机/步骤编排（内嵌脚本）/执行策略/
审批规则/通知规则/权限范围/版本。规则任一变更自动升版（TPL-06）。
"""
from fastapi import APIRouter, Depends, Query, Request

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.models.auth import User
from app.schemas.job import TemplateStatusRequest, TemplateUpsertRequest
from app.services import job_host_service, template_service

router = APIRouter(prefix="/templates", tags=["工单模板"])


def _tpl_brief(t) -> dict:
    """模板主表统一序列化（列表用，不含步骤/节点明细）。"""
    return {
        "id": t.id,
        "name": t.name,
        "type": t.type,
        "description": t.description,
        "job_host_id": t.job_host_id,
        "approval_enabled": t.approval_enabled,
        "status": t.status,
        "current_version": t.current_version,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _step_row(s) -> dict:
    """模板步骤序列化。"""
    return {
        "step_order": s.step_order,
        "name": s.name,
        "script_type": s.script_type,
        "content": s.content,
        "params_schema": s.params_schema or [],
        "timeout": s.timeout,
    }


def _node_row(n) -> dict:
    """审批节点序列化。"""
    return {"node_order": n.node_order, "role_id": n.role_id, "approve_mode": n.approve_mode}


def _version_brief(v, with_snapshot: bool = False) -> dict:
    """版本行序列化；with_snapshot=True 时附全量配置快照。"""
    data = {
        "id": v.id,
        "version": v.version,
        "changelog": v.changelog,
        "created_by": v.created_by,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
    if with_snapshot:
        data["snapshot"] = v.snapshot or {}
    return data


@router.get("", summary="模板列表")
async def list_templates(
    session: DbSession,
    _: User = Depends(require_perm("template:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    type: str | None = Query(None, description="release | change | ops | other"),  # noqa: A002
    status: str | None = Query(None, description="enabled | disabled"),
) -> dict:
    """分页查模板（keyword 模糊匹配名称）。"""
    tpls, total = await template_service.list_templates(
        session, page=page, page_size=page_size, keyword=keyword, type_=type, status=status
    )
    return ok({"items": [_tpl_brief(t) for t in tpls], "total": total,
               "page": page, "page_size": page_size})


@router.post("", summary="新建模板")
async def create_template(
    req: TemplateUpsertRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("template:write")),
) -> dict:
    """新建模板（全量配置）并生成 v1 版本；名称冲突 40901。"""
    tpl = await template_service.create_template(
        session, created_by=actor.id, data=req.model_dump()
    )
    audit.log(module="job", action="template.create", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="template", target_id=str(tpl.id), target_name=tpl.name,
              detail={"type": tpl.type, "version": 1, "steps": len(req.steps),
                      "approval_enabled": req.approval_enabled})
    return ok({"id": tpl.id})


@router.get("/{template_id}", summary="模板详情（当前版本全量配置）")
async def get_template(
    template_id: int,
    session: DbSession,
    _: User = Depends(require_perm("template:read")),
) -> dict:
    """详情 = 主表全部规则 + 步骤 + 审批节点（当前生效配置）。"""
    tpl = await template_service.get_template_or_404(session, template_id)
    steps = await template_service.get_template_steps(session, template_id)
    nodes = await template_service.get_template_nodes(session, template_id)
    data = _tpl_brief(tpl)
    cred_names = await job_host_service.credential_names(
        session, [r["credential_id"] for r in (tpl.credential_refs or [])]
    )
    data.update({
        "exec_strategy": tpl.exec_strategy or {},
        "allow_withdraw": tpl.allow_withdraw,
        "allow_transfer": tpl.allow_transfer,
        "allow_countersign": tpl.allow_countersign,
        "notify_rules": tpl.notify_rules or [],
        "visible_role_ids": tpl.visible_role_ids or [],
        "credential_refs": [
            {**r, "credential_name": cred_names.get(r["credential_id"])}
            for r in (tpl.credential_refs or [])
        ],
        "steps": [_step_row(s) for s in steps],
        "approval_nodes": [_node_row(n) for n in nodes],
    })
    return ok(data)


@router.put("/{template_id}", summary="编辑模板")
async def update_template(
    template_id: int,
    req: TemplateUpsertRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("template:write")),
) -> dict:
    """全量更新；规则任一变更自动升版，仅改名/说明不升版（TPL-06）。"""
    tpl, bumped = await template_service.update_template(
        session, template_id, updated_by=actor.id, data=req.model_dump()
    )
    audit.log(module="job", action="template.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="template", target_id=str(tpl.id), target_name=tpl.name,
              detail={"type": tpl.type, "version_bumped": bumped,
                      "current_version": tpl.current_version})
    return ok({"current_version": tpl.current_version, "version_bumped": bumped})


@router.put("/{template_id}/status", summary="启用/禁用模板")
async def set_template_status(
    template_id: int,
    req: TemplateStatusRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("template:write")),
) -> dict:
    """禁用后不可被提交工单，不影响已提交工单（TPL-03）。"""
    tpl = await template_service.set_status(session, template_id, status=req.status)
    audit.log(module="job", action=f"template.{req.status}", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="template", target_id=str(tpl.id), target_name=tpl.name)
    return ok({"status": tpl.status})


@router.delete("/{template_id}", summary="删除模板")
async def delete_template(
    template_id: int,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("template:write")),
) -> dict:
    """删除模板（含步骤/节点/全部版本行）；被进行中工单引用时 42201。"""
    tpl = await template_service.delete_template(session, template_id)
    audit.log(module="job", action="template.delete", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              target_type="template", target_id=str(template_id), target_name=tpl.name)
    return ok()


@router.get("/{template_id}/versions", summary="版本历史")
async def list_versions(
    template_id: int,
    session: DbSession,
    _: User = Depends(require_perm("template:read")),
) -> dict:
    """版本列表（新版本在前，不含快照体）。"""
    versions = await template_service.list_versions(session, template_id)
    return ok({"items": [_version_brief(v) for v in versions]})


@router.get("/{template_id}/versions/{version}", summary="历史版本快照")
async def get_version(
    template_id: int,
    version: int,
    session: DbSession,
    _: User = Depends(require_perm("template:read")),
) -> dict:
    """查看指定历史版本的全量配置快照（只读）。"""
    row = await template_service.get_version_row(session, template_id, version)
    return ok(_version_brief(row, with_snapshot=True))
