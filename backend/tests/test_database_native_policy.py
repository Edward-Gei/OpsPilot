"""原生入口只允许固定只读操作，过滤器不接受脚本或写入运算。"""
import unittest

from app.database_adapters.base import AdapterError
from app.database_adapters.native_policy import validate_native_operation


class NativePolicyTests(unittest.TestCase):
    def test_mongo_allows_small_find(self):
        validate_native_operation("mongodb", {"database": "demo", "collection": "orders"}, {
            "type": "find", "filter": {"price": {"$gte": 10}}, "projection": {"_id": 0}, "sort": [{"field": "price", "direction": -1}], "limit": 10,
        })

    def test_nested_javascript_and_unknown_options_are_rejected(self):
        for operation in (
            {"type": "find", "filter": {"$or": [{"x": 1}, {"$where": "function(){}"}]}},
            {"type": "aggregate", "pipeline": []},
            {"type": "find", "filter": {}, "allowDiskUse": True},
            {"type": "distinct", "field": "x", "limit": True},
        ):
            with self.subTest(operation=operation), self.assertRaises(AdapterError):
                validate_native_operation("mongodb", {"database": "demo", "collection": "orders"}, operation)

    def test_redis_only_bounded_forms(self):
        for operation in (
            {"type": "scan", "pattern": "demo:*", "count": 100},
            {"type": "string_preview", "key": "a", "offset": 0, "length": 100},
            {"type": "hash_page", "key": "a", "count": 10},
        ):
            validate_native_operation("redis", {"database": 0}, operation)
        for operation in (
            {"type": "get", "key": "a"}, {"type": "keys", "pattern": "*"}, {"type": "eval", "script": "return 1"},
            {"type": "scan", "count": 10000000}, {"type": "string_preview", "key": "a", "length": 99999999},
            {"type": "scan", "cursor": "-1"}, {"type": "ttl", "key": "x", "command": "SET"},
        ):
            with self.subTest(operation=operation), self.assertRaises(AdapterError):
                validate_native_operation("redis", {"database": 0}, operation)


if __name__ == "__main__":
    unittest.main()
