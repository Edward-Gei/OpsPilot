"""应用配置正文的格式校验、规范化与脱敏。"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import yaml


SUPPORTED_CONTENT_FORMATS = frozenset({"properties", "yaml", "json", "text", "consul_kv"})
SECRET_PLACEHOLDER = "__OPSPILOT_SECRET_PLACEHOLDER__"
TEXT_MASK = "********"

_SENSITIVE_KEY_PARTS = frozenset({
    "password", "passwd", "secret", "token", "apikey", "accesskey", "accesskeyid",
    "privatekey", "credential", "serviceaccountkeyjson",
})
_SENSITIVE_URL_PARAMS = frozenset({"sig", "signature", "xamzsignature", "token", "accesstoken", "apikey", "secret", "key"})


@dataclass(frozen=True)
class NormalizedContent:
    """同一语义正文的稳定表示，供版本比较和远端回读确认使用。"""

    format: str
    canonical: str
    structured: Any | None


class _NoDuplicateYamlLoader(yaml.SafeLoader):
    """禁止 YAML 重复键，避免不同解析器采用不同覆盖顺序。"""


def _construct_yaml_mapping(loader: _NoDuplicateYamlLoader, node: yaml.nodes.MappingNode, deep: bool = False):
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("YAML 对象键必须为字符串")
        if key in mapping:
            raise ValueError(f"YAML 存在重复键: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_NoDuplicateYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_yaml_mapping,
)


def validate_and_normalize_content(content_format: str, raw: str) -> NormalizedContent:
    """校验输入格式并产生可比较的规范化正文。"""
    _require_utf8(raw)
    if content_format not in SUPPORTED_CONTENT_FORMATS:
        raise ValueError("配置格式暂不支持")
    if content_format == "text":
        return NormalizedContent(content_format, raw, None)
    if content_format == "properties":
        return _normalize_properties(raw)
    if content_format == "consul_kv":
        return _normalize_consul_kv(raw)
    if content_format == "json":
        parsed = _load_json(raw)
        _ensure_json_compatible(parsed, "JSON")
        return _structured_content("json", parsed)

    try:
        parsed = yaml.load(raw, Loader=_NoDuplicateYamlLoader)
    except (yaml.YAMLError, ValueError) as exc:
        raise ValueError(f"YAML 格式无效: {exc}") from exc
    _ensure_json_compatible(parsed, "YAML")
    return _structured_content("yaml", parsed)


def redact_content(content: NormalizedContent, can_read_secret: bool) -> str:
    """按权限返回可展示的正文，绝不做全文字符串替换。"""
    if can_read_secret:
        return content.canonical
    if content.format == "text":
        return TEXT_MASK
    assert content.structured is not None
    return _structured_content(content.format, _redact_value(content.structured)).canonical


def merge_redacted_update(
    baseline: NormalizedContent,
    content_format: str,
    raw_update: str,
) -> NormalizedContent:
    """把无密钥查看权限的占位符安全合并到持久化基线。"""
    if baseline.format != content_format:
        raise ValueError("配置格式不可变")
    if content_format == "text":
        if raw_update == SECRET_PLACEHOLDER:
            raise ValueError("TEXT 内容不支持使用敏感值占位符")
        return validate_and_normalize_content(content_format, raw_update)

    updated = validate_and_normalize_content(content_format, raw_update)
    assert baseline.structured is not None and updated.structured is not None
    merged = _merge_value(baseline.structured, updated.structured)
    return _structured_content(content_format, merged)


def _require_utf8(raw: str) -> None:
    if not isinstance(raw, str):
        raise ValueError("配置正文必须是 UTF-8 文本")
    if "\x00" in raw:
        raise ValueError("配置正文不能包含空字符")
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("配置正文必须是 UTF-8 文本") from exc


def _load_json(raw: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"JSON 存在重复键: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"JSON 不支持常量: {value}")),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"JSON 格式无效: {exc}") from exc


def _normalize_properties(raw: str) -> NormalizedContent:
    values: dict[str, str] = {}
    for line_no, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "!")):
            continue
        separator_positions = [position for position in (line.find("="), line.find(":")) if position >= 0]
        position = min(separator_positions) if separator_positions else len(line)
        key = line[:position].strip()
        value = line[position + 1:].lstrip() if separator_positions else ""
        if not key:
            raise ValueError(f"properties 第 {line_no} 行缺少键")
        if key in values:
            raise ValueError(f"properties 存在重复键: {key}")
        values[key] = value
    return NormalizedContent("properties", _canonical_properties(values), values)


def _normalize_consul_kv(raw: str) -> NormalizedContent:
    parsed = _load_json(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Consul KV 正文必须是 JSON 对象")
    for key, value in parsed.items():
        if not key:
            raise ValueError("Consul KV 键不能为空")
        if not isinstance(value, str):
            raise ValueError("Consul KV 的值必须是字符串")
    canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2)
    return NormalizedContent("consul_kv", canonical, parsed)


def _structured_content(content_format: str, parsed: Any) -> NormalizedContent:
    if content_format == "json":
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    elif content_format == "yaml":
        canonical = yaml.safe_dump(parsed, allow_unicode=True, sort_keys=True, default_flow_style=False)
    elif content_format == "properties":
        canonical = _canonical_properties(parsed)
    elif content_format == "consul_kv":
        canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, indent=2)
    else:
        raise ValueError("配置格式暂不支持")
    return NormalizedContent(content_format, canonical, parsed)


def _canonical_properties(values: dict[str, str]) -> str:
    return "".join(f"{key}={values[key]}\n" for key in sorted(values))


def _ensure_json_compatible(value: Any, source: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError(f"{source} 不支持非有限数字")
    if isinstance(value, list):
        for item in value:
            _ensure_json_compatible(item, source)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{source} 对象键必须为字符串")
            _ensure_json_compatible(item, source)
        return
    raise ValueError(f"{source} 包含不支持的值类型")


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key).lower()
    parts = [part for part in re.split(r"[_\-.]+", normalized) if part]
    compact = "".join(parts)
    return compact in _SENSITIVE_KEY_PARTS or any(part in _SENSITIVE_KEY_PARTS for part in parts)


def _is_sensitive_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    return any(re.sub(r"[^a-z0-9]", "", name.lower()) in _SENSITIVE_URL_PARAMS
               for name, _ in parse_qsl(parsed.query, keep_blank_values=True))


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: SECRET_PLACEHOLDER if _is_sensitive_key(key) else _redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, str) and _is_sensitive_url(value):
        return SECRET_PLACEHOLDER
    return value


def _merge_value(baseline: Any, updated: Any) -> Any:
    if isinstance(baseline, dict):
        if not isinstance(updated, dict):
            raise ValueError("脱敏内容的对象结构不能被替换")
        merged: dict[str, Any] = {}
        for key, old_value in baseline.items():
            if _is_sensitive_key(key):
                if key not in updated or updated[key] != SECRET_PLACEHOLDER:
                    raise ValueError("无密钥查看权限时不能修改敏感字段")
                merged[key] = old_value
            elif key in updated:
                merged[key] = _merge_value(old_value, updated[key])
            elif _contains_sensitive_value(old_value):
                raise ValueError("无密钥查看权限时不能删除含敏感字段的结构")
        for key, new_value in updated.items():
            if key in baseline:
                continue
            if _is_sensitive_key(key) or _contains_placeholder(new_value):
                raise ValueError("敏感值占位符位置无效")
            merged[key] = new_value
        return merged
    if isinstance(baseline, list):
        if not isinstance(updated, list):
            raise ValueError("脱敏内容的列表结构不能被替换")
        if _contains_sensitive_value(baseline) and len(baseline) != len(updated):
            raise ValueError("包含敏感字段的列表不能调整长度")
        return [_merge_value(old, new) for old, new in zip(baseline, updated)] if _contains_sensitive_value(baseline) else updated
    if isinstance(baseline, str) and _is_sensitive_url(baseline):
        if updated != SECRET_PLACEHOLDER:
            raise ValueError("无密钥查看权限时不能修改敏感字段")
        return baseline
    if updated == SECRET_PLACEHOLDER:
        raise ValueError("敏感值占位符位置无效")
    return updated


def _contains_sensitive_value(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_is_sensitive_key(key) or _contains_sensitive_value(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_value(item) for item in value)
    return isinstance(value, str) and _is_sensitive_url(value)


def _contains_placeholder(value: Any) -> bool:
    if value == SECRET_PLACEHOLDER:
        return True
    if isinstance(value, dict):
        return any(_contains_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    return False
