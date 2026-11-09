"""只读 SQL 边界：全量校验、末条选择及非 SELECT 副作用拒绝。"""
import unittest

from app.database_sql_policy import SqlPolicyError, validate_sql


class SqlPolicyTests(unittest.TestCase):
    def test_last_complete_select_keeps_quoted_semicolons(self):
        result = validate_sql("mysql", "SELECT 'a;b';\nSELECT\n  ';SELECT x' AS text;")
        self.assertEqual(result.statement_count, 2)
        self.assertEqual(result.selected_statement_index, 1)
        self.assertIn("';SELECT x'", result.executed_statement)
        self.assertNotIn("'a;b'", result.executed_statement)

    def test_readonly_cte_union_and_aggregate(self):
        for engine in ("mysql", "postgresql", "clickhouse"):
            with self.subTest(engine=engine):
                result = validate_sql(engine, "WITH t AS (SELECT 1 AS id) SELECT COUNT(*) AS n FROM t UNION ALL SELECT 2")
                self.assertEqual(result.statement_count, 1)

    def test_mixed_statements_reject_entire_submission(self):
        for sql in ("SELECT 1; UPDATE users SET x=1; SELECT 2", "SET @a=1;SELECT 2", "DELETE FROM t;SELECT 1", "SELECT 1; SELCT 2"):
            with self.subTest(sql=sql), self.assertRaises(SqlPolicyError):
                validate_sql("mysql", sql)

    def test_side_effects_and_unknown_functions_are_rejected(self):
        cases = {
            "mysql": ["SELECT * FROM t FOR UPDATE", "SELECT 1 INTO OUTFILE '/tmp/x'", "SELECT LOAD_FILE('/tmp/x')", "SELECT my_udf()", "/*!50000 SELECT 1 */ SELECT 2"],
            "postgresql": ["WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "SELECT pg_read_file('/etc/passwd')", "SELECT nextval('x')", "SELECT * INTO x FROM t", "SELECT pg_advisory_lock(1)"],
            "clickhouse": ["SELECT * FROM url('http://example.com')", "SELECT 1 SETTINGS max_execution_time=600", "SELECT file('/tmp/x')"],
        }
        for engine, queries in cases.items():
            for sql in queries:
                with self.subTest(engine=engine,sql=sql), self.assertRaises(SqlPolicyError):
                    validate_sql(engine, sql)

    def test_size_and_unsupported_engine(self):
        for engine, sql in (("redis", "SELECT 1"), ("mysql", " "*65537), ("mysql", "; -- none")):
            with self.subTest(engine=engine), self.assertRaises(SqlPolicyError):
                validate_sql(engine, sql)

    def test_common_expressions_and_qualified_function_boundary(self):
        validate_sql("postgresql", "SELECT CAST(1 AS BIGINT), CASE WHEN 1=1 THEN 2 ELSE 3 END, COALESCE(NULL, 1)")
        validate_sql("mysql", "SELECT '/*! literal; SELECT x */' AS value")
        for sql in ("SELECT public.abs(1)", "SELECT evil.lower('x')", "SELECT", "SELECT FROM t"):
            with self.subTest(sql=sql), self.assertRaises(SqlPolicyError):
                validate_sql("postgresql", sql)


if __name__ == "__main__":
    unittest.main()
