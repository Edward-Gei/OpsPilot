"""系统配置路由（冲突决策⑥）：M1 仅提供接口，设置 UI 后续里程碑补齐。

用途：切换 mfa.policy / password.policy 等走 OpenAPI 文档页或脚本调用。
"""
from fastapi import APIRouter, Depends, Request

from app import audit
from app.core.deps import DbSession, get_client_ip, require_perm
from app.core.response import ok
from app.engine.ansible_runner import JobHostError, test_job_host
from app.models.auth import User
from app.models.job import Credential
from app.schemas.rbac import ConfigUpdateRequest, JobHostTestRequest
from app.services import config_service

router = APIRouter(prefix="/system", tags=["系统配置"])

# 敏感配置键：读取时脱敏（凭据不回显）
_SENSITIVE_KEYS = {"ldap.config", "oidc.config"}
_SENSITIVE_FIELDS = {"bind_password", "client_secret"}


def _mask(key: str, value):
    """敏感字段脱敏：LDAP/OIDC 配置中的密钥以 ****** 回显。"""
    if key in _SENSITIVE_KEYS and isinstance(value, dict):
        return {k: ("******" if k in _SENSITIVE_FIELDS and v else v) for k, v in value.items()}
    return value


async def _unmask(session: DbSession, key: str, value):
    """脱敏回写保护：前端原样提交 ****** 时保留库中真实密钥，避免误覆盖。"""
    if key in _SENSITIVE_KEYS and isinstance(value, dict):
        if any(value.get(f) == "******" for f in _SENSITIVE_FIELDS):
            current = await config_service.get_config(session, key) or {}
            value = {
                k: (current.get(k) if k in _SENSITIVE_FIELDS and v == "******" else v)
                for k, v in value.items()
            }
    return value


@router.get("/configs", summary="读取系统配置")
async def get_configs(
    session: DbSession,
    _: User = Depends(require_perm("system:config")),
) -> dict:
    """全部配置键值（敏感字段脱敏）。"""
    configs = await config_service.get_all_configs(session)
    return ok({k: _mask(k, v) for k, v in configs.items()})


@router.put("/configs", summary="更新系统配置")
async def update_configs(
    req: ConfigUpdateRequest,
    request: Request,
    session: DbSession,
    actor: User = Depends(require_perm("system:config")),
) -> dict:
    """批量更新配置键；仅允许预置键，逐键写库并审计。"""
    for key, value in req.configs.items():
        value = await _unmask(session, key, value)
        await config_service.set_config(session, key, value, actor.id)
    audit.log(module="system", action="config.update", actor_id=actor.id,
              actor_name=actor.username, source_ip=get_client_ip(request),
              detail={"keys": sorted(req.configs.keys())})
    return ok()


@router.post("/ansible-job-host/test", summary="作业主机连通性测试")
async def ansible_job_host_test(
    req: JobHostTestRequest,
    session: DbSession,
    _: User = Depends(require_perm("system:config")),
) -> dict:
    """SSH 建连 + `ansible --version` 探测（04-API §11）；同步返回成败与原因。

    接受当前表单值（非已保存配置），支持保存前预测；失败不抛异常，
    前端按 success 字段展示结果。"""
    credential = await session.get(Credential, req.credential_id)
    if credential is None:
        return ok({"success": False, "message": "凭据不存在或已删除"})
    try:
        version = await test_job_host({"ip": req.ip, "port": req.port}, credential)
    except JobHostError as exc:
        return ok({"success": False, "message": str(exc)})
    return ok({"success": True, "message": version})
