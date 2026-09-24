"""应用配置的本地元数据、草稿基线与引用校验服务。"""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_providers import get_config_adapter
from app.config_providers.base import PlatformConnection
from app.core.constants import (
    ConfigFileStatus,
    ConfigProvider,
    ConfigTaskStatus,
    ConfigVersionStatus,
    CredentialAuthType,
)
from app.core.response import Errors
from app.core.security import decrypt_text, encrypt_text
from app.models.application_config import (
    ConfigDraft,
    ConfigFile,
    ConfigFileApplication,
    ConfigPlatformInstance,
    ConfigRemoteSnapshot,
    ConfigTask,
    ConfigVersion,
)
from app.models.auth import Role, User, UserRole
from app.models.cmdb import Application
from app.models.job import Credential
from app.services.config_content_service import (
    merge_redacted_update,
    redact_content,
    validate_and_normalize_content,
)
from app.services.credential_service import get_credential_or_404


_COMPATIBLE_AUTH_TYPES = {
    ConfigProvider.APOLLO.value: {CredentialAuthType.API_TOKEN.value},
    ConfigProvider.NACOS.value: {CredentialAuthType.API_TOKEN.value, CredentialAuthType.USERNAME_PASSWORD.value},
    ConfigProvider.CONSUL.value: {CredentialAuthType.API_TOKEN.value},
}


@dataclass(frozen=True)
class ConfigActor:
    id: int
    name: str
    source_ip: str | None = None


async def create_platform_instance(
    session: AsyncSession,
    *,
    actor_id: int,
    name: str,
    provider: ConfigProvider | str,
    base_url: str,
    credential_id: int,
    description: str | None = None,
    compatibility_version: str | None = None,
) -> ConfigPlatformInstance:
    """创建可复用连接边界，并验证凭据认证类型。"""
    provider_value = _provider_value(provider)
    await _ensure_instance_name_unique(session, name)
    await _ensure_compatible_credential(session, provider_value, credential_id)
    instance = ConfigPlatformInstance(
        name=name.strip(),
        provider=provider_value,
        base_url=_normalize_base_url(base_url),
        credential_id=credential_id,
        description=description,
        compatibility_version=compatibility_version,
        created_by=actor_id,
    )
    session.add(instance)
    await session.flush()
    return instance


async def list_platform_instances(session: AsyncSession) -> list[ConfigPlatformInstance]:
    rows = await session.execute(select(ConfigPlatformInstance).order_by(ConfigPlatformInstance.id.desc()))
    return list(rows.scalars())


async def get_platform_instance(session: AsyncSession, instance_id: int) -> ConfigPlatformInstance:
    instance = await session.get(ConfigPlatformInstance, instance_id)
    if instance is None:
        raise Errors.not_found("配置平台实例不存在")
    return instance


async def update_platform_instance(
    session: AsyncSession,
    instance_id: int,
    **fields,
) -> ConfigPlatformInstance:
    """首次绑定配置文件后仅允许维护凭据、描述、启停和兼容记录。"""
    instance = await get_platform_instance(session, instance_id)
    provider = fields.get("provider")
    base_url = fields.get("base_url")
    if instance.first_bound_at is not None:
        if provider is not None and _provider_value(provider) != instance.provider:
            raise Errors.rejected("已有配置文件绑定后不能修改平台类型")
        if base_url is not None and _normalize_base_url(str(base_url)) != instance.base_url:
            raise Errors.rejected("已有配置文件绑定后不能修改平台地址")
    if fields.get("name") is not None and fields["name"].strip() != instance.name:
        await _ensure_instance_name_unique(session, fields["name"], exclude_id=instance.id)
        instance.name = fields["name"].strip()
    next_provider = _provider_value(provider) if provider is not None else instance.provider
    if fields.get("credential_id") is not None:
        await _ensure_compatible_credential(session, next_provider, fields["credential_id"])
        instance.credential_id = fields["credential_id"]
    if provider is not None and instance.first_bound_at is None:
        instance.provider = next_provider
    if base_url is not None and instance.first_bound_at is None:
        instance.base_url = _normalize_base_url(str(base_url))
    for field in ("description", "enabled", "compatibility_version"):
        if field in fields and fields[field] is not None:
            setattr(instance, field, fields[field])
    await session.flush()
    return instance


async def delete_platform_instance(session: AsyncSession, instance_id: int) -> ConfigPlatformInstance:
    instance = await get_platform_instance(session, instance_id)
    if instance.enabled:
        raise Errors.rejected("请先停用配置平台实例")
    count = (await session.execute(
        select(func.count()).select_from(ConfigFile).where(ConfigFile.platform_instance_id == instance_id)
    )).scalar_one()
    if count:
        raise Errors.rejected("配置平台实例仍被配置文件引用")
    await session.delete(instance)
    await session.flush()
    return instance


async def probe_platform_instance(session: AsyncSession, instance_id: int) -> ConfigPlatformInstance:
    """执行一次显式连通性测试，仅记录无敏感的结果码。"""
    instance = await get_platform_instance(session, instance_id)
    try:
        probe = await get_config_adapter(instance.provider).probe(
            await resolve_platform_connection(session, instance)
        )
    except Exception as exc:  # 适配器异常已脱敏，仅保留类型或受控码
        instance.last_probed_at = datetime.now()
        instance.last_probe_result = "failed"
        instance.last_probe_error = getattr(exc, "code", "unavailable")[:512]
        await session.flush()
        raise Errors.upstream("配置平台连通性测试失败") from None
    instance.last_probed_at = datetime.now()
    instance.last_probe_result = "success" if probe.reachable else "failed"
    instance.last_probe_error = None if probe.reachable else "unavailable"
    await session.flush()
    return instance


async def create_config_file(
    session: AsyncSession,
    *,
    platform_instance_id: int,
    name: str,
    description: str | None,
    locator: dict[str, str],
    content_format: str,
    approval_role_id: int,
    application_ids: list[int],
    initial_content: str,
    actor_id: int,
) -> ConfigFile:
    """手工登记缺失远端目标，创建本地缺失快照和唯一草稿，不进行远端 I/O。"""
    instance = await get_platform_instance(session, platform_instance_id)
    if not instance.enabled:
        raise Errors.rejected("配置平台实例已停用")
    normalized_locator, locator_key = await normalize_locator(session, instance, locator)
    await _validate_content_format(instance.provider, content_format)
    await _ensure_approval_role(session, approval_role_id)
    app_ids = await _validate_application_ids(session, application_ids)
    normalized_content = validate_and_normalize_content(content_format, initial_content)
    exists = (await session.execute(
        select(ConfigFile.id).where(
            ConfigFile.platform_instance_id == instance.id,
            ConfigFile.locator_key == locator_key,
        )
    )).scalar_one_or_none()
    if exists is not None:
        raise Errors.conflict("该远端定位器已被管理")
    config_file = ConfigFile(
        name=name.strip(),
        description=description,
        platform_instance_id=instance.id,
        locator=normalized_locator,
        locator_key=locator_key,
        content_format=content_format,
        approval_role_id=approval_role_id,
        created_by=actor_id,
    )
    session.add(config_file)
    try:
        await session.flush()
    except IntegrityError:
        raise Errors.conflict("该远端定位器已被管理") from None
    snapshot = ConfigRemoteSnapshot(
        config_file_id=config_file.id,
        exists=False,
        observed_at=datetime.now(),
    )
    session.add(snapshot)
    await session.flush()
    config_file.current_snapshot_id = snapshot.id
    config_file.latest_snapshot_id = snapshot.id
    session.add(ConfigDraft(
        config_file_id=config_file.id,
        content_enc=encrypt_text(normalized_content.canonical),
        base_snapshot_id=snapshot.id,
        updated_by=actor_id,
    ))
    for app_id in app_ids:
        session.add(ConfigFileApplication(config_file_id=config_file.id, application_id=app_id))
    if instance.first_bound_at is None:
        instance.first_bound_at = datetime.now()
    await session.flush()
    return config_file


async def list_config_files(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
) -> tuple[list[ConfigFile], int]:
    query = select(ConfigFile)
    if keyword:
        query = query.where(ConfigFile.name.like(f"%{keyword}%"))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = await session.execute(query.order_by(ConfigFile.id.desc()).offset((page - 1) * page_size).limit(page_size))
    return list(rows.scalars()), total


async def get_config_file(session: AsyncSession, file_id: int) -> ConfigFile:
    config_file = await session.get(ConfigFile, file_id)
    if config_file is None:
        raise Errors.not_found("配置文件不存在")
    return config_file


async def update_config_file_metadata(
    session: AsyncSession,
    file_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    approval_role_id: int | None = None,
    application_ids: list[int] | None = None,
) -> ConfigFile:
    config_file = await get_config_file(session, file_id)
    if name is not None:
        config_file.name = name.strip()
    if description is not None:
        config_file.description = description
    if approval_role_id is not None and approval_role_id != config_file.approval_role_id:
        await _ensure_approval_role(session, approval_role_id)
        config_file.approval_role_id = approval_role_id
        await session.execute(
            ConfigVersion.__table__.update().where(
                ConfigVersion.config_file_id == file_id,
                ConfigVersion.status.in_([
                    ConfigVersionStatus.PENDING_APPROVAL.value,
                    ConfigVersionStatus.APPROVED.value,
                ]),
            ).values(status=ConfigVersionStatus.INVALIDATED.value)
        )
    if application_ids is not None:
        app_ids = await _validate_application_ids(session, application_ids)
        await session.execute(delete(ConfigFileApplication).where(ConfigFileApplication.config_file_id == file_id))
        session.add_all([ConfigFileApplication(config_file_id=file_id, application_id=app_id) for app_id in app_ids])
    await session.flush()
    return config_file


async def archive_config_file(session: AsyncSession, file_id: int) -> ConfigFile:
    config_file = await get_config_file(session, file_id)
    active_task = (await session.execute(select(ConfigTask.id).where(
        ConfigTask.config_file_id == file_id,
        ConfigTask.status.in_([ConfigTaskStatus.QUEUED.value, ConfigTaskStatus.RUNNING.value]),
    ).limit(1))).scalar_one_or_none()
    if active_task is not None or (config_file.operation_token and config_file.operation_expires_at
                                   and config_file.operation_expires_at > datetime.now()):
        raise Errors.conflict("配置文件正在同步或发布，不能归档")
    config_file.status = ConfigFileStatus.ARCHIVED.value
    await session.flush()
    return config_file


async def delete_archived_config_file(session: AsyncSession, file_id: int) -> ConfigFile:
    config_file = await get_config_file(session, file_id)
    if config_file.status != ConfigFileStatus.ARCHIVED.value:
        raise Errors.rejected("配置文件必须先归档才能删除")
    await session.execute(delete(ConfigFileApplication).where(ConfigFileApplication.config_file_id == file_id))
    await session.execute(delete(ConfigDraft).where(ConfigDraft.config_file_id == file_id))
    await session.execute(delete(ConfigVersion).where(ConfigVersion.config_file_id == file_id))
    await session.execute(delete(ConfigRemoteSnapshot).where(ConfigRemoteSnapshot.config_file_id == file_id))
    await session.delete(config_file)
    await session.flush()
    return config_file


async def resolve_platform_connection(
    session: AsyncSession,
    instance: ConfigPlatformInstance,
) -> PlatformConnection:
    """仅远端调用前解密凭据，调用者不得把返回对象写入持久化或日志。"""
    credential = await get_credential_or_404(session, instance.credential_id)
    await _ensure_compatible_credential(session, instance.provider, credential.id)
    try:
        secret = decrypt_text(credential.secret_enc)
    except Exception:
        raise Errors.rejected("配置平台凭据无法解密") from None
    return PlatformConnection(
        provider=ConfigProvider(instance.provider),
        base_url=instance.base_url,
        credential_id=credential.id,
        auth_type=credential.auth_type,
        secret=secret,
        username=credential.login_user,
    )


async def normalize_locator(
    session: AsyncSession,
    instance: ConfigPlatformInstance,
    locator: dict[str, str],
) -> tuple[dict[str, str], str]:
    """按平台校验精确定位器，Consul 额外拒绝同数据中心的前缀重叠。"""
    provider = instance.provider
    keys = {
        ConfigProvider.APOLLO.value: ("app_id", "cluster", "namespace"),
        ConfigProvider.NACOS.value: ("namespace", "group", "data_id"),
        ConfigProvider.CONSUL.value: ("datacenter", "kv_prefix"),
    }.get(provider)
    if keys is None:
        raise Errors.param("不支持的配置平台")
    normalized = {key: str(locator.get(key, "")).strip() for key in keys}
    if (provider == ConfigProvider.NACOS.value and "namespace" not in locator) or any(
        not value for key, value in normalized.items()
        if provider != ConfigProvider.NACOS.value or key != "namespace"
    ):
        raise Errors.param("远端定位器字段不完整")
    if provider == ConfigProvider.CONSUL.value:
        normalized["kv_prefix"] = normalized["kv_prefix"].strip("/")
        if not normalized["kv_prefix"]:
            raise Errors.param("Consul KV 前缀不能为空")
        existing = (await session.execute(
            select(ConfigFile.locator).where(ConfigFile.platform_instance_id == instance.id)
        )).scalars()
        for current in existing:
            if not isinstance(current, dict) or current.get("datacenter") != normalized["datacenter"]:
                continue
            existing_prefix = str(current.get("kv_prefix", "")).strip("/")
            prefix = normalized["kv_prefix"]
            if existing_prefix == prefix or existing_prefix.startswith(f"{prefix}/") or prefix.startswith(f"{existing_prefix}/"):
                raise Errors.conflict("Consul KV 前缀不能与已有配置重叠")
    locator_bytes = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return normalized, hashlib.sha256(locator_bytes).hexdigest()


async def config_file_application_ids(session: AsyncSession, file_id: int) -> list[int]:
    rows = await session.execute(
        select(ConfigFileApplication.application_id).where(ConfigFileApplication.config_file_id == file_id)
    )
    return list(rows.scalars())


async def _ensure_instance_name_unique(session: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    query = select(ConfigPlatformInstance.id).where(ConfigPlatformInstance.name == name.strip())
    if exclude_id is not None:
        query = query.where(ConfigPlatformInstance.id != exclude_id)
    if (await session.execute(query)).scalar_one_or_none() is not None:
        raise Errors.conflict("配置平台实例名称已存在")


async def _ensure_compatible_credential(session: AsyncSession, provider: str, credential_id: int) -> Credential:
    credential = await get_credential_or_404(session, credential_id)
    if credential.auth_type not in _COMPATIBLE_AUTH_TYPES.get(provider, set()):
        raise Errors.rejected("所选凭据类型与配置平台不兼容")
    return credential


async def _ensure_approval_role(session: AsyncSession, role_id: int) -> Role:
    role = await session.get(Role, role_id)
    if role is None:
        raise Errors.param("审批角色不存在")
    return role


async def _validate_application_ids(session: AsyncSession, application_ids: list[int]) -> list[int]:
    ids = sorted(set(application_ids))
    if not ids:
        return []
    existing = set((await session.execute(select(Application.id).where(Application.id.in_(ids)))).scalars())
    if existing != set(ids):
        raise Errors.param("关联的 CMDB 应用不存在")
    return ids


async def _validate_content_format(provider: str, content_format: str) -> None:
    if provider == ConfigProvider.CONSUL.value and content_format != "consul_kv":
        raise Errors.param("Consul 仅支持 KV 前缀格式")
    if provider != ConfigProvider.CONSUL.value and content_format == "consul_kv":
        raise Errors.param("仅 Consul 支持 KV 前缀格式")
    if provider == ConfigProvider.APOLLO.value and content_format == "text":
        raise Errors.param("Apollo Namespace 不支持 TEXT 格式")


def _provider_value(provider: ConfigProvider | str) -> str:
    return provider.value if isinstance(provider, ConfigProvider) else provider


def _normalize_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise Errors.param("平台地址不能包含认证信息、查询参数或片段")
    return base_url.strip().rstrip("/")


async def get_or_create_draft(session: AsyncSession, file_id: int, actor_id: int) -> ConfigDraft:
    """按当前正式基线建立唯一草稿；首次未发布文件延用创建时的草稿。"""
    config_file = await get_config_file(session, file_id)
    if config_file.status != ConfigFileStatus.ACTIVE.value:
        raise Errors.rejected("已归档配置文件不可编辑")
    draft = (await session.execute(
        select(ConfigDraft).where(ConfigDraft.config_file_id == file_id)
    )).scalar_one_or_none()
    if draft is not None:
        return draft
    version = await session.get(ConfigVersion, config_file.current_version_id) if config_file.current_version_id else None
    draft = ConfigDraft(
        config_file_id=file_id,
        content_enc=version.content_enc if version else encrypt_text(""),
        base_version_id=version.id if version else None,
        base_snapshot_id=config_file.current_snapshot_id,
        updated_by=actor_id,
    )
    session.add(draft)
    await session.flush()
    return draft


async def save_draft(
    session: AsyncSession,
    file_id: int,
    raw_content: str,
    *,
    actor_id: int,
    can_read_secret: bool,
) -> ConfigDraft:
    config_file = await get_config_file(session, file_id)
    draft = await get_or_create_draft(session, file_id, actor_id)
    try:
        current = validate_and_normalize_content(config_file.content_format, decrypt_text(draft.content_enc))
        content = (
            validate_and_normalize_content(config_file.content_format, raw_content)
            if can_read_secret else merge_redacted_update(current, config_file.content_format, raw_content)
        )
    except ValueError:
        raise Errors.param("配置正文格式或敏感字段占位符无效") from None
    draft.content_enc = encrypt_text(content.canonical)
    draft.updated_by = actor_id
    await session.flush()
    return draft


async def discard_draft(session: AsyncSession, file_id: int) -> None:
    await get_config_file(session, file_id)
    await session.execute(delete(ConfigDraft).where(ConfigDraft.config_file_id == file_id))


async def draft_to_dict(session: AsyncSession, file_id: int, actor_id: int, can_read_secret: bool) -> dict:
    config_file = await get_config_file(session, file_id)
    draft = (await session.execute(select(ConfigDraft).where(ConfigDraft.config_file_id == file_id))).scalar_one_or_none()
    version = await session.get(ConfigVersion, config_file.current_version_id) if config_file.current_version_id else None
    encrypted = draft.content_enc if draft else version.content_enc if version else None
    empty_content = "{}" if config_file.content_format in {"json", "yaml", "consul_kv"} else ""
    content = validate_and_normalize_content(config_file.content_format, decrypt_text(encrypted) if encrypted else empty_content)
    return {
        "id": draft.id if draft else None,
        "content": redact_content(content, can_read_secret),
        "base_version_id": draft.base_version_id if draft else version.id if version else None,
        "base_snapshot_id": draft.base_snapshot_id if draft else config_file.current_snapshot_id,
    }


async def submit_candidate(session: AsyncSession, file_id: int, actor_id: int) -> ConfigVersion:
    """冻结草稿、审批角色与远端基线；提交后草稿不再可编辑。"""
    config_file = await get_config_file(session, file_id)
    if config_file.status != ConfigFileStatus.ACTIVE.value:
        raise Errors.rejected("已归档配置文件不可提交")
    draft = (await session.execute(
        select(ConfigDraft).where(ConfigDraft.config_file_id == file_id)
    )).scalar_one_or_none()
    if draft is None:
        raise Errors.rejected("没有可提交的草稿")
    active = (await session.execute(
        select(ConfigVersion.id).where(
            ConfigVersion.config_file_id == file_id,
            ConfigVersion.status.in_([
                ConfigVersionStatus.PENDING_APPROVAL.value,
                ConfigVersionStatus.APPROVED.value,
                ConfigVersionStatus.PUBLISHING.value,
            ]),
        ).limit(1)
    )).scalar_one_or_none()
    if active is not None:
        raise Errors.rejected("已有待处理的候选版本")
    next_no = (await session.execute(
        select(func.max(ConfigVersion.version_no)).where(ConfigVersion.config_file_id == file_id)
    )).scalar_one()
    version = ConfigVersion(
        config_file_id=file_id,
        version_no=(next_no or 0) + 1,
        source="opspilot_publish",
        status=ConfigVersionStatus.PENDING_APPROVAL.value,
        content_enc=draft.content_enc,
        base_snapshot_id=draft.base_snapshot_id,
        approval_role_id=config_file.approval_role_id,
        submitted_by=actor_id,
    )
    session.add(version)
    await session.flush()
    await session.delete(draft)
    await session.flush()
    await session.refresh(version)
    return version


async def get_candidate(session: AsyncSession, file_id: int, version_id: int) -> ConfigVersion:
    version = await session.get(ConfigVersion, version_id)
    if version is None or version.config_file_id != file_id:
        raise Errors.not_found("配置版本不存在")
    return version


async def can_approve_candidate(session: AsyncSession, user_id: int, role_id: int) -> bool:
    """实时核对当前角色成员；提交人与审批人可以是同一用户。"""
    rows = await session.execute(
        select(Role.id, Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(User, User.id == UserRole.user_id)
        .where(User.id == user_id, User.status == "active")
    )
    return any(current_id == role_id or code == "admin" for current_id, code in rows)


async def list_approval_todo(session: AsyncSession, user_id: int, page: int, page_size: int) -> tuple[list[dict], int]:
    """只列出当前角色可审批的候选版本；admin 可处理任一配置。"""
    memberships = (await session.execute(
        select(Role.id, Role.code).join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
    )).all()
    if not memberships:
        return [], 0
    filters = [ConfigVersion.status == ConfigVersionStatus.PENDING_APPROVAL.value]
    if not any(code == "admin" for _, code in memberships):
        filters.append(ConfigVersion.approval_role_id.in_([role_id for role_id, _ in memberships]))
    total = (await session.execute(select(func.count()).select_from(ConfigVersion).where(*filters))).scalar_one()
    rows = await session.execute(
        select(ConfigVersion, ConfigFile.name, Role.name, User.username)
        .join(ConfigFile, ConfigFile.id == ConfigVersion.config_file_id)
        .outerjoin(Role, Role.id == ConfigVersion.approval_role_id)
        .outerjoin(User, User.id == ConfigVersion.submitted_by)
        .where(*filters).order_by(ConfigVersion.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    )
    return [
        {"version_id": version.id, "file_id": version.config_file_id, "file_name": name,
         "version_no": version.version_no, "approval_role_name": role_name,
         "submitter_name": username, "submitted_at": version.created_at.isoformat()}
        for version, name, role_name, username in rows
    ], total


async def approval_todo_content(session: AsyncSession, version_id: int, actor_id: int, can_read_secret: bool) -> dict:
    """审批预览只向当前有权处理该候选的成员开放，并沿用正文脱敏规则。"""
    version = await session.get(ConfigVersion, version_id)
    if (version is None or version.status != ConfigVersionStatus.PENDING_APPROVAL.value
            or version.approval_role_id is None
            or not await can_approve_candidate(session, actor_id, version.approval_role_id)):
        raise Errors.not_found("配置待办不存在")
    config_file = await get_config_file(session, version.config_file_id)
    content = validate_and_normalize_content(config_file.content_format, decrypt_text(version.content_enc))
    return {"version_id": version.id, "file_id": config_file.id, "file_name": config_file.name,
            "version_no": version.version_no, "content_format": config_file.content_format,
            "content": redact_content(content, can_read_secret)}


async def decide_candidate(
    session: AsyncSession,
    file_id: int,
    version_id: int,
    actor_id: int,
    *,
    approved: bool,
    reason: str | None = None,
) -> ConfigVersion:
    version = (await session.execute(select(ConfigVersion).where(
        ConfigVersion.id == version_id, ConfigVersion.config_file_id == file_id,
    ).with_for_update())).scalar_one_or_none()
    if version is None:
        raise Errors.not_found("配置版本不存在")
    if version.status != ConfigVersionStatus.PENDING_APPROVAL.value or version.approval_role_id is None:
        raise Errors.rejected("候选版本当前不可审批")
    if not await can_approve_candidate(session, actor_id, version.approval_role_id):
        from app.core.response import BizError

        raise BizError(Errors.NO_PERM, "不属于该配置文件的审批角色", 403)
    if approved:
        version.status = ConfigVersionStatus.APPROVED.value
        version.approved_by = actor_id
        version.approved_at = datetime.now()
    else:
        version.status = ConfigVersionStatus.REJECTED.value
        version.rejected_at = datetime.now()
        version.reject_reason = reason
    await session.flush()
    return version


async def list_versions(session: AsyncSession, file_id: int) -> list[ConfigVersion]:
    await get_config_file(session, file_id)
    rows = await session.execute(
        select(ConfigVersion).where(ConfigVersion.config_file_id == file_id).order_by(ConfigVersion.version_no.desc())
    )
    return list(rows.scalars())


def version_to_dict(version: ConfigVersion) -> dict:
    return {
        "id": version.id,
        "version_no": version.version_no,
        "status": version.status,
        "source": version.source,
        "approval_role_id": version.approval_role_id,
        "submitted_by": version.submitted_by,
        "approved_by": version.approved_by,
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }
