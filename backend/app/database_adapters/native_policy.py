"""受控原生查询白名单；驱动调用前再校验，避免数据库任务被篡改后越界。"""
import json
import re

from app.database_adapters.base import AdapterError, validate_text_identifier

_MONGO_OPS = {
    "find": {"type", "filter", "projection", "sort", "limit"},
    "count_documents": {"type", "filter"},
    "distinct": {"type", "field", "filter", "limit"},
}
_FILTER_OPS = {
    "$eq", "$ne", "$gt", "$gte", "$lt", "$lte", "$in", "$nin", "$and", "$or", "$nor", "$not",
    "$exists", "$type", "$all", "$elemMatch", "$size", "$regex", "$options",
}
_REDIS_OPS = {
    "scan": {"type", "pattern", "cursor", "count"},
    "type": {"type", "key"}, "ttl": {"type", "key"}, "length": {"type", "key"},
    "string_preview": {"type", "key", "offset", "length"},
    "hash_page": {"type", "key", "cursor", "count"},
    "list_page": {"type", "key", "offset", "count"},
    "set_page": {"type", "key", "cursor", "count"},
    "zset_page": {"type", "key", "offset", "count"},
}


def _fail():
    raise AdapterError("invalid_native_operation", "原生查询不符合受控只读操作规则")


def _integer(value, low: int, high: int):
    if type(value) is not int or not low <= value <= high:
        _fail()


def _filter_tree(value, depth=0):
    if depth > 10:
        _fail()
    if isinstance(value, dict):
        if len(value) > 100:
            _fail()
        for key, child in value.items():
            if not isinstance(key, str) or not key or len(key) > 255 or "\x00" in key:
                _fail()
            if key.startswith("$") and key not in _FILTER_OPS:
                _fail()
            _filter_tree(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > 1000:
            _fail()
        for child in value:
            _filter_tree(child, depth + 1)
    elif not (value is None or type(value) in (str, int, float, bool)):
        _fail()


def validate_native_operation(engine: str, context: dict, operation: dict) -> None:
    """引擎与操作判别；既不接受任意命令名，也不将额外驱动选项透传。"""
    if not isinstance(operation, dict) or not isinstance(context, dict):
        _fail()
    if engine == "mongodb":
        if set(context) != {"database", "collection"}:
            _fail()
        for name in context.values():
            validate_text_identifier(name)
        allowed = _MONGO_OPS.get(operation.get("type"))
        if allowed is None or set(operation) - allowed:
            _fail()
        filter_value = operation.get("filter", {})
        if not isinstance(filter_value, dict):
            _fail()
        try:
            encoded = json.dumps(filter_value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, RecursionError):
            _fail()
        if len(encoded) > 16384:
            _fail()
        _filter_tree(filter_value)
        projection = operation.get("projection")
        if projection is not None:
            if not isinstance(projection, dict) or len(projection) > 100:
                _fail()
            for key, value in projection.items():
                validate_text_identifier(key)
                if key.startswith("$") or type(value) is not int or value not in (0, 1):
                    _fail()
        sort = operation.get("sort", [])
        if not isinstance(sort, list) or len(sort) > 16:
            _fail()
        for item in sort:
            if not isinstance(item, dict) or set(item) != {"field", "direction"}:
                _fail()
            validate_text_identifier(item["field"])
            if item["field"].startswith("$") or type(item["direction"]) is not int or item["direction"] not in (-1, 1):
                _fail()
        if "limit" in operation:
            _integer(operation["limit"], 1, 10000)
        if operation["type"] == "distinct":
            validate_text_identifier(operation.get("field"))
            if operation["field"].startswith("$"):
                _fail()
    elif engine == "redis":
        if set(context) != {"database"}:
            _fail()
        _integer(context["database"], 0, 15)
        allowed = _REDIS_OPS.get(operation.get("type"))
        if allowed is None or set(operation) - allowed:
            _fail()
        if operation["type"] != "scan":
            validate_text_identifier(operation.get("key"), max_length=1024)
        elif "pattern" in operation:
            validate_text_identifier(operation["pattern"], max_length=1024)
        if "count" in operation:
            _integer(operation["count"], 1, 1000)
        if "offset" in operation:
            _integer(operation["offset"], 0, 2147483647)
        if "length" in operation:
            _integer(operation["length"], 1, 65536)
        cursor = operation.get("cursor")
        if cursor is not None and (not isinstance(cursor, str) or len(cursor) > 256 or not re.fullmatch(r"(0|v1\.[A-Za-z0-9._-]+)", cursor)):
            _fail()
    else:
        raise AdapterError("unsupported_engine", "该引擎不接受原生查询")
