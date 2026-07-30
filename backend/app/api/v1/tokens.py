"""个人访问密钥路由（04-API设计 §2.9）：自助创建 / 列表 / 删除。

安全约束：管理接口仅限登录态（JWT）操作——API Token 本身无权管理密钥，
防止密钥泄露后被用于创建新密钥（自我繁殖）。
"""
from datetime import datetime

from fastapi import APIRouter, Request

from app import audit
from app.core.deps import CurrentUser, DbSession, get_client_ip
from app.core.response import BizError, Errors, ok
from app.models.auth import ApiToken
from app.schemas.auth import TokenCreateRequest
from app.services import token_service

router = APIRouter(prefix="/user/tokens", tags=["访问密钥"])


def _ensure_session_auth(request: Request) -> None:
    """拒绝 API Token 通道：密钥管理必须使用登录态凭证。"""
    if getattr(request.state, "api_token_auth", False):
        raise BizError(Errors.NO_PERM, "访问密钥无权管理密钥，请登录后操作", 403)


def _brief(t: ApiToken) -> dict:
    """密钥列表序列化（永不含哈希/明文）。"""
    return {
        "id": t.id,
        "name": t.name,
        "prefix": t.prefix,
        "expired": bool(t.expires_at and t.expires_at <= datetime.now()),
        "expires_at": t.expires_at.isoformat() if t.expires_at else None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.get("", summary="我的访问密钥列表")
async def list_tokens(request: Request, user: CurrentUser, session: DbSession) -> dict:
    """仅返回本人密钥（新建在前）。"""
    _ensure_session_auth(request)
    items = await token_service.list_tokens(session, user.id)
    return ok({"items": [_brief(t) for t in items], "limit": token_service.MAX_TOKENS_PER_USER})


@router.post("", summary="创建访问密钥")
async def create_token(
    req: TokenCreateRequest, request: Request, user: CurrentUser, session: DbSession
) -> dict:
    """创建成功返回明文 token（仅此一次），列表接口不再可见。"""
    _ensure_session_auth(request)
    record, plaintext = await token_service.create_token(
        session, user, name=req.name, expires_in_days=req.expires_in_days
    )
    audit.log(module="user", action="token.create", actor_id=user.id, actor_name=user.username,
              source_ip=get_client_ip(request), target_type="api_token",
              target_id=str(record.id), target_name=record.name,
              detail={"expires_in_days": req.expires_in_days})
    return ok({**_brief(record), "token": plaintext})


@router.delete("/{token_id}", summary="删除访问密钥")
async def delete_token(
    token_id: int, request: Request, user: CurrentUser, session: DbSession
) -> dict:
    """删除本人密钥，立即失效。"""
    _ensure_session_auth(request)
    record = await token_service.delete_token(session, user.id, token_id)
    audit.log(module="user", action="token.delete", actor_id=user.id, actor_name=user.username,
              source_ip=get_client_ip(request), target_type="api_token",
              target_id=str(token_id), target_name=record.name)
    return ok()
