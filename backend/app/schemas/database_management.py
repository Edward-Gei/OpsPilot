"""数据库管理严格入口；驱动参数和原生命令始终采用白名单。"""
import json
from dataclasses import dataclass
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

PositiveId = Annotated[int, Field(strict=True, gt=0)]
DatabaseEngine = Literal["mysql", "postgresql", "mongodb", "clickhouse", "redis"]


class StrictModel(BaseModel):
    """拒绝未知字段，防止客户端绕过平台约束。"""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TlsOptions(StrictModel):
    tls: StrictBool = False
    tls_verify: StrictBool = True


class SqlOptions(TlsOptions):
    default_database: str | None = Field(default=None, min_length=1, max_length=128)


class PostgresOptions(SqlOptions):
    default_schema: str | None = Field(default=None, min_length=1, max_length=128)


class MongoOptions(SqlOptions):
    auth_database: str = Field(default="admin", min_length=1, max_length=128)


class RedisOptions(TlsOptions):
    database: Annotated[int, Field(strict=True, ge=0, le=15)] = 0
    auth_mode: Literal["password_only", "acl"] = "acl"


class InstanceWrite(StrictModel):
    """连接信息与登记字段分开校验，关系字段保留省略/清空差异。"""
    name: str = Field(min_length=1, max_length=128)
    engine: DatabaseEngine
    environment: Literal["demo", "stage", "prod"] = "demo"
    description: str | None = Field(default=None, max_length=255)
    enabled: StrictBool = True
    host: str = Field(min_length=1, max_length=255)
    port: Annotated[int, Field(strict=True, ge=1, le=65535)]
    credential_id: PositiveId | None = None
    business_line: Literal["mitrade", "tradingkey"] | None = None
    cluster_name: str | None = Field(default=None, max_length=128)
    cluster_role: str | None = Field(default=None, max_length=64)
    version: str | None = Field(default=None, max_length=128)
    connection_options: dict = Field(default_factory=dict)
    concurrency_limit: Annotated[int, Field(strict=True, ge=2, le=100)] | None = None
    app_ids: list[PositiveId] | None = None
    host_ids: list[PositiveId] | None = None

    @field_validator("name", "host")
    @classmethod
    def trim_required(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("host")
    @classmethod
    def plain_host(cls, value):
        if any(c.isspace() for c in value) or any(c in value for c in "/@?#\\") or "://" in value:
            raise ValueError("地址仅接受主机名或 IP，不能包含 URI/认证材料")
        return value

    @field_validator("description", "cluster_name", "cluster_role", "version")
    @classmethod
    def normalize_text(cls, value):
        return value.strip() or None if value is not None else None

    @field_validator("app_ids", "host_ids")
    @classmethod
    def strict_relations(cls, value):
        if value is None:
            raise ValueError("关联字段不能为 null；请省略或传 []")
        if len(value) > 1000 or len(value) != len(set(value)):
            raise ValueError("关联 ID 必须唯一且不超过 1000 项")
        return value

    @model_validator(mode="after")
    def validate_options(self):
        options_type = {"postgresql": PostgresOptions, "mongodb": MongoOptions,
                        "redis": RedisOptions}.get(self.engine, SqlOptions)
        self.connection_options = options_type.model_validate(self.connection_options).model_dump(exclude_none=True)
        return self


class RoleGrantRequest(StrictModel):
    role_ids: list[PositiveId]

    @field_validator("role_ids")
    @classmethod
    def distinct_roles(cls, value):
        if len(value) != len(set(value)) or len(value) > 1000:
            raise ValueError("角色 ID 重复或过多")
        return value


class SqlContext(StrictModel):
    database: str | None = Field(default=None, min_length=1, max_length=128)
    schema_: str | None = Field(default=None, alias="schema", min_length=1, max_length=128)


class MetadataContext(SqlContext):
    table: str | None = Field(default=None, min_length=1, max_length=128)
    collection: str | None = Field(default=None, min_length=1, max_length=128)


class MongoContext(StrictModel):
    database: str = Field(min_length=1, max_length=128)
    collection: str = Field(min_length=1, max_length=128)


class RedisContext(StrictModel):
    database: Annotated[int, Field(strict=True, ge=0, le=15)] = 0


def _validate_mongo_document(value):
    """入口只验证有界 JSON 结构；操作语义交给授权后的持久拒绝审计。"""
    def walk(node, depth=0):
        if depth > 12:
            raise ValueError("筛选嵌套最多 12 层")
        if isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str) or len(key) > 256:
                    raise ValueError("JSON 字段名称过长或类型不合法")
                walk(item, depth + 1)
        elif isinstance(node, list):
            if len(node) > 1000:
                raise ValueError("筛选数组过长")
            for item in node:
                walk(item, depth + 1)
    walk(value)
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 32768:
        raise ValueError("筛选最多 32768 字节")
    return value


class MongoSort(StrictModel):
    field: str = Field(min_length=1, max_length=128)
    direction: Literal[1, -1]


class MongoFilterOperation(StrictModel):
    filter: dict = Field(default_factory=dict)
    _filter = field_validator("filter")(_validate_mongo_document)


class MongoFind(MongoFilterOperation):
    type: Literal["find"]
    projection: dict | None = None
    sort: list[MongoSort] = Field(default_factory=list, max_length=16)
    limit: Annotated[int, Field(strict=True, ge=1, le=10000)] = 1000

    @field_validator("projection")
    @classmethod
    def projection_only(cls, value):
        return _validate_mongo_document(value) if value is not None else None


class MongoCount(MongoFilterOperation):
    type: Literal["count_documents"]


class MongoDistinct(MongoFilterOperation):
    type: Literal["distinct"]
    field: str = Field(min_length=1, max_length=128)
    limit: Annotated[int, Field(strict=True, ge=1, le=10000)] = 1000


MongoOperation = Annotated[Union[MongoFind, MongoCount, MongoDistinct], Field(discriminator="type")]


class RedisScan(StrictModel):
    type: Literal["scan"]
    pattern: str = Field(default="*", max_length=512)
    cursor: str | None = Field(default=None, max_length=2048)
    count: Annotated[int, Field(strict=True, ge=1, le=1000)] = 100


class RedisKey(StrictModel):
    type: Literal["type", "ttl", "length"]
    key: str = Field(min_length=1, max_length=1024)


class RedisPreview(StrictModel):
    type: Literal["string_preview"]
    key: str = Field(min_length=1, max_length=1024)
    offset: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 0
    length: Annotated[int, Field(strict=True, ge=1, le=65536)] = 4096


class RedisCursorPage(StrictModel):
    """SCAN 分页只接受游标，不能静默忽略不适用的 offset。"""
    type: Literal["hash_page", "set_page"]
    key: str = Field(min_length=1, max_length=1024)
    cursor: str | None = Field(default=None, max_length=2048)
    count: Annotated[int, Field(strict=True, ge=1, le=1000)] = 100


class RedisOffsetPage(StrictModel):
    """有序范围使用偏移量，不能接收没有意义的游标。"""
    type: Literal["list_page", "zset_page"]
    key: str = Field(min_length=1, max_length=1024)
    offset: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 0
    count: Annotated[int, Field(strict=True, ge=1, le=1000)] = 100


RedisOperation = Annotated[Union[RedisScan, RedisKey, RedisPreview, RedisCursorPage, RedisOffsetPage], Field(discriminator="type")]


class SqlTaskRequest(StrictModel):
    kind: Literal["sql"]
    instance_id: PositiveId
    context: SqlContext = Field(default_factory=SqlContext)
    # 字节上限在语义提交阶段留存拒绝证据，不在入口丢失审计。
    sql: str


class ProbeTaskRequest(StrictModel):
    kind: Literal["probe"]
    instance_id: PositiveId


class MetadataTaskRequest(StrictModel):
    kind: Literal["metadata"]
    instance_id: PositiveId
    context: MetadataContext = Field(default_factory=MetadataContext)
    operation: Literal["databases", "schemas", "tables", "columns", "collections", "fields"]


class MongoReadTaskRequest(StrictModel):
    kind: Literal["mongo_read"]
    instance_id: PositiveId
    context: MongoContext
    operation: MongoOperation


class RedisReadTaskRequest(StrictModel):
    kind: Literal["redis_read"]
    instance_id: PositiveId
    context: RedisContext = Field(default_factory=RedisContext)
    operation: RedisOperation


DatabaseTaskRequest = Annotated[Union[SqlTaskRequest, ProbeTaskRequest, MetadataTaskRequest,
                                     MongoReadTaskRequest, RedisReadTaskRequest], Field(discriminator="kind")]


@dataclass(frozen=True)
class ActorContext:
    """身份只从认证上下文构建，不能由请求体指定。"""
    user_id: int
    username: str
    source_ip: str | None = None


@dataclass(frozen=True)
class TaskSubmission:
    task_id: int
    status: str
    executed_statement: str | None
    operation_summary: str
    http_status: int = 202
    error_code: int = 0
    reason: str | None = None
