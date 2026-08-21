"""流程动态参数预生成：在工单模板作业主机执行脚本并短期保存结果。"""

import asyncio
import json
import secrets
import shlex
from datetime import datetime, timedelta

from jinja2.sandbox import SandboxedEnvironment
from sqlalchemy import select

from app.core.response import Errors
from app.engine.ssh_runner import open_connection
from app.models.cmdb import JobHost
from app.models.job import Credential
from app.models.ticket import TicketParameterPrepare
from app.services import template_service


def _generator_command(params: dict) -> str:
    """将参数 JSON 安全传入远端 Shell，避免引号破坏 export 命令。"""
    return f"export PARAMS_JSON={shlex.quote(json.dumps(params, ensure_ascii=False))}; bash -s"


async def _run_generator(host: JobHost, credential, script: str, timeout: int, params: dict):
    """执行生成脚本并只返回 stdout；不接入执行日志或审计写入器。"""
    connection = await open_connection(host.ip, host.ssh_port, credential)
    try:
        command = _generator_command(params)
        # 先渲染模板，让固定值和用户参数可直接用于脚本；完整参数仍通过 PARAMS_JSON 提供。
        rendered_script = SandboxedEnvironment(
            autoescape=False, keep_trailing_newline=True,
        ).from_string(script).render(**(params or {}))
        result = await asyncio.wait_for(connection.run(command, input=rendered_script), timeout=timeout)
        if result.exit_status != 0:
            raise Errors.param("动态参数脚本执行失败")
        return str(result.stdout).strip()
    finally:
        connection.close()


def _parse_output(output: str, generated_names: list[str]) -> tuple[dict, dict]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise Errors.param("动态参数脚本必须返回 JSON") from exc
    if isinstance(payload, dict):
        options = {name: value for name, value in payload.items() if isinstance(value, list)}
        values = {name: payload[name] for name in generated_names if name in payload and name not in options}
    elif isinstance(payload, list) and len(generated_names) == 1:
        values, options = {}, {generated_names[0]: payload}
    elif len(generated_names) == 1:
        values, options = {generated_names[0]: payload}, {}
    else:
        raise Errors.param("动态参数脚本返回结构无法映射到参数定义")
    missing = set(generated_names) - set(values) - set(options)
    if missing:
        raise Errors.param(f"动态参数脚本未返回: {sorted(missing)}")
    return values, options


async def prepare_parameters(session, *, template_id: int, params: dict) -> dict:
    """预生成参数并返回 token；结果过期后不可用于创建工单。"""
    tpl = await template_service.get_template_or_404(session, template_id)
    process = await template_service.get_process_or_404(session, tpl.process_template_id)
    if process.status != "enabled":
        raise Errors.conflict("流程模板已停用，不能生成参数")
    schema = tpl.params_schema or []
    definitions = {p["name"]: p for p in schema}
    unknown = set(params) - set(definitions)
    if unknown:
        raise Errors.param(f"参数未定义: {sorted(unknown)}")
    generated = [p["name"] for p in schema if p["source"] == "generated"]
    values = {p["name"]: p.get("default") for p in schema if p["source"] == "fixed"}
    values.update({k: v for k, v in params.items() if definitions[k]["source"] != "generated"})
    options: dict = {}
    if generated:
        host = await session.get(JobHost, tpl.job_host_id)
        credential = await session.get(Credential, host.credential_id) if host else None
        if not host or not credential:
            raise Errors.conflict("作业主机未配置可用凭据")
        output = await _run_generator(host, credential, tpl.generator_script or "", tpl.generator_timeout or 60, values)
        generated_values, generated_options = _parse_output(output, generated)
        values.update(generated_values)
        options.update(generated_options)
    token = secrets.token_urlsafe(32)
    stable_params = {k: v for k, v in (params or {}).items() if definitions[k]["source"] != "generated"}
    row = TicketParameterPrepare(token=token, template_id=template_id, input_params=stable_params, values=values,
                                 options=options, expires_at=datetime.now() + timedelta(minutes=10))
    session.add(row)
    await session.flush()
    return {"prepare_id": token, "values": values, "options": options, "expires_at": row.expires_at.isoformat()}


async def consume_parameters(session, *, token: str, template_id: int, params: dict) -> dict:
    """校验预生成上下文与本次输入一致，并原子标记已消费。"""
    row = (await session.execute(select(TicketParameterPrepare).where(TicketParameterPrepare.token == token))).scalar_one_or_none()
    if row is None or row.template_id != template_id or row.consumed_at or row.expires_at < datetime.now():
        raise Errors.conflict("动态参数预生成结果已失效")
    selected = {}
    for name, options in (row.options or {}).items():
        if name in params:
            if params[name] not in options:
                raise Errors.param(f"动态参数 {name} 不是有效候选值")
            selected[name] = params[name]
        elif len(options) == 1:
            selected[name] = options[0]
        else:
            raise Errors.param(f"动态参数 {name} 必须选择一个候选值")
    row.consumed_at = datetime.now()
    await session.flush()
    return {**(row.values or {}), **selected}
