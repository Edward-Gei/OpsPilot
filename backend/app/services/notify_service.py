"""通知服务：事件发射 + 消息模板渲染 + 渠道/映射/记录管理（05-任务拆解 M6-2）。

emit 语义（M6 正式版）：按事件-渠道映射逐渠道落 notification_record(pending)，
随后 XADD ops:notify:queue 提示分发器尽快扫描；XADD 仅是"加速器"，失败或
与业务事务 commit 竞态都不影响送达——分发器 30s 定时扫描是可靠兜底
（冲突决策 C3）。

消息模板：按渠道×事件保存于 notify_channel.config.templates，
emit 落库时即按渠道和事件渲染——发送记录里的 title/content 即最终实发内容，
修改模板只对新产生的通知生效。
"""
import logging
import json
import re
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import redis as redis_mod
from app.core.constants import NotifyChannelType, NotifyEvent
from app.core.response import Errors
from app.core.security import decrypt_text, encrypt_text
from app.models.auth import User
from app.models.notify import NotificationRecord, NotifyChannel, NotifyChannelEvent, UserNotification
from app.notify import ChannelSendError, NotifyMessage, get_channel

logger = logging.getLogger("opspilot.notify")

# 敏感配置掩码占位：回显与"提交未变更"识别共用（与 system.py 约定一致）
SECRET_MASK = "******"

# V1 落地渠道（可真实发送）；其余仅预留占位
IMPLEMENTED_CHANNELS = {
    NotifyChannelType.EMAIL.value,
    NotifyChannelType.WEBHOOK.value,
    NotifyChannelType.TEAMS.value,
}

# 事件中文名（与前端 NotifyCenter eventText 同口径）：模板 {event} 变量取值
NOTIFY_EVENT_TEXT = {
    NotifyEvent.TICKET_PENDING_APPROVAL.value: "工单待审批",
    NotifyEvent.TICKET_APPROVED.value: "审批通过",
    NotifyEvent.TICKET_REJECTED.value: "审批驳回",
    NotifyEvent.EXECUTION_SUCCESS.value: "执行成功",
    NotifyEvent.EXECUTION_FAILED.value: "执行失败",
    NotifyEvent.EXECUTION_INTERRUPTED.value: "执行中断",
}

# 模板长度上限（按渠道模板字段校验）
TITLE_TEMPLATE_MAX = 200
CONTENT_TEMPLATE_MAX = 2000
JSON_TEMPLATE_MAX = 10000

# 模板占位符语法：{变量名}（仅字母数字下划线）
_VAR_PATTERN = re.compile(r"\{(\w+)\}")

# test_channel 预览用示例变量：与 emit 实发变量口径一致（job_host_name 为作业主机名），
# 保证“测试发送”预览效果与真实通知一致（无 app_name——emit 从不注入该变量）
SAMPLE_TEMPLATE_VARS = {
    "event": "审批通过",
    "ticket_no": "TK20260001",
    "ticket_title": "示例工单",
    "job_host_name": "示例作业主机",
    "creator": "admin",
    "approver": "ops1",
    "comment": "同意",
    "node": 1,
    "role": "运维组",
    "reason": "worker_lost",
    "detail": "示例中断详情",
    "ref_id": 1,
    "receivers": ["admin", "ops1"],
}

TEMPLATE_EVENTS = [
    {"key": key, "label": label}
    for key, label in NOTIFY_EVENT_TEXT.items()
]
TEMPLATE_VARIABLES = [
    {"key": "event", "label": "事件名称", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "ticket_no", "label": "工单号", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "ticket_title", "label": "工单标题", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "job_host_name", "label": "作业主机名", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "creator", "label": "创建人", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "receiver", "label": "收件人", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "receivers", "label": "收件人列表", "type": "array", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "time", "label": "触发时间", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "ref_id", "label": "关联 ID", "type": "number", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "default_title", "label": "默认标题", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "default_content", "label": "默认正文", "type": "string", "group": "通用", "events": list(NOTIFY_EVENT_TEXT)},
    {"key": "node", "label": "审批节点", "type": "number", "group": "审批", "events": ["ticket.pending_approval"]},
    {"key": "role", "label": "审批角色", "type": "string", "group": "审批", "events": ["ticket.pending_approval"]},
    {"key": "approver", "label": "审批人", "type": "string", "group": "审批", "events": ["ticket.approved", "ticket.rejected"]},
    {"key": "comment", "label": "审批意见", "type": "string", "group": "审批", "events": ["ticket.approved", "ticket.rejected"]},
    {"key": "reason", "label": "中断原因", "type": "string", "group": "中断", "events": ["execution.interrupted"]},
    {"key": "detail", "label": "详情", "type": "string", "group": "中断", "events": ["execution.interrupted"]},
]
_VARIABLE_TYPES = {item["key"]: item["type"] for item in TEMPLATE_VARIABLES}

_JSON_DEFAULTS = {
    "webhook": {
        event: '{"event": "{event}", "title": "{default_title}", "content": "{default_content}", "receivers": {receivers}, "ref_id": {ref_id}, "timestamp": "{time}"}'
        for event in NOTIFY_EVENT_TEXT
    },
    "teams": {
        event: '{"@type": "MessageCard", "@context": "http://schema.org/extensions", "summary": "{default_title}", "title": "{default_title}", "text": "{default_content}"}'
        for event in NOTIFY_EVENT_TEXT
    },
}

def default_template(channel_type: str, event: str) -> dict:
    """返回渠道×事件的只读默认模板；未配置自定义模板时由发送链路使用。"""
    if channel_type == "email":
        return {"title": "{default_title}", "content": "{default_content}"}
    if channel_type not in _JSON_DEFAULTS:
        return {}
    field = "body" if channel_type == "webhook" else "card"
    return {field: _JSON_DEFAULTS[channel_type][event]}

def default_templates(channel_type: str) -> dict[str, dict]:
    return {event: default_template(channel_type, event) for event in NOTIFY_EVENT_TEXT}


def _validate_templates(channel_type: str, templates: dict) -> None:
    """校验渠道事件模板，拒绝未知事件和不匹配的字段。"""
    if not isinstance(templates, dict):
        raise Errors.param("templates 必须是对象")
    valid_events = set(NOTIFY_EVENT_TEXT)
    for event, item in templates.items():
        if event not in valid_events or not isinstance(item, dict):
            raise Errors.param(f"模板事件无效: {event}")
        if channel_type == "email":
            allowed = {"title", "content"}
            if set(item) - allowed:
                raise Errors.param(f"邮件模板字段无效: {event}")
            if len(str(item.get("title") or "")) > TITLE_TEMPLATE_MAX:
                raise Errors.param(f"标题模板不能超过 {TITLE_TEMPLATE_MAX} 字")
            if len(str(item.get("content") or "")) > CONTENT_TEMPLATE_MAX:
                raise Errors.param(f"正文模板不能超过 {CONTENT_TEMPLATE_MAX} 字")
        elif channel_type in {"webhook", "teams"}:
            key = "body" if channel_type == "webhook" else "card"
            if set(item) - {key} or key not in item:
                raise Errors.param(f"{channel_type} 模板字段无效: {event}")
            if not isinstance(item[key], str):
                raise Errors.param(f"{channel_type} 模板字段必须是字符串: {event}")
            value = item[key]
            if len(value) > JSON_TEMPLATE_MAX:
                raise Errors.param(f"JSON 模板不能超过 {JSON_TEMPLATE_MAX} 字")
            try:
                validate_json_template(value)
            except (ValueError, json.JSONDecodeError) as exc:
                raise Errors.param(f"JSON 模板无效: {exc}") from exc


def render_template(tpl: str, variables: dict) -> str:
    """渲染普通文本模板；未知或空变量原样保留占位符。"""
    def _sub(m: re.Match) -> str:
        key = m.group(1)
        value = variables.get(key)
        if value is None:
            kind = _VARIABLE_TYPES.get(key)
            if kind == "number":
                return "0"
            if kind == "array":
                return "[]"
            return "" if kind == "string" else m.group(0)
        return str(value)

    return _VAR_PATTERN.sub(_sub, tpl)


def _placeholder_context(template: str, match_start: int) -> bool:
    """判断占位符是否处于 JSON 字符串中，忽略转义引号。"""
    in_string = False
    escaped = False
    for char in template[:match_start]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            in_string = not in_string
    return in_string


def _placeholder_is_object_key(template: str, match_end: int) -> bool:
    """判断字符串占位符是否位于 JSON 对象 key，key 不允许使用变量。"""
    escaped = False
    index = match_end
    while index < len(template):
        char = template[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            index += 1
            break
        index += 1
    while index < len(template) and template[index].isspace():
        index += 1
    return index < len(template) and template[index] == ":"


def _json_placeholder_value(key: str, variables: dict, *, strict: bool) -> str:
    """把已知原生变量编码为 JSON 值；未知变量仅在字符串上下文中允许。"""
    if key not in _VARIABLE_TYPES:
        if strict:
            raise ValueError(f"未知变量 {{{key}}} 不能作为 JSON 原生值")
        return "{" + key + "}"
    kind = _VARIABLE_TYPES[key]
    value = variables.get(key)
    if value is None:
        value = [] if kind == "array" else 0 if kind == "number" else ""
    if kind == "number":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"变量 {{{key}}} 必须是 number 类型")
    elif kind == "array" and not isinstance(value, list):
        raise ValueError(f"变量 {{{key}}} 必须是 array 类型")
    elif kind == "string" and not isinstance(value, str):
        value = str(value)
    return json.dumps(value, ensure_ascii=False)


def render_json_template(template: str, variables: dict, *, validate: bool = True) -> str:
    """渲染 JSON 模板并解析校验，保证原生数字/数组变量不被字符串化。"""
    pieces: list[str] = []
    last = 0
    for match in _VAR_PATTERN.finditer(template):
        pieces.append(template[last:match.start()])
        in_string = _placeholder_context(template, match.start())
        key = match.group(1)
        if in_string:
            if _placeholder_is_object_key(template, match.end()):
                raise ValueError(f"变量 {{{key}}} 不能作为 JSON 对象 key")
            kind = _VARIABLE_TYPES.get(key, "string")
            if key in _VARIABLE_TYPES and kind != "string":
                raise ValueError(f"变量 {{{key}}} 必须以 JSON 原生值使用，不能放在字符串中")
            value = variables.get(key)
            replacement = "{" + key + "}" if key not in _VARIABLE_TYPES else str(value if value is not None else "")
            pieces.append(json.dumps(replacement, ensure_ascii=False)[1:-1] if key in _VARIABLE_TYPES else replacement)
        else:
            pieces.append(_json_placeholder_value(key, variables, strict=True))
        last = match.end()
    pieces.append(template[last:])
    rendered = "".join(pieces)
    if validate:
        json.loads(rendered)
    return rendered


def validate_json_template(template: str) -> None:
    """校验 JSON 模板结构、变量位置和原生类型占位符。"""
    render_json_template(template, {key: (0 if kind == "number" else [] if kind == "array" else "x") for key, kind in _VARIABLE_TYPES.items()})


def _iso(dt: datetime | None) -> str | None:
    """datetime → ISO 字符串（None 透传）。"""
    return dt.isoformat(sep=" ", timespec="seconds") if dt else None


# ---------- 事件发射（业务侧统一入口） ----------

async def emit(
    session: AsyncSession,
    event: NotifyEvent,
    *,
    title: str,
    content: str | None = None,
    receiver: str | None = None,
    ref_type: str | None = None,
    ref_id: int | None = None,
    variables: dict | None = None,
) -> None:
    """发射通知事件：按事件-渠道映射逐渠道渲染模板并落 pending 记录（无映射兜底 email）。

    此处不判断渠道 enabled——记录始终留痕，未启用渠道由分发器置 failed；
    发送、重试、状态回写全部由 worker 侧分发器承担。
    配置了模板的渠道按 variables 渲染 title/content，未配置保持业务默认文案。
    """
    channels = list(
        (
            await session.execute(
                select(NotifyChannelEvent.channel_type).where(NotifyChannelEvent.event == event.value)
            )
        ).scalars()
    ) or ["email"]
    receiver_names = [item.strip() for item in (receiver or "").split(",") if item.strip()]
    # 一次性取涉及渠道的模板配置（templates 按事件保存）
    configs = {
        row.type: (row.config or {})
        for row in (
            await session.execute(select(NotifyChannel).where(NotifyChannel.type.in_(channels)))
        ).scalars()
    }
    # 基础变量（事件文案/默认标题正文/收件人/时间/工单 ID）合并业务变量，同名时业务侧优先
    all_vars = {
        "event": NOTIFY_EVENT_TEXT.get(event.value, event.value),
        "default_title": title,
        "default_content": content,
        "receiver": receiver,
        "receivers": receiver_names,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ref_id": ref_id,
        **(variables or {}),
    }
    for channel_type in channels:
        cfg = configs.get(channel_type) or {}
        custom = (cfg.get("templates") or {}).get(event.value) or {}
        template = {**default_template(channel_type, event.value), **custom}
        r_title = render_template(template.get("title", title), all_vars)[:255]
        render_error: str | None = None
        if channel_type in {"webhook", "teams"}:
            json_key = "body" if channel_type == "webhook" else "card"
            try:
                r_content = render_json_template(template[json_key], all_vars)
            except (ValueError, json.JSONDecodeError) as exc:
                # 配置保存时已校验；运行时变量类型变化仍需留下明确失败记录。
                r_content = ""
                render_error = f"模板渲染失败: {exc}"
        else:
            r_content = render_template(template.get("content", content) or "", all_vars)
        session.add(
            NotificationRecord(
                event=event.value,
                channel_type=channel_type,
                receiver=receiver,
                title=r_title,
                content=r_content,
                ref_type=ref_type,
                ref_id=ref_id,
                status="failed" if render_error else "pending",
                error=render_error,
            )
        )
    await session.flush()
    # 站内信（NOTIFY-06）：同事务按收件人每人一行，不受渠道启用/事件映射影响；
    # 标题/正文用业务默认文案（不走渠道模板），解析不到的用户名静默跳过
    await _emit_inapp(
        session, event, receiver=receiver, title=title, content=content,
        ref_type=ref_type, ref_id=ref_id,
    )
    # 队列提示（加速器）：失败仅告警，可靠性由分发器定时扫描保证
    try:
        await redis_mod.redis_client.xadd(
            redis_mod.NOTIFY_QUEUE, {"event": event.value}, maxlen=10000, approximate=True
        )
    except Exception:  # noqa: BLE001 Redis 抖动不能反噬业务事务
        logger.warning("通知队列 XADD 失败（事件 %s），等待定时扫描兜底", event.value)


async def emit_security_email(
    session: AsyncSession, *, receiver: str, title: str, content: str, source_ip: str | None = None
) -> None:
    """落密码重置安全邮件记录，不经过事件映射或站内信。"""
    record = NotificationRecord(
        event="password_reset",
        channel_type=NotifyChannelType.EMAIL.value,
        receiver=receiver,
        title=title,
        content=content,
        status="pending",
    )
    session.add(record)
    await session.flush()
    if source_ip:
        await redis_mod.redis_client.set(
            redis_mod.KEY_PWD_RESET_NOTIFICATION_IP.format(record_id=record.id),
            source_ip,
            ex=86400,
        )
    try:
        await redis_mod.redis_client.xadd(
            redis_mod.NOTIFY_QUEUE, {"event": "password_reset"}, maxlen=10000, approximate=True
        )
    except Exception:  # noqa: BLE001 队列失败由扫描兜底
        logger.warning("密码重置邮件队列提示失败，等待定时扫描兜底")


async def _emit_inapp(
    session: AsyncSession,
    event: NotifyEvent,
    *,
    receiver: str | None,
    title: str,
    content: str | None,
    ref_type: str | None,
    ref_id: int | None,
) -> None:
    """落站内信：把逗号分隔的收件人用户名解析为有效用户，每人插一行 user_notification。"""
    names = list(dict.fromkeys(n.strip() for n in (receiver or "").split(",") if n.strip()))
    if not names:
        return
    user_ids = (
        await session.execute(select(User.id).where(User.username.in_(names)))
    ).scalars()
    for uid in user_ids:
        session.add(
            UserNotification(
                user_id=uid, event=event.value, title=title[:255], content=content,
                ref_type=ref_type, ref_id=ref_id,
            )
        )
    await session.flush()


# ---------- 站内信查询与已读（/notifications，NOTIFY-06） ----------

async def list_user_notifications(
    session: AsyncSession, user_id: int, *, page: int, page_size: int, only_unread: bool = False,
) -> tuple[list[dict], int]:
    """我的站内信分页：按创建时间倒序，可只看未读。"""
    cond = [UserNotification.user_id == user_id]
    if only_unread:
        cond.append(UserNotification.is_read.is_(False))
    total = (
        await session.execute(select(func.count()).select_from(UserNotification).where(*cond))
    ).scalar_one()
    rows = (
        await session.execute(
            select(UserNotification).where(*cond)
            .order_by(UserNotification.created_at.desc(), UserNotification.id.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars()
    items = [
        {
            "id": n.id,
            "event": n.event,
            "title": n.title,
            "content": n.content,
            "ref_type": n.ref_type,
            "ref_id": n.ref_id,
            "is_read": bool(n.is_read),
            "created_at": _iso(n.created_at),
        }
        for n in rows
    ]
    return items, total


async def unread_count(session: AsyncSession, user_id: int) -> int:
    """未读站内信数（铃铛角标 30s 轮询）。"""
    return (
        await session.execute(
            select(func.count()).select_from(UserNotification).where(
                UserNotification.user_id == user_id, UserNotification.is_read.is_(False)
            )
        )
    ).scalar_one()


async def mark_read(session: AsyncSession, user_id: int, notification_id: int) -> None:
    """单条已读：校验归属，非本人/不存在统一 40401；重复已读幂等。"""
    row = await session.get(UserNotification, notification_id)
    if row is None or row.user_id != user_id:
        raise Errors.not_found("通知不存在")
    if not row.is_read:
        row.is_read = True
        row.read_at = datetime.now()


async def mark_all_read(session: AsyncSession, user_id: int) -> int:
    """全部已读：返回本次置已读条数。"""
    rows = (
        await session.execute(
            select(UserNotification).where(
                UserNotification.user_id == user_id, UserNotification.is_read.is_(False)
            )
        )
    ).scalars().all()
    now = datetime.now()
    for row in rows:
        row.is_read = True
        row.read_at = now
    return len(rows)


# ---------- 渠道配置管理（/notify/channels） ----------

async def list_channels(session: AsyncSession) -> list[dict]:
    """全渠道配置列表：敏感项以 ****** 掩码回显，按枚举顺序输出六类型。"""
    rows = {
        c.type: c for c in (await session.execute(select(NotifyChannel))).scalars()
    }
    items = []
    for channel_type in NotifyChannelType:
        row = rows.get(channel_type.value)
        items.append(
            {
                "type": channel_type.value,
                "implemented": channel_type.value in IMPLEMENTED_CHANNELS,
                "enabled": bool(row.enabled) if row else False,
                "config": (row.config if row else None) or {},
                "template_events": TEMPLATE_EVENTS,
                "template_defaults": default_templates(channel_type.value),
                "template_variables": TEMPLATE_VARIABLES,
                "secret": SECRET_MASK if (row and row.secret_enc) else None,
                "updated_at": _iso(row.updated_at) if row else None,
            }
        )
    return items


def _validate_channel_type(channel_type: str) -> None:
    """渠道类型合法性校验：非枚举值直接 40001。"""
    if channel_type not in {t.value for t in NotifyChannelType}:
        raise Errors.param(f"未知渠道类型: {channel_type}")


async def get_channel_row(session: AsyncSession, channel_type: str) -> NotifyChannel:
    """取渠道配置行；种子缺失时兜底创建（幂等）。"""
    _validate_channel_type(channel_type)
    row = (
        await session.execute(select(NotifyChannel).where(NotifyChannel.type == channel_type))
    ).scalar_one_or_none()
    if row is None:
        row = NotifyChannel(type=channel_type, enabled=False)
        session.add(row)
        await session.flush()
    return row


async def update_channel(
    session: AsyncSession,
    channel_type: str,
    *,
    enabled: bool,
    config: dict | None,
    secret: str | None,
    actor_id: int,
) -> None:
    """更新渠道配置（04-API §10）：secret 不传或提交掩码 = 保留原密钥。

    secret 传空字符串 = 显式清空；预留渠道不允许启用（无发送实现）。
    """
    row = await get_channel_row(session, channel_type)
    if enabled and channel_type not in IMPLEMENTED_CHANNELS:
        raise Errors.param(f"渠道 {channel_type} 暂未实现，不能启用")
    cfg = config or {}
    _validate_templates(channel_type, cfg.get("templates") or {})
    row.enabled = enabled
    row.config = cfg
    if secret is not None and secret != SECRET_MASK:
        # AES-256-GCM 加密落库（复用 M3 字段加密工具）；空串视为清空
        row.secret_enc = encrypt_text(secret) if secret else None
    row.updated_by = actor_id
    await session.flush()


async def test_channel(
    session: AsyncSession,
    channel_type: str,
    *,
    config: dict | None,
    secret: str | None,
    receiver: str | None,
    event: str = "ticket.approved",
) -> tuple[bool, str]:
    """按当前表单值发送测试消息，同步返回成败（保存前预测，与作业主机测试同形态）。

    secret 为掩码/不传时回退库中已存密钥；Email 渠道 receiver 需直接给邮箱地址。
    """
    _validate_channel_type(channel_type)
    if event not in NOTIFY_EVENT_TEXT:
        return False, f"未知通知事件: {event}"
    if secret is None or secret == SECRET_MASK:
        row = (
            await session.execute(select(NotifyChannel).where(NotifyChannel.type == channel_type))
        ).scalar_one_or_none()
        secret = decrypt_text(row.secret_enc) if row and row.secret_enc else None
    receivers = [r.strip() for r in (receiver or "").split(",") if r.strip()]
    cfg = config or {}
    # 用示例变量渲染表单里的模板：保存前即可预览模板效果
    default_title = "OpsPilot 通知测试"
    default_content = f"这是一条来自 OpsPilot 的测试消息（渠道 {channel_type}），收到即配置成功。"
    variables = {
        **SAMPLE_TEMPLATE_VARS,
        "default_title": default_title,
        "default_content": default_content,
        "receiver": receiver,
        "receivers": receivers,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    custom = (cfg.get("templates") or {}).get(event) or {}
    template = {**default_template(channel_type, event), **custom}
    try:
        title_value = render_template(template.get("title", default_title), variables)[:255]
        if channel_type in {"webhook", "teams"}:
            json_key = "body" if channel_type == "webhook" else "card"
            content_value = render_json_template(template[json_key], variables)
        else:
            content_value = render_template(template.get("content", default_content), variables)
    except (ValueError, json.JSONDecodeError) as exc:
        return False, f"模板无效: {exc}"
    message = NotifyMessage(event=event, title=title_value, content=content_value, receivers=receivers)
    try:
        await get_channel(channel_type).send(message, cfg, secret)
    except ChannelSendError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 测试接口不抛 500，前端按 success 展示
        return False, f"渠道异常: {exc}"
    return True, "发送成功"


# ---------- 事件-渠道映射管理（/notify/events） ----------

async def list_event_mappings(session: AsyncSession) -> list[dict]:
    """事件-渠道映射：按六事件全量输出（含空映射事件）。"""
    rows = (await session.execute(select(NotifyChannelEvent))).scalars().all()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row.event, []).append(row.channel_type)
    return [
        {"event": event.value, "channels": sorted(grouped.get(event.value, []))}
        for event in NotifyEvent
    ]


async def update_event_mappings(session: AsyncSession, mappings: dict[str, list[str]]) -> None:
    """全量提交映射（04-API §10）：整表按提交内容重建，未提交事件视为清空。"""
    valid_events = {e.value for e in NotifyEvent}
    valid_channels = {t.value for t in NotifyChannelType}
    for event, channels in mappings.items():
        if event not in valid_events:
            raise Errors.param(f"未知事件: {event}")
        for channel_type in channels:
            if channel_type not in valid_channels:
                raise Errors.param(f"未知渠道类型: {channel_type}")
    # 先清后插：uk_event_channel 保证幂等，事务内整体生效
    for row in (await session.execute(select(NotifyChannelEvent))).scalars():
        await session.delete(row)
    await session.flush()
    for event, channels in mappings.items():
        for channel_type in dict.fromkeys(channels):  # 去重且保序
            session.add(NotifyChannelEvent(event=event, channel_type=channel_type))
    await session.flush()


# ---------- 发送记录查询（/notify/records） ----------

async def list_records(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    event: str | None = None,
    channel: str | None = None,
    status: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> tuple[list[dict], int]:
    """发送记录分页：event/channel/status/时间范围筛选（04-API §10）。"""
    query = select(NotificationRecord)
    if event:
        query = query.where(NotificationRecord.event == event)
    if channel:
        query = query.where(NotificationRecord.channel_type == channel)
    if status:
        query = query.where(NotificationRecord.status == status)
    if start:
        query = query.where(NotificationRecord.created_at >= start)
    if end:
        # 只传日期时把上界补到当天末（与执行列表同约定）
        query = query.where(
            NotificationRecord.created_at <= (f"{end} 23:59:59" if len(end) == 10 else end)
        )
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(NotificationRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": r.id,
            "event": r.event,
            "channel_type": r.channel_type,
            "receiver": r.receiver,
            "title": r.title,
            "content": r.content,
            "ref_type": r.ref_type,
            "ref_id": r.ref_id,
            "status": r.status,
            "retry_count": r.retry_count,
            "next_retry_at": _iso(r.next_retry_at),
            "error": r.error,
            "sent_at": _iso(r.sent_at),
            "created_at": _iso(r.created_at),
        }
        for r in rows
    ]
    return items, total
