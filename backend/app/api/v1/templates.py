"""工单模板 API；页面维护业务入口、参数契约和一个流程引用。"""

from fastapi import APIRouter, Depends, Query, Request

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import BizError, Errors, ok
from app.models.auth import User
from app.schemas.ticket_template import TicketTemplateStatusRequest, TicketTemplateUpsertRequest
from app.services import rbac_service, template_service

router = APIRouter(prefix="/templates", tags=["工单模板"])


def _brief(t, credential_refs: list[dict] | None = None) -> dict:
    data = {
        "id": t.id, "name": t.name, "type": t.type, "description": t.description,
        "job_host_id": t.job_host_id, "process_template_id": t.process_template_id,
        "params_schema": t.params_schema or [], "generator_script": t.generator_script,
        "generator_timeout": t.generator_timeout,
        "status": t.status, "allow_withdraw": t.allow_withdraw,
        "concurrency_control_enabled": t.concurrency_control_enabled,
        "notify_rules": t.notify_rules or [], "visible_role_ids": t.visible_role_ids or [],
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }
    if credential_refs is not None:
        data["credential_refs"] = credential_refs
    return data


async def _can_view_secret_refs(session: DbSession, actor: User) -> bool:
    return "secret:read" in await rbac_service.get_user_perms(session, actor.id)


@router.get("")
async def list_templates(session: DbSession, actor: User = Depends(require_perm("template:read")),
                         page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                         keyword: str | None = None, type: str | None = None, status: str | None = None,
                         process_template_id: int | None = Query(None, ge=1)):
    rows, total = await template_service.list_templates(
        session, page=page, page_size=page_size, keyword=keyword, type_=type, status=status,
        process_template_id=process_template_id,
    )
    can_view_refs = await _can_view_secret_refs(session, actor)
    items = [
        _brief(t, await template_service.credential_ref_metadata(session, t.credential_refs or []))
        if can_view_refs else _brief(t)
        for t in rows
    ]
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.post("")
async def create_template(req: TicketTemplateUpsertRequest, request: Request, session: DbSession,
                          actor: User = Depends(require_perm("template:write"))):
    if req.credential_refs and not await _can_view_secret_refs(session, actor):
        raise BizError(Errors.NO_PERM, "缺少权限: secret:read", 403)
    tpl = await template_service.create_template(session, created_by=actor.id, data=req.model_dump())
    audit.log(module="job", action="template.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="template", target_id=str(tpl.id), target_name=tpl.name)
    return ok({"id": tpl.id})


@router.get("/{template_id}")
async def get_template(template_id: int, session: DbSession, actor: User = Depends(require_perm("template:read"))):
    tpl = await template_service.get_template_or_404(session, template_id)
    process = await template_service.get_process_or_404(session, tpl.process_template_id)
    data = _brief(
        tpl,
        await template_service.credential_ref_metadata(session, tpl.credential_refs or [])
        if await _can_view_secret_refs(session, actor) else None,
    )
    data["process_template"] = {"id": process.id, "name": process.name, "status": process.status}
    return ok(data)


@router.put("/{template_id}")
async def update_template(template_id: int, req: TicketTemplateUpsertRequest, request: Request,
                          session: DbSession, actor: User = Depends(require_perm("template:write"))):
    existing = await template_service.get_template_or_404(session, template_id)
    if (req.credential_refs or existing.credential_refs) and not await _can_view_secret_refs(session, actor):
        raise BizError(Errors.NO_PERM, "缺少权限: secret:read", 403)
    tpl = await template_service.update_template(session, template_id, data=req.model_dump())
    audit.log(module="job", action="template.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="template", target_id=str(tpl.id), target_name=tpl.name)
    return ok({"id": tpl.id})


@router.put("/{template_id}/status")
async def set_template_status(template_id: int, req: TicketTemplateStatusRequest, request: Request,
                              session: DbSession, actor: User = Depends(require_perm("template:write"))):
    tpl = await template_service.set_status(session, template_id, status=req.status)
    audit.log(module="job", action=f"template.{req.status}", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="template", target_id=str(tpl.id), target_name=tpl.name)
    return ok({"status": tpl.status})


@router.delete("/{template_id}")
async def delete_template(template_id: int, request: Request, session: DbSession,
                          actor: User = Depends(require_perm("template:delete"))):
    tpl = await template_service.delete_template(session, template_id)
    audit.log(module="job", action="template.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="template", target_id=str(template_id), target_name=tpl.name)
    return ok({"id": template_id})
