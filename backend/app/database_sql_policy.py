"""按方言全量解析的只读 SQL 策略；绝不从错误输入回退提取 SELECT。"""
import sqlglot
from sqlglot import Dialect, exp
from sqlglot.errors import ErrorLevel, SqlglotError

from app.database_adapters.base import ValidatedSql

_DIALECTS = {"mysql": "mysql", "postgresql": "postgres", "clickhouse": "clickhouse"}
_SAFE_FUNCTIONS = {
    "CAST", "CASE", "MAP", "STRUCT", "DATABASE", "CURRENT_DATABASE", "CURRENT_SCHEMA", "CURRENT_SCHEMAS", "CURRENT_USER", "SESSION_USER", "USER", "VERSION",
    "ABS", "ACOS", "ANY_VALUE", "APPROX_DISTINCT", "ARRAY", "ARRAY_AGG", "ARRAY_CONCAT", "ARRAY_SIZE", "ASCII", "ASIN", "ATAN", "ATAN2", "AVG", "BITWISE_AND", "BITWISE_OR", "BITWISE_XOR", "BOOL_AND", "BOOL_OR", "CEIL", "CEILING", "CHAR_LENGTH", "CHR", "COALESCE", "CONCAT", "CONCAT_WS", "CONVERT", "COS", "COUNT", "COUNT_IF", "CURRENT_DATE", "CURRENT_DATETIME", "CURRENT_TIME", "CURRENT_TIMESTAMP", "DATE", "DATE_ADD", "DATE_DIFF", "DATE_FORMAT", "DATE_SUB", "DATE_TRUNC", "DAY", "DAY_OF_MONTH", "DAY_OF_WEEK", "DAY_OF_YEAR", "DENSE_RANK", "EXP", "EXTRACT", "FIRST_VALUE", "FLOOR", "FROM_UNIXTIME", "GREATEST", "GROUP_CONCAT", "HEX", "HOUR", "IF", "IFNULL", "INITCAP", "JSON_EXTRACT", "JSON_EXTRACT_SCALAR", "JSON_ARRAY_LENGTH", "JSON_FORMAT", "LAG", "LAST_VALUE", "LEAD", "LEAST", "LEFT", "LENGTH", "LN", "LOG", "LOG10", "LOWER", "LPAD", "LTRIM", "MAX", "MD5", "MEDIAN", "MIN", "MINUTE", "MOD", "MONTH", "NOW", "NTH_VALUE", "NTILE", "NULLIF", "NVL", "PERCENT_RANK", "POW", "POWER", "QUANTILE", "RANK", "REGEXP_EXTRACT", "REGEXP_LIKE", "REGEXP_REPLACE", "REPEAT", "REPLACE", "REVERSE", "RIGHT", "ROUND", "ROW_NUMBER", "RPAD", "RTRIM", "SECOND", "SIGN", "SIN", "SPLIT", "SQRT", "STDDEV", "STDDEV_POP", "STDDEV_SAMP", "STR_POSITION", "STR_TO_DATE", "STRING_AGG", "SUBSTR", "SUBSTRING", "SUM", "TAN", "TIME", "TIMESTAMP", "TIMESTAMP_ADD", "TIMESTAMP_DIFF", "TO_CHAR", "TO_DATE", "TO_TIMESTAMP", "TRIM", "TRUNC", "TRY_CAST", "UNHEX", "UNIX_TIMESTAMP", "UPPER", "VARIANCE", "VAR_POP", "VAR_SAMP", "WEEK", "YEAR",
    # ClickHouse 普通函数没有专门节点，逐个放行；不放行外部数据源函数。
    "TOSTRING", "TOINT32", "TOINT64", "TOUINT32", "TOUINT64", "TOFLOAT64", "TODATE", "TODATETIME", "TOYYYYMM", "TOSTARTOFDAY", "TOSTARTOFMONTH", "FORMATDATETIME", "COUNTIF", "SUMIF", "UNIQ", "UNIQEXACT", "TODAY", "YESTERDAY",
}
_SAFE_NODES = set("""
Select Union Intersect Except Subquery With CTE From Table TableAlias Identifier Column Star Literal Null Boolean
Alias Distinct Where Having Qualify Group Order Ordered Limit Offset Join And Or Not Paren Tuple Array Map Bracket
EQ NEQ GT GTE LT LTE NullSafeEQ NullSafeNEQ Is In Between Like ILike Escape RegexpLike SimilarTo
Add Sub Mul Div IntDiv Mod Pow Neg Pos BitwiseAnd BitwiseOr BitwiseXor BitwiseNot BitwiseLeftShift BitwiseRightShift
Cast TryCast DataType DataTypeParam Interval Case When If Window WindowSpec Filter WithinGroup IgnoreNulls RespectNulls
Exists Any All Extract Var Collate AtTimeZone Dot JSONExtract JSONExtractScalar JSONPath JSONPathRoot JSONPathKey JSONPathSubscript
JSONPathWildcard JSONPathRecursive JSONPathSlice JSONPathUnion Concat DPipe ArrayContains ArrayOverlaps
""".split())


class SqlPolicyError(ValueError):
    """拒绝原因不含原文，便于安全写入独立审计。"""

    def __init__(self, reason: str, code: str = "sql_not_readonly"):
        self.code = code
        self.reason = reason
        super().__init__(reason)


def validate_sql(engine: str, submitted_text: str) -> ValidatedSql:
    """所有顶层语句和子树满足白名单后，才选择最后一条完整语句。"""
    dialect = _DIALECTS.get(engine)
    if dialect is None:
        raise SqlPolicyError("该引擎不接受 SELECT SQL", "unsupported_engine")
    if not isinstance(submitted_text, str) or len(submitted_text.encode("utf-8")) > 65536:
        raise SqlPolicyError("SQL 不能超过 65536 字节", "input_too_large")
    try:
        tokens = Dialect.get_or_raise(dialect).tokenizer.tokenize(submitted_text)
        if any(comment.lstrip().startswith(("!", "+")) for token in tokens for comment in token.comments):
            raise SqlPolicyError("不允许执行注释或查询提示")
        statements = [node for node in sqlglot.parse(submitted_text, read=dialect, error_level=ErrorLevel.RAISE) if node is not None]
    except (SqlglotError, ValueError, RecursionError) as exc:
        raise SqlPolicyError("SQL 解析失败，只允许完整 SELECT", "sql_parse_error") from exc
    if not statements:
        raise SqlPolicyError("请输入完整 SELECT")
    for statement in statements:
        _validate_tree(statement)
    try:
        selected = statements[-1].sql(dialect=dialect, comments=False, unsupported_level=ErrorLevel.RAISE)
    except SqlglotError as exc:
        raise SqlPolicyError("该 SELECT 包含不支持的方言语法") from exc
    return ValidatedSql(dialect, submitted_text, selected, len(statements), len(statements) - 1)


def _validate_tree(statement: exp.Expression) -> None:
    if not isinstance(statement, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise SqlPolicyError("只允许 SELECT，混合语句整段拒绝")
    for node in statement.walk():
        if isinstance(node, exp.Select) and not node.expressions:
            raise SqlPolicyError("请输入完整 SELECT")
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            raise SqlPolicyError("不允许限定名称的用户函数")
        if isinstance(node, exp.Func):
            function = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if str(function).upper() not in _SAFE_FUNCTIONS:
                raise SqlPolicyError("SELECT 包含未允许的函数或数据源")
        elif type(node).__name__ not in _SAFE_NODES:
            raise SqlPolicyError("SELECT 包含未允许的语法或副作用")
        if isinstance(node, exp.Table) and not isinstance(node.this, exp.Identifier):
            raise SqlPolicyError("不允许外部数据源或表函数")
        if isinstance(node, exp.Column) and isinstance(node.this, exp.Var):
            raise SqlPolicyError("不允许会话变量")
