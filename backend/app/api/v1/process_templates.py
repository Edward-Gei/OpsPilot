"""可复用流程模板 API：步骤、参数、动态脚本和步骤前审批统一维护。"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.models.auth import User
from app.models.job import TicketTemplate
from app.schemas.process_template import ProcessTemplateStatusRequest, ProcessTemplateUpsertRequest
from app.services import template_service

router = APIRouter(prefix="/process-templates", tags=["流程模板"])


def _brief(p, steps_count: int | None = None, refs: int | None = None) -> dict:
    return {
        "id": p.id, "name": p.name, "description": p.description, "status": p.status,
        "params_schema": p.params_schema or [], "generator_script": p.generator_script,
        "generator_timeout": p.generator_timeout, "exec_strategy": p.exec_strategy or {},
        "steps_count": steps_count, "ticket_template_refs": refs,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("")
async def list_process_templates(session: DbSession, _: User = Depends(require_perm("template:read")),
                                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                                 keyword: str | None = None, status: str | None = None):
    rows, total = await template_service.list_process_templates(
        session, page=page, page_size=page_size, keyword=keyword, status=status
    )
    items = []
    for p in rows:
        steps = await template_service.get_process_steps(session, p.id)
        refs = (await session.execute(select(func.count()).select_from(TicketTemplate).where(TicketTemplate.process_template_id == p.id))).scalar_one()
        items.append(_brief(p, len(steps), refs))
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.post("")
async def create_process_template(req: ProcessTemplateUpsertRequest, request: Request, session: DbSession,
                                  actor: User = Depends(require_perm("template:write"))):
    p = await template_service.create_process_template(session, created_by=actor.id, data=req.model_dump())
    audit.log(module="job", action="process_template.create", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="process_template", target_id=str(p.id), target_name=p.name)
    return ok({"id": p.id})


@router.get("/{process_id}")
async def get_process_template(process_id: int, session: DbSession, _: User = Depends(require_perm("template:read"))):
    p = await template_service.get_process_or_404(session, process_id)
    steps = await template_service.get_process_steps(session, process_id)
    return ok({**_brief(p, len(steps)), "steps": [
        {"step_order": s.step_order, "name": s.name, "script_type": s.script_type,
         "content": s.content, "timeout": s.timeout, "approval_role_id": s.approval_role_id}
        for s in steps
    ]})


@router.put("/{process_id}")
async def update_process_template(process_id: int, req: ProcessTemplateUpsertRequest, request: Request,
                                  session: DbSession, actor: User = Depends(require_perm("template:write"))):
    p = await template_service.update_process_template(session, process_id, data=req.model_dump())
    audit.log(module="job", action="process_template.update", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="process_template", target_id=str(p.id), target_name=p.name)
    return ok({"id": p.id})


@router.put("/{process_id}/status")
async def set_process_template_status(process_id: int, req: ProcessTemplateStatusRequest, request: Request,
                                      session: DbSession, actor: User = Depends(require_perm("template:write"))):
    p = await template_service.set_process_status(session, process_id, req.status)
    audit.log(module="job", action=f"process_template.{req.status}", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="process_template", target_id=str(p.id), target_name=p.name)
    return ok({"status": p.status})


@router.delete("/{process_id}")
async def delete_process_template(process_id: int, request: Request, session: DbSession,
                                  actor: User = Depends(require_perm("template:delete"))):
    p = await template_service.delete_process_template(session, process_id)
    audit.log(module="job", action="process_template.delete", actor_id=actor.id, actor_name=actor.username,
              source_ip=get_client_ip(request), target_type="process_template", target_id=str(process_id), target_name=p.name)
    return ok({"id": process_id})
