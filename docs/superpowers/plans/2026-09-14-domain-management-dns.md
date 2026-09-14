# 域名管理与 DNS 一期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a DNS Zone asset ledger that discovers existing public Zones from Tencent Cloud DNSPod, AWS Route 53, and Google Cloud DNS, snapshots their records, and lets authorized users safely manage supported simple-routing record sets.

**Architecture:** `dns_zone` and `dns_record_set` hold the local snapshot while provider adapters own cloud SDK calls and canonical data conversion. `domain_service` owns credential resolution, operation leases, remote-drift detection, snapshot replacement, audit events, and safe error mapping; the API and Vue pages remain thin clients of that service. Cloud providers remain the source of truth, with explicit manual synchronization and an immediate post-write synchronization path.

**Tech Stack:** FastAPI, async SQLAlchemy, Alembic, Pydantic v2, openpyxl, Vue 3, TypeScript, Ant Design Vue, boto3, tencentcloud-sdk-python, google-cloud-dns.

**Spec:** `docs/superpowers/specs/2026-09-14-domain-management-dns-design.md`

## Global Constraints

- The only first-phase asset is a DNS Zone; do not add domain registrar, certificate, renewal, application-link, ticket, or remote Zone create/delete features.
- Support only public Zones for `tencent_dnspod`, `aws_route53`, and `google_cloud_dns`; a binding starts from an existing credential and an adapter-discovered remote Zone.
- The provider is the source of truth. Persist a local snapshot only after a complete initial sync succeeds; manual sync and every successful record write refresh that snapshot.
- Keep credentials in the existing encrypted `credential` table. AWS and Tencent use `username_password`; Google uses `secret_file`; no DNS endpoint, audit event, log line, exception message, or export may reveal secret material.
- New or updated editable records allow only `A`, `AAAA`, `CNAME`, `MX`, `TXT`, `CAA`, and `SRV`; `SOA`, every `NS`, Alias, advanced-routing, unsupported, and out-of-range-TTL records are always read-only.
- Editable TTL is an integer in the inclusive range `300-86400`; a new record defaults to `300`. Existing records outside that range remain visible and read-only.
- Serialize each Zone's sync and write operations with a persisted lease. Do not keep a database transaction open while a cloud SDK call runs.
- Before every record write, compare the freshly-read canonical remote record set with the local snapshot. Return `40901` on drift and require manual sync.
- A remote write is never retried automatically. If it succeeds but its immediate sync fails, retain the old snapshot, mark sync failure, return a sanitized failure, and write separate remote-write-success and sync-failure audit events.
- Zone import is the only Excel import. Its exact required columns are `服务商* | 凭据名称* | Zone 名称* | 远端 Zone ID* | 描述`; record import and record-value global search are out of scope.
- Add only `domain:read`, `domain:write`, and `domain:delete`; only the built-in `admin` role receives them by default.
- Static `/domains` child paths must be declared before dynamic `/{zone_id}` paths. Keep the existing `{code, message, data}` response envelope and existing Chinese Conventional Commit convention.

---

## File Structure

### New backend files

| File | Responsibility |
| --- | --- |
| `backend/app/models/domain.py` | SQLAlchemy models for Zone ledger rows and normalized record-set snapshots. |
| `backend/app/schemas/domain.py` | Pydantic request models for discovery, binding, description updates, records, and import row contracts. |
| `backend/app/dns_providers/base.py` | Adapter protocol, canonical provider data classes, record-key codec, provider exceptions, and shared DNS normalization/validation helpers. |
| `backend/app/dns_providers/__init__.py` | Provider adapter registry keyed by `DomainProvider`. |
| `backend/app/dns_providers/aws_route53.py` | Route 53 discovery, record conversion, safe reads, writes, and change-status polling. |
| `backend/app/dns_providers/tencent_dnspod.py` | DNSPod public-domain discovery, record grouping, and record mutation adapter. |
| `backend/app/dns_providers/google_cloud_dns.py` | Google Cloud DNS public-zone discovery, resource-record-set conversion, and change-status polling. |
| `backend/app/services/domain_service.py` | Zone/record business behavior, secure credential resolution, leases, snapshot replacement, audit data shaping, and remote-drift protection. |
| `backend/app/services/domain_excel.py` | Zone import template/parser and Zone/record snapshot export workbook generation. |
| `backend/app/api/v1/domains.py` | `/domains` REST endpoints and safe response serialization. |
| `backend/alembic/versions/0019_domain_management.py` | Idempotent MySQL migration for `dns_zone` and `dns_record_set`. |
| `backend/tests/test_domain_migration.py` | Migration existence-guard tests. |
| `backend/tests/test_dns_record_normalization.py` | Pure normalization, editable-type, and grammar tests. |
| `backend/tests/test_dns_providers.py` | Adapter tests using injected fake SDK clients; never use real cloud credentials. |
| `backend/tests/test_domain_service.py` | Lease, initial bind, snapshot, drift, failure, audit, and credential-resolution service tests. |
| `backend/tests/test_domain_api.py` | Permission and REST contract tests with a fake adapter registry. |
| `backend/tests/test_domain_excel.py` | Workbook layout, import result, duplicate, and export tests. |

### New frontend files

| File | Responsibility |
| --- | --- |
| `frontend/src/api/domain.ts` | Typed Domain REST client, multipart upload, and Zone export/template download helpers. |
| `frontend/src/views/domain/DomainList.vue` | Zone table, filters, description editing, discovery/binding, import/export, synchronization, and local unbinding. |
| `frontend/src/views/domain/DomainDetail.vue` | Zone summary and record-set table with create/edit/delete preview-confirmation flows. |

### Modified files

| File | Responsibility |
| --- | --- |
| `backend/app/models/__init__.py` | Import domain models so `Base.metadata` and Alembic include them. |
| `backend/app/core/constants.py` | Add domain provider/sync enums and the three permissions; extend only `admin` defaults. |
| `backend/app/core/response.py` | Add sanitized upstream-provider error `50201`. |
| `backend/app/api/v1/__init__.py` | Register the Domain router. |
| `backend/app/services/credential_service.py` | Refuse deletion when a credential is referenced by one or more Zones. |
| `backend/app/services/search_service.py` | Add the permission-gated `domains` global-search segment. |
| `backend/requirements.txt` | Add the three official DNS provider SDKs. |
| `backend/tests/test_rbac_api.py` | Assert default domain permission allocation. |
| `backend/tests/test_job_api.py` | Assert credential deletion protection for Zone references. |
| `backend/tests/test_search_api.py` | Assert Domain search matching and permission omission. |
| `frontend/src/router/index.ts` | Add `/domains` and `/domains/:id` routes with `domain:read` protection. |
| `frontend/src/layouts/BasicLayout.vue` | Add the Domain menu and the `domains` global-search result group. |
| `frontend/src/api/search.ts` | Add the typed `domains` search segment. |
| `docs/01-产品需求文档PRD.md` | Describe the implemented first-phase module and exclusions. |
| `docs/02-技术架构设计.md` | Document snapshot/adapters/lease and credential boundaries. |
| `docs/03-数据库设计.md` | Document both new tables, constraints, indexes, and no-foreign-key convention. |
| `docs/04-API设计.md` | Document permissions, error `50201`, and the Domain API contracts. |
| `docs/05-开发任务拆解.md` | Mark the delivered Domain work and validation responsibilities. |

## Shared Interfaces

These contracts are introduced before any provider implementation. Later tasks must use these names and types exactly.

```python
@dataclass(frozen=True)
class ProviderCredential:
    provider: DomainProvider
    credential_id: int
    login_user: str | None
    secret: str


@dataclass(frozen=True)
class ZoneRef:
    provider: DomainProvider
    remote_zone_id: str
    zone_name: str


@dataclass(frozen=True)
class RemoteRecordSet:
    record_key: str
    record_name: str
    record_type: str
    ttl: int | None
    values: Sequence[str]
    read_only_reason: str | None
    provider_meta: dict[str, object]


@dataclass(frozen=True)
class RecordSetDraft:
    record_name: str
    record_type: str
    ttl: int
    values: Sequence[str]


class DnsProviderAdapter(Protocol):
    async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
        raise NotImplementedError

    async def list_record_sets(self, zone: ZoneRef, credential: ProviderCredential) -> list[RemoteRecordSet]:
        raise NotImplementedError

    async def get_record_set(self, zone: ZoneRef, record_key: str, credential: ProviderCredential) -> RemoteRecordSet | None:
        raise NotImplementedError

    async def create_simple_record_set(self, zone: ZoneRef, record: RecordSetDraft, credential: ProviderCredential) -> None:
        raise NotImplementedError

    async def replace_simple_record_set(self, zone: ZoneRef, before: RemoteRecordSet, after: RecordSetDraft, credential: ProviderCredential) -> None:
        raise NotImplementedError

    async def delete_simple_record_set(self, zone: ZoneRef, before: RemoteRecordSet, credential: ProviderCredential) -> None:
        raise NotImplementedError
```

`record_key` is a URL-safe base64 encoding of a canonical provider locator JSON object. It is opaque to clients, deterministically decodable by the owning adapter, and limited to 512 characters; `provider_meta` stores the same non-secret locator data for display/debugging only. A record comparison uses normalized name, type, TTL, value set, and locator, never provider response ordering.

### Task 1: Add the persistent Domain model, migration, error code, and metadata registration

**Files:**
- Create: `backend/app/models/domain.py`
- Create: `backend/alembic/versions/0019_domain_management.py`
- Create: `backend/tests/test_domain_migration.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/core/constants.py`
- Modify: `backend/app/core/response.py`

**Interfaces:**
- Consumes: `Base`, `UBIGINT`, `DT3`, `created_at_column`, and `updated_at_column` from `app.models.base`.
- Produces: `DnsZone`, `DnsRecordSet`, `DomainProvider`, `DnsSyncStatus`, and `Errors.upstream(message: str) -> BizError`.

- [ ] **Step 1: Write the failing model and migration guard tests**

```python
def test_domain_models_are_registered_in_metadata():
    from app.models import Base

    assert {"dns_zone", "dns_record_set"}.issubset(Base.metadata.tables)
    assert "uk_dns_zone_provider_remote" in {
        constraint.name for constraint in Base.metadata.tables["dns_zone"].constraints
    }


def test_upgrade_creates_only_missing_domain_tables(monkeypatch):
    migration = _load_migration_module()
    bind = _Bind(existing_tables={"dns_zone"})
    created: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "create_table", lambda name, *args, **kwargs: created.append(name))
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: None)

    migration.upgrade()

    assert created == ["dns_record_set"]
```

- [ ] **Step 2: Run the tests to verify they fail before implementation**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_migration.py -q`

Expected: FAIL because `app.models.domain` and migration `0019` do not exist.

- [ ] **Step 3: Implement the models, idempotent migration, enum values, and upstream error**

```python
class DnsZone(Base):
    __tablename__ = "dns_zone"
    __table_args__ = (
        UniqueConstraint("provider", "remote_zone_id", name="uk_dns_zone_provider_remote"),
        Index("idx_dns_zone_credential", "credential_id"),
        Index("idx_dns_zone_sync_status", "sync_status"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_zone_id: Mapped[str] = mapped_column(String(255), nullable=False)
    zone_name: Mapped[str] = mapped_column(String(253), nullable=False)
    credential_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sync_status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    last_synced_at: Mapped[datetime | None] = mapped_column(DT3)
    last_sync_error: Mapped[str | None] = mapped_column(String(512))
    operation_token: Mapped[str | None] = mapped_column(String(36))
    operation_kind: Mapped[str | None] = mapped_column(String(32))
    operation_expires_at: Mapped[datetime | None] = mapped_column(DT3)
    created_by: Mapped[int | None] = mapped_column(UBIGINT)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
```

```python
class DnsRecordSet(Base):
    __tablename__ = "dns_record_set"
    __table_args__ = (
        UniqueConstraint("zone_id", "record_key", name="uk_dns_record_set_zone_key"),
        Index("idx_dns_record_set_zone", "zone_id"),
        Index("idx_dns_record_set_zone_name_type", "zone_id", "record_name", "record_type"),
        Base.__table_args__,
    )

    id: Mapped[int] = pk_column()
    zone_id: Mapped[int] = mapped_column(UBIGINT, nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    record_name: Mapped[str] = mapped_column(String(253), nullable=False)
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    ttl: Mapped[int | None] = mapped_column(Integer)
    values: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    read_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_only_reason: Mapped[str | None] = mapped_column(String(255))
    provider_meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()
```

Use `_table_exists(bind, table)` with `information_schema.tables`, call `op.create_table` only for missing tables, then create the unique/index definitions with the exact names above. Add `DomainProvider` values `tencent_dnspod`, `aws_route53`, `google_cloud_dns`; add `DnsSyncStatus` values `success`, `failed`, `syncing`; add `Errors.UPSTREAM = 50201` and `Errors.upstream()` with HTTP status `502`. Import both models in `app.models.__init__`.

- [ ] **Step 4: Run the focused tests and metadata smoke check**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_migration.py tests/test_smoke.py -q`

Expected: PASS; the migration skips an existing table and `Base.metadata` can create both new tables under SQLite test setup.

- [ ] **Step 5: Commit the model foundation**

```bash
git add backend/app/models/domain.py backend/app/models/__init__.py backend/app/core/constants.py backend/app/core/response.py backend/alembic/versions/0019_domain_management.py backend/tests/test_domain_migration.py
git commit -m "feat(domain): 建立 DNS Zone 快照数据模型"
```

### Task 2: Define the provider contract and canonical DNS validation

**Files:**
- Create: `backend/app/dns_providers/base.py`
- Create: `backend/app/dns_providers/__init__.py`
- Create: `backend/tests/test_dns_record_normalization.py`
- Modify: `backend/requirements.txt`

**Interfaces:**
- Consumes: `DomainProvider` from Task 1 and provider-neutral Zone/record fields from the approved spec.
- Produces: `ProviderCredential`, `ZoneRef`, `RemoteRecordSet`, `RecordSetDraft`, `DnsProviderAdapter`, `ProviderRejectedError`, `ProviderUnavailableError`, `encode_record_key`, `decode_record_key`, `normalize_zone_name`, `normalize_owner_name`, `normalize_record_values`, `read_only_reason`, and `get_adapter(provider)`.

- [ ] **Step 1: Write failing canonicalization and read-only tests**

```python
def test_owner_and_value_normalization_accepts_at_relative_and_fqdn():
    assert normalize_owner_name("@", "example.com") == "example.com"
    assert normalize_owner_name("api", "example.com") == "api.example.com"
    assert normalize_owner_name("api.example.com.", "example.com") == "api.example.com"
    assert normalize_record_values("MX", ("10 mail.example.net.",)) == ("10 mail.example.net",)


@pytest.mark.parametrize("record_type,values", [
    ("A", ("999.0.0.1",)),
    ("AAAA", ("192.0.2.1",)),
    ("CNAME", ("a.example.com", "b.example.com")),
    ("SRV", ("10 5 70000 target.example.com",)),
])
def test_invalid_record_values_are_rejected(record_type, values):
    with pytest.raises(ValueError):
        normalize_record_values(record_type, values)


def test_read_only_rules_cover_system_advanced_and_ttl_boundaries():
    assert read_only_reason("NS", 300, {}) == "NS 记录不可编辑或删除"
    assert read_only_reason("A", None, {"alias": True}) == "Alias 记录不可编辑或删除"
    assert read_only_reason("TXT", 120, {}) == "TTL 不在可编辑范围内"
    assert read_only_reason("CAA", 300, {}) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_record_normalization.py -q`

Expected: FAIL because the provider contract module is absent.

- [ ] **Step 3: Implement the provider-neutral types, record-key codec, normalization rules, and adapter registry**

```python
WRITABLE_RECORD_TYPES = frozenset({"A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV"})
MIN_EDITABLE_TTL = 300
MAX_EDITABLE_TTL = 86400


def encode_record_key(locator: dict[str, object]) -> str:
    raw = json.dumps(locator, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    key = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if len(key) > 512:
        raise ValueError("服务商记录定位键过长")
    return key


def normalize_owner_name(owner: str, zone_name: str) -> str:
    name = owner.strip().rstrip(".").lower()
    zone = normalize_zone_name(zone_name)
    if name == "@":
        return zone
    full_name = name if name.endswith(f".{zone}") or name == zone else f"{name}.{zone}"
    if full_name != zone and not full_name.endswith(f".{zone}"):
        raise ValueError("记录所有权名称必须位于当前 Zone 内")
    return full_name
```

Use the standard library only for grammar checks: `ipaddress` for A/AAAA; a shared DNS-name validator for CNAME/MX/SRV targets; integer bounds for MX/SRV priority/weight/port and CAA flags; a one-value CNAME restriction; UTF-8, non-empty, no-NUL TXT validation while retaining the caller's raw line content. Reject duplicate canonical values. Return Chinese reasons exactly as the UI displays them. `read_only_reason()` must first protect `SOA`/`NS`, then Alias/advanced routing metadata, unsupported types, and TTL outside `300-86400`.

In the registry, construct one adapter per `DomainProvider` without creating SDK clients at import time:

```python
def get_adapter(provider: DomainProvider) -> DnsProviderAdapter:
    if provider is DomainProvider.AWS_ROUTE53:
        from app.dns_providers.aws_route53 import AwsRoute53Adapter
        return AwsRoute53Adapter()
    if provider is DomainProvider.TENCENT_DNSPOD:
        from app.dns_providers.tencent_dnspod import TencentDnsPodAdapter
        return TencentDnsPodAdapter()
    if provider is DomainProvider.GOOGLE_CLOUD_DNS:
        from app.dns_providers.google_cloud_dns import GoogleCloudDnsAdapter
        return GoogleCloudDnsAdapter()
    raise ValueError(f"未知 DNS 服务商: {provider}")
```

Append these pinned SDK dependencies in the existing dependency file:

```text
boto3==1.35.99
tencentcloud-sdk-python==3.0.1210
google-cloud-dns==0.35.0
```

- [ ] **Step 4: Run the canonicalization tests**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_record_normalization.py -q`

Expected: PASS; malformed values, out-of-zone owner names, duplicate values, and protected record kinds are rejected without any SDK call.

- [ ] **Step 5: Commit the provider foundation**

```bash
git add backend/app/dns_providers backend/tests/test_dns_record_normalization.py backend/requirements.txt
git commit -m "feat(domain): 定义 DNS 服务商适配契约"
```

### Task 3: Implement the AWS Route 53 adapter

**Files:**
- Create: `backend/app/dns_providers/aws_route53.py`
- Modify: `backend/app/dns_providers/__init__.py`
- Modify: `backend/tests/test_dns_providers.py`

**Interfaces:**
- Consumes: Task 2 adapter protocol, `ProviderCredential`, `ZoneRef`, `RemoteRecordSet`, `RecordSetDraft`, and provider exceptions.
- Produces: `AwsRoute53Adapter`, registered for `DomainProvider.AWS_ROUTE53`.

- [ ] **Step 1: Write failing Route 53 adapter tests with a fake boto3 client**

```python
@pytest.mark.asyncio
async def test_route53_discovers_only_public_zones_and_normalizes_name():
    client = FakeRoute53Client(hosted_zones=[
        {"Id": "/hostedzone/ZPUBLIC", "Name": "example.com.", "Config": {"PrivateZone": False}},
        {"Id": "/hostedzone/ZPRIVATE", "Name": "internal.example.", "Config": {"PrivateZone": True}},
    ])
    adapter = AwsRoute53Adapter(client_factory=lambda credential: client)

    zones = await adapter.discover_public_zones(AWS_CREDENTIAL)

    assert zones == [ZoneRef(DomainProvider.AWS_ROUTE53, "ZPUBLIC", "example.com")]


@pytest.mark.asyncio
async def test_route53_alias_and_weighted_records_are_read_only():
    adapter = AwsRoute53Adapter(client_factory=lambda credential: FakeRoute53Client(record_sets=[
        {"Name": "www.example.com.", "Type": "A", "AliasTarget": {"DNSName": "target."}},
        {"Name": "blue.example.com.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "192.0.2.1"}], "SetIdentifier": "blue", "Weight": 10},
    ]))

    records = await adapter.list_record_sets(AWS_ZONE, AWS_CREDENTIAL)

    assert [record.read_only_reason for record in records] == [
        "Alias 记录不可编辑或删除", "高级路由记录不可编辑或删除",
    ]
```

- [ ] **Step 2: Run the focused adapter tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_providers.py -q -k route53`

Expected: FAIL because `AwsRoute53Adapter` is not implemented.

- [ ] **Step 3: Implement pagination, canonical conversion, safe writes, and Route 53 change polling**

```python
def _route53_client(credential: ProviderCredential):
    return boto3.client(
        "route53",
        aws_access_key_id=credential.login_user,
        aws_secret_access_key=credential.secret,
    )


async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
    pages = await asyncio.to_thread(self._list_hosted_zone_pages, credential)
    return [
        ZoneRef(DomainProvider.AWS_ROUTE53, zone["Id"].rsplit("/", 1)[-1], normalize_zone_name(zone["Name"]))
        for page in pages
        for zone in page.get("HostedZones", [])
        if not zone.get("Config", {}).get("PrivateZone", False)
    ]
```

Use Route 53's `list_hosted_zones` and `list_resource_record_sets` paginators inside `asyncio.to_thread`. Build a locator with normalized name, type, and all routing selectors; decode it in `get_record_set`. Detect `AliasTarget`, `SetIdentifier`, `Failover`, `GeoLocation`, `Region`, `MultiValueAnswer`, and CIDR routing as non-simple metadata before calling `read_only_reason()`.

For mutation, submit a single `change_resource_record_sets` request using `CREATE`/`UPSERT`/`DELETE`, then poll `get_change(Id=change_id)` in `asyncio.to_thread` until `INSYNC` or a bounded timeout. A timeout, throttling exception, or connection error becomes `ProviderUnavailableError("AWS Route 53 暂时不可用")`; an auth, permission, or validation response becomes `ProviderRejectedError("AWS Route 53 拒绝该请求")`. Do not log or re-raise the raw boto exception.

- [ ] **Step 4: Run Route 53 adapter tests**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_providers.py -q -k route53`

Expected: PASS; public discovery, multipage record collection, protected routing detection, locator reads, UPSERT/DELETE request shapes, and `INSYNC` polling are covered by fakes.

- [ ] **Step 5: Commit the Route 53 adapter**

```bash
git add backend/app/dns_providers/aws_route53.py backend/app/dns_providers/__init__.py backend/tests/test_dns_providers.py
git commit -m "feat(domain): 接入 AWS Route 53 DNS 适配器"
```

### Task 4: Implement the Tencent Cloud DNSPod adapter

**Files:**
- Create: `backend/app/dns_providers/tencent_dnspod.py`
- Modify: `backend/app/dns_providers/__init__.py`
- Modify: `backend/tests/test_dns_providers.py`

**Interfaces:**
- Consumes: the Task 2 protocol and Tencent credential mapping `login_user=SecretId`, `secret=SecretKey`.
- Produces: `TencentDnsPodAdapter`, registered for `DomainProvider.TENCENT_DNSPOD`.

- [ ] **Step 1: Write failing DNSPod adapter tests**

```python
@pytest.mark.asyncio
async def test_dnspod_groups_simple_records_as_one_record_set():
    responder = FakeDnsPodResponder({
        "DescribeRecordList": {
            "RecordList": [
                {"RecordId": 10, "Name": "@", "Type": "A", "TTL": 300, "Value": "192.0.2.1", "Status": "ENABLE"},
                {"RecordId": 11, "Name": "@", "Type": "A", "TTL": 300, "Value": "192.0.2.2", "Status": "ENABLE"},
            ]
        }
    })
    adapter = TencentDnsPodAdapter(requester=responder)

    records = await adapter.list_record_sets(TENCENT_ZONE, TENCENT_CREDENTIAL)

    assert records[0].record_name == "example.com"
    assert records[0].values == ("192.0.2.1", "192.0.2.2")
    assert records[0].provider_meta["record_ids"] == [10, 11]


@pytest.mark.asyncio
async def test_dnspod_uses_only_public_domain_api():
    responder = FakeDnsPodResponder({"DescribeDomainList": {"DomainList": [{"DomainId": 21, "Name": "example.com"}]}})
    adapter = TencentDnsPodAdapter(requester=responder)

    zones = await adapter.discover_public_zones(TENCENT_CREDENTIAL)

    assert zones == [ZoneRef(DomainProvider.TENCENT_DNSPOD, "21", "example.com")]
    assert responder.actions == ["DescribeDomainList"]
```

- [ ] **Step 2: Run the DNSPod tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_providers.py -q -k dnspod`

Expected: FAIL because `TencentDnsPodAdapter` is not implemented.

- [ ] **Step 3: Implement public discovery, pagination, grouping, mutation, and convergence checks**

```python
async def _request(self, action: str, payload: dict[str, object], credential: ProviderCredential) -> dict[str, object]:
    return await asyncio.to_thread(self._requester, action, payload, credential)


async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
    offset = 0
    zones: list[ZoneRef] = []
    while True:
        body = await self._request("DescribeDomainList", {"Offset": offset, "Limit": 100}, credential)
        rows = body.get("DomainList", [])
        zones.extend(
            ZoneRef(DomainProvider.TENCENT_DNSPOD, str(row["DomainId"]), normalize_zone_name(row["Name"]))
            for row in rows
        )
        if len(rows) < 100:
            return zones
        offset += len(rows)
```

Use only the DNSPod public DNS API; never call Tencent Private DNS APIs. Page `DescribeRecordList`, normalize `@` using the Zone name, and group simple rows by name/type/TTL into one `RemoteRecordSet` with `record_ids` in its locator. If grouped rows have different TTLs, disabled state, line/monitor/weight routing, or provider policy fields that prevent a whole-set replacement, mark that set as `高级路由记录不可编辑或删除`.

For a simple create, create each canonical value with `CreateRecord`; for replacement, delete the old `record_ids` with `DeleteRecord`/`DeleteRecordBatch` then create all new values; for deletion, delete the grouped IDs. After every successful DNSPod mutation, poll the record-set read path until the canonical remote state matches the requested create/replace/delete state. If an operation returns an error after one or more known writes, raise one sanitized `ProviderUnavailableError` and let Task 8's immediate sync reveal the authoritative partial remote state; never repeat the write and never attempt an unapproved rollback.

- [ ] **Step 4: Run DNSPod adapter tests**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_providers.py -q -k dnspod`

Expected: PASS; tests cover public-only discovery, record pagination/grouping, non-simple read-only marking, normalized record locators, and bounded convergence polling.

- [ ] **Step 5: Commit the DNSPod adapter**

```bash
git add backend/app/dns_providers/tencent_dnspod.py backend/app/dns_providers/__init__.py backend/tests/test_dns_providers.py
git commit -m "feat(domain): 接入腾讯云 DNSPod 适配器"
```

### Task 5: Implement the Google Cloud DNS adapter

**Files:**
- Create: `backend/app/dns_providers/google_cloud_dns.py`
- Modify: `backend/app/dns_providers/__init__.py`
- Modify: `backend/tests/test_dns_providers.py`

**Interfaces:**
- Consumes: the Task 2 protocol and a `secret_file` credential containing Service Account JSON.
- Produces: `GoogleCloudDnsAdapter`, registered for `DomainProvider.GOOGLE_CLOUD_DNS`.

- [ ] **Step 1: Write failing Google Cloud DNS adapter tests**

```python
@pytest.mark.asyncio
async def test_google_discovers_public_managed_zones_only():
    client = FakeGoogleDnsClient(managed_zones=[
        {"name": "public-zone", "dnsName": "example.com.", "visibility": "public"},
        {"name": "private-zone", "dnsName": "corp.example.", "visibility": "private"},
    ])
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: client)

    zones = await adapter.discover_public_zones(GOOGLE_CREDENTIAL)

    assert zones == [ZoneRef(DomainProvider.GOOGLE_CLOUD_DNS, "public-zone", "example.com")]


@pytest.mark.asyncio
async def test_google_replace_submits_one_change_and_waits_for_done():
    client = FakeGoogleDnsClient(change_statuses=["pending", "done"])
    adapter = GoogleCloudDnsAdapter(client_factory=lambda credential: client)

    await adapter.replace_simple_record_set(GOOGLE_ZONE, GOOGLE_BEFORE_A, GOOGLE_AFTER_A, GOOGLE_CREDENTIAL)

    assert client.change.deleted == [("api.example.com.", "A", 300, ["192.0.2.1"])]
    assert client.change.added == [("api.example.com.", "A", 300, ["192.0.2.2"])]
```

- [ ] **Step 2: Run the Google adapter tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_providers.py -q -k google`

Expected: FAIL because `GoogleCloudDnsAdapter` is not implemented.

- [ ] **Step 3: Implement service-account construction, public filtering, record conversion, and change polling**

```python
def _client_from_credential(credential: ProviderCredential):
    info = json.loads(credential.secret)
    credentials = service_account.Credentials.from_service_account_info(info)
    return dns.Client(project=info["project_id"], credentials=credentials)


async def discover_public_zones(self, credential: ProviderCredential) -> list[ZoneRef]:
    zones = await asyncio.to_thread(lambda: list(self._client_factory(credential).list_zones()))
    return [
        ZoneRef(DomainProvider.GOOGLE_CLOUD_DNS, zone.name, normalize_zone_name(zone.dns_name))
        for zone in zones
        if zone.visibility == "public"
    ]
```

Keep the parsed Service Account object only in process memory; do not write a temporary JSON file. Through `asyncio.to_thread`, call `dns.Client.list_zones()`, `client.zone(remote_zone_id).list_resource_record_sets()`, `zone.resource_record_set()`, and the `zone.changes()` object API. Mark `SOA`, `NS`, unsupported types, out-of-range TTL, and metadata-bearing advanced records read-only. For replacement, call `change.delete_record_set(before_set)`, `change.add_record_set(after_set)`, then `change.create()` once and poll `change.reload()` until status is `done` before returning. Convert JSON parsing/auth/permission errors to `ProviderRejectedError`; convert timeout, retryable HTTP status, and bounded polling timeout to `ProviderUnavailableError`, without including the original SDK exception text.

- [ ] **Step 4: Run Google and full provider tests**

Working directory: `backend`

Run: `python -m pytest tests/test_dns_providers.py -q`

Expected: PASS; all three adapter implementations use fake SDK clients only, filter private Zones, normalize records, and never expose credential content.

- [ ] **Step 5: Commit the Google adapter**

```bash
git add backend/app/dns_providers/google_cloud_dns.py backend/app/dns_providers/__init__.py backend/tests/test_dns_providers.py
git commit -m "feat(domain): 接入 Google Cloud DNS 适配器"
```

### Task 6: Add domain permissions and Zone-aware credential protection

**Files:**
- Modify: `backend/app/core/constants.py`
- Modify: `backend/app/services/credential_service.py`
- Modify: `backend/tests/test_rbac_api.py`
- Modify: `backend/tests/test_job_api.py`

**Interfaces:**
- Consumes: `DnsZone` from Task 1 and existing permission seed behavior.
- Produces: `domain:read`, `domain:write`, `domain:delete` permission records; a `42201` credential deletion guard containing the affected Zone count.

- [ ] **Step 1: Write failing RBAC and deletion-protection tests**

```python
async def test_domain_permissions_are_admin_only_by_default(client):
    admin = (await client.get("/api/v1/auth/me", headers=auth_header(await login_for_tokens(client, "admin")))).json()["data"]
    ops = (await client.get("/api/v1/auth/me", headers=auth_header(await login_for_tokens(client, "ops1")))).json()["data"]

    assert {"domain:read", "domain:write", "domain:delete"}.issubset(admin["permissions"])
    assert not {"domain:read", "domain:write", "domain:delete"}.intersection(ops["permissions"])


async def test_credential_referenced_by_zone_cannot_be_deleted(client, db_factory):
    headers = auth_header(await login_for_tokens(client, "admin"))
    credential_id = await _create_credential(client, headers, name="route53-prod")
    async with db_factory() as session:
        session.add(DnsZone(provider="aws_route53", remote_zone_id="Z123", zone_name="example.com", credential_id=credential_id))
        await session.commit()

    response = await client.delete(f"/api/v1/credentials/{credential_id}", headers=headers)
    assert response.json()["code"] == 42201
    assert "1 个 Zone" in response.json()["message"]
```

- [ ] **Step 2: Run the tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_rbac_api.py tests/test_job_api.py -q -k "domain_permissions or referenced_by_zone"`

Expected: FAIL because neither the default permissions nor the DNS Zone deletion guard exists.

- [ ] **Step 3: Add the permission definitions and credential guard**

```python
("domain:read", "域名管理查看", "domain"),
("domain:write", "域名管理操作", "domain"),
("domain:delete", "域名管理删除", "domain"),
```

Add the three values to `PERMISSIONS`; because `BUILTIN_ROLES["admin"]["permissions"]` derives from `PERMISSIONS`, do not add them to `ops`, `approver`, or `auditor`. In `delete_credential()`, query `select(func.count()).select_from(DnsZone).where(DnsZone.credential_id == credential_id)` before deleting and reject a nonzero count:

```python
zone_count = (await session.execute(
    select(func.count()).select_from(DnsZone).where(DnsZone.credential_id == credential_id)
)).scalar_one()
if zone_count:
    raise Errors.rejected(f"凭据被 {zone_count} 个 Zone 引用，无法删除")
```

- [ ] **Step 4: Run focused RBAC and credential tests**

Working directory: `backend`

Run: `python -m pytest tests/test_rbac_api.py tests/test_job_api.py -q -k "domain_permissions or referenced_by_zone"`

Expected: PASS; default role assignment stays least-privilege and referenced credentials cannot be removed.

- [ ] **Step 5: Commit the security boundary**

```bash
git add backend/app/core/constants.py backend/app/services/credential_service.py backend/tests/test_rbac_api.py backend/tests/test_job_api.py
git commit -m "feat(domain): 增加域名权限与凭据删除保护"
```

### Task 7: Implement secure credential selection, Zone discovery/binding, leases, and snapshot synchronization

**Files:**
- Create: `backend/app/services/domain_service.py`
- Create: `backend/tests/test_domain_service.py`
- Modify: `backend/app/schemas/domain.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 adapter contract, Task 6 permissions/credential rules, `decrypt_text`, and `audit.log`.
- Produces: `DomainAuditContext`, `list_compatible_credentials`, `discover_zones`, `bind_zones`, `list_zones`, `get_zone`, `update_zone_description`, `unbind_zone`, `sync_zone`, `list_record_sets`, and the internal `_sync_zone_under_lease` helper used by Task 8.

```python
class ZoneBindSelection(BaseModel):
    remote_zone_id: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=255)


class ZoneBindResult(BaseModel):
    remote_zone_id: str
    zone_id: int | None = None
    status: Literal["success", "skipped", "failed"]
    reason: str | None = None


@dataclass(frozen=True)
class DomainAuditContext:
    actor_id: int
    actor_name: str
    source_ip: str | None
```

- [ ] **Step 1: Write failing discovery, initial-bind, lease, and snapshot tests**

```python
@pytest.mark.asyncio
async def test_initial_bind_persists_zone_and_records_only_after_full_sync(db_factory, monkeypatch):
    adapter = FakeAdapter(records=[REMOTE_A, REMOTE_TXT])
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: adapter)
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "aws", "username_password")
        result = await domain_service.bind_zones(
            session, provider=DomainProvider.AWS_ROUTE53, credential_id=credential.id,
            selections=[ZoneBindSelection(remote_zone_id="Z1", description="生产")],
            created_by=1, audit_context=AUDIT_CONTEXT,
        )

        assert result[0].status == "success"
        assert (await session.execute(select(func.count()).select_from(DnsZone))).scalar_one() == 1
        assert (await session.execute(select(func.count()).select_from(DnsRecordSet))).scalar_one() == 2


@pytest.mark.asyncio
async def test_initial_bind_failure_leaves_no_zone_or_snapshot(db_factory, monkeypatch):
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: FakeAdapter(list_error=ProviderUnavailableError("x")))
    async with db_factory() as session:
        credential = await _create_cloud_credential(session, "aws", "username_password")
        result = await domain_service.bind_zones(
            session, provider=DomainProvider.AWS_ROUTE53, credential_id=credential.id,
            selections=[ZoneBindSelection(remote_zone_id="Z1", description=None)],
            created_by=1, audit_context=AUDIT_CONTEXT,
        )

        assert result[0].status == "failed"
        assert (await session.execute(select(func.count()).select_from(DnsZone))).scalar_one() == 0


@pytest.mark.asyncio
async def test_sync_rejects_active_lease_and_preserves_snapshot_on_read_failure(db_factory):
    zone = await _seed_zone_with_record(db_factory, operation_expires_at=datetime.now() + timedelta(minutes=2))
    async with db_factory() as session:
        with pytest.raises(BizError) as exc:
            await domain_service.sync_zone(session, zone.id, AUDIT_CONTEXT)

    assert exc.value.code == 40901
```

- [ ] **Step 2: Run the service tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_service.py -q -k "initial_bind or sync_rejects"`

Expected: FAIL because the Domain service and request schemas do not exist.

- [ ] **Step 3: Implement safe credential resolution and compatible-credential listing**

```python
COMPATIBLE_AUTH_TYPES = {
    DomainProvider.AWS_ROUTE53: CredentialAuthType.USERNAME_PASSWORD.value,
    DomainProvider.TENCENT_DNSPOD: CredentialAuthType.USERNAME_PASSWORD.value,
    DomainProvider.GOOGLE_CLOUD_DNS: CredentialAuthType.SECRET_FILE.value,
}


async def resolve_provider_credential(session: AsyncSession, provider: DomainProvider, credential_id: int) -> ProviderCredential:
    credential = await get_credential_or_404(session, credential_id)
    if credential.auth_type != COMPATIBLE_AUTH_TYPES[provider]:
        raise Errors.rejected("所选凭据类型与 DNS 服务商不兼容")
    return ProviderCredential(
        provider=provider,
        credential_id=credential.id,
        login_user=credential.login_user,
        secret=decrypt_text(credential.secret_enc),
    )
```

`list_compatible_credentials()` must return only `id`, `name`, `auth_type`, and `description`, never `login_user`, `file_name`, ciphertext, or plaintext. It is called only from a `domain:write` endpoint in Task 9, so it must not reuse the broader `/credentials` permission path.

- [ ] **Step 4: Implement leases and snapshot synchronization without holding cloud calls in a transaction**

```python
async def acquire_zone_lease(session: AsyncSession, zone_id: int, kind: str) -> str:
    now = datetime.now()
    token = str(uuid4())
    changed = await session.execute(
        update(DnsZone)
        .where(
            DnsZone.id == zone_id,
            or_(DnsZone.operation_expires_at.is_(None), DnsZone.operation_expires_at <= now),
        )
        .values(
            operation_token=token,
            operation_kind=kind,
            operation_expires_at=now + timedelta(minutes=2),
            sync_status="syncing" if kind == "sync" else DnsZone.sync_status,
        )
    )
    await session.commit()
    if changed.rowcount != 1:
        raise Errors.conflict("Zone 正在同步或变更，请稍后重试")
    return token
```

After the lease commit, load remote pages through the adapter with at most two retries for `ProviderUnavailableError` on discovery/list operations only. On complete success, use one local database transaction to upsert every `DnsRecordSet`, delete absent snapshot rows, set `record_count`, clear `last_sync_error`, set `last_synced_at`, and mark `sync_status="success"`. On any read failure, keep record rows untouched, write only `sync_status="failed"` plus a short sanitized summary, and then release the lease conditionally by matching its token. The release update must never clear another request's token.

For binding, rediscover the selected public IDs immediately before syncing. Persist a Zone and its records only after its full adapter list call succeeds. Return one `ZoneBindResult` per submitted remote ID with `success`, `skipped`, or `failed`, never fail the whole batch because another selected Zone failed. On a uniqueness race, return `skipped` and leave the existing description unchanged.

- [ ] **Step 5: Add the remaining service tests and run them**

```python
@pytest.mark.asyncio
async def test_failed_sync_keeps_old_records_and_sets_sanitized_status(db_factory, monkeypatch):
    zone = await _seed_zone_with_record(db_factory, values=["192.0.2.1"])
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: FakeAdapter(list_error=ProviderUnavailableError("raw secret must not escape")))

    async with db_factory() as session:
        with pytest.raises(BizError) as exc:
            await domain_service.sync_zone(session, zone.id, AUDIT_CONTEXT)
        record = (await session.execute(select(DnsRecordSet).where(DnsRecordSet.zone_id == zone.id))).scalar_one()
        refreshed_zone = await session.get(DnsZone, zone.id)

    assert exc.value.code == 50201
    assert record.values == ["192.0.2.1"]
    assert refreshed_zone.sync_status == "failed"
    assert "secret" not in (refreshed_zone.last_sync_error or "").lower()
```

Working directory: `backend`

Run: `python -m pytest tests/test_domain_service.py -q`

Expected: PASS; discovery receives only compatible decrypted credentials, no secret reaches a result/audit payload, initial binding is atomic per Zone, leases reject overlap, and failed reads preserve snapshots.

- [ ] **Step 6: Commit Zone service behavior**

```bash
git add backend/app/services/domain_service.py backend/app/schemas/domain.py backend/tests/test_domain_service.py
git commit -m "feat(domain): 实现 Zone 发现绑定与快照同步"
```

### Task 8: Implement record-set validation, drift protection, and write-after-sync behavior

**Files:**
- Modify: `backend/app/services/domain_service.py`
- Modify: `backend/app/schemas/domain.py`
- Modify: `backend/tests/test_domain_service.py`

**Interfaces:**
- Consumes: `_sync_zone_under_lease`, `RemoteRecordSet`, `RecordSetDraft`, normalization helpers, and lease APIs from Task 7.
- Produces: `create_record_set`, `update_record_set`, `delete_record_set`, and a record list filter accepting `keyword`, `record_type`, and `read_only`.

- [ ] **Step 1: Write failing drift, validation, read-only, and post-write sync tests**

```python
@pytest.mark.asyncio
async def test_update_rejects_remote_drift_before_provider_write(db_factory, monkeypatch):
    zone, record = await _seed_editable_a_record(db_factory, values=["192.0.2.1"])
    adapter = FakeAdapter(remote_record=replace(REMOTE_A, values=("192.0.2.99",)))
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: adapter)

    async with db_factory() as session:
        with pytest.raises(BizError) as exc:
            await domain_service.update_record_set(
                session, zone.id, record.id,
                RecordSetDraft("api.example.com", "A", 300, ("192.0.2.2",)), AUDIT_CONTEXT,
            )

    assert exc.value.code == 40901
    assert adapter.replace_calls == []


@pytest.mark.asyncio
async def test_successful_remote_write_then_sync_failure_keeps_old_snapshot_and_audits_both_outcomes(db_factory, monkeypatch):
    zone, record = await _seed_editable_a_record(db_factory, values=["192.0.2.1"])
    adapter = FakeAdapter(remote_record=REMOTE_A, list_error_after_write=ProviderUnavailableError("network"))
    monkeypatch.setattr(domain_service, "get_adapter", lambda provider: adapter)

    async with db_factory() as session:
        with pytest.raises(BizError) as exc:
            await domain_service.delete_record_set(session, zone.id, record.id, AUDIT_CONTEXT)

    assert exc.value.code == 50201
    assert adapter.delete_calls == 1
    assert _queued_actions() == [("record.delete", "success"), ("zone.sync", "failed")]
```

- [ ] **Step 2: Run the tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_service.py -q -k "drift or post_write"`

Expected: FAIL because record write services do not exist.

- [ ] **Step 3: Implement the common write flow and immutable record identity**

```python
async def _ensure_remote_matches_snapshot(
    adapter: DnsProviderAdapter, zone: ZoneRef, record: DnsRecordSet, credential: ProviderCredential,
) -> RemoteRecordSet:
    remote = await adapter.get_record_set(zone, record.record_key, credential)
    if remote is None or not _same_record_snapshot(remote, record):
        raise Errors.conflict("DNS 记录已在服务商侧变更，请先手动同步")
    return remote
```

For create, normalize the owner name against the Zone, validate the type/TTL/values, acquire a `record.create` lease, and require `get_record_set()` to return `None` before calling `create_simple_record_set()`. For update/delete, load the local record by numeric ID, reject `read_only=True`, acquire a `record.update` or `record.delete` lease, and compare canonical remote state before calling the adapter.

Keep `record_name` and `record_type` immutable for an update; update accepts only `ttl` and `values`. A rename or type change is represented by an explicitly confirmed delete followed by an explicitly confirmed create, which preserves the adapter's stable locator contract. `RecordSetDraft` validation must reject any unsupported type, TTL outside `300-86400`, empty/duplicate values, and out-of-zone names before a remote read.

- [ ] **Step 4: Implement audit-safe success/failure semantics and reuse the existing lease for immediate sync**

```python
try:
    await adapter.replace_simple_record_set(zone_ref, remote_before, draft, credential)
except ProviderRejectedError as exc:
    _audit_record("record.update", "failed", before=before_payload, after=after_payload, error=str(exc))
    raise Errors.rejected("DNS 服务商拒绝该记录变更") from None

_audit_record("record.update", "success", before=before_payload, after=after_payload)
await _sync_zone_under_lease(session, zone, lease_token, credential, audit_context)
```

`_audit_record()` must whitelist only provider, Zone ID/name, record name/type, TTL, values, and sanitized result text. Do not include `provider_meta`, credential fields, exception objects, SDK headers, signatures, or raw request/response payloads. When immediate synchronization fails after a successful remote write, leave the old `DnsRecordSet` snapshot in place, mark the Zone failed, enqueue `record.*` success then `zone.sync` failed, release the lease, and raise `Errors.upstream("远端记录已变更，但快照同步失败，请手动同步")`.

- [ ] **Step 5: Run all Domain service tests**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_service.py tests/test_dns_record_normalization.py -q`

Expected: PASS; a provider change blocks local writes, protected records never reach adapters, valid writes are followed by snapshot sync, and the uncertain post-write state is auditable without rollback.

- [ ] **Step 6: Commit record-write safety behavior**

```bash
git add backend/app/services/domain_service.py backend/app/schemas/domain.py backend/tests/test_domain_service.py
git commit -m "feat(domain): 支持安全的 DNS 记录集变更"
```

### Task 9: Expose the Domain REST API with permission and response-boundary tests

**Files:**
- Create: `backend/app/api/v1/domains.py`
- Create: `backend/tests/test_domain_api.py`
- Modify: `backend/app/schemas/domain.py`
- Modify: `backend/app/api/v1/__init__.py`

**Interfaces:**
- Consumes: Task 7/8 Domain service APIs and `DomainAuditContext(actor.id, actor.username, get_client_ip(request))`.
- Produces: the API paths below, all using current response envelopes and `require_perm` dependencies.

| Method | Path | Permission | Service call |
| --- | --- | --- | --- |
| GET | `/domains/credentials` | `domain:write` | `list_compatible_credentials` |
| POST | `/domains/zones/discover` | `domain:write` | `discover_zones` |
| POST | `/domains/zones` | `domain:write` | `bind_zones` |
| GET | `/domains/zones` | `domain:read` | `list_zones` |
| GET | `/domains/zones/{zone_id}` | `domain:read` | `get_zone` |
| PUT | `/domains/zones/{zone_id}` | `domain:write` | `update_zone_description` |
| DELETE | `/domains/zones/{zone_id}` | `domain:delete` | `unbind_zone` |
| POST | `/domains/zones/{zone_id}/sync` | `domain:write` | `sync_zone` |
| GET | `/domains/zones/{zone_id}/records` | `domain:read` | `list_record_sets` |
| POST | `/domains/zones/{zone_id}/records` | `domain:write` | `create_record_set` |
| PUT | `/domains/zones/{zone_id}/records/{record_id}` | `domain:write` | `update_record_set` |
| DELETE | `/domains/zones/{zone_id}/records/{record_id}` | `domain:delete` | `delete_record_set` |

- [ ] **Step 1: Write failing API contract and authorization tests**

```python
async def test_domain_write_user_can_list_compatible_credentials_without_secret_read(client, db_factory, monkeypatch):
    user_headers = await _make_user_with_permissions(client, db_factory, {"domain:write"})
    await _seed_cloud_credentials(db_factory)

    response = await client.get(
        "/api/v1/domains/credentials", params={"provider": "aws_route53"}, headers=user_headers,
    )

    item = response.json()["data"]["items"][0]
    assert response.json()["code"] == 0
    assert item["auth_type"] == "username_password"
    assert set(item) == {"id", "name", "auth_type", "description"}


async def test_domain_routes_enforce_separate_read_write_delete_permissions(client):
    ops_headers = auth_header(await login_for_tokens(client, "ops1"))

    assert (await client.get("/api/v1/domains/zones", headers=ops_headers)).json()["code"] == 40301
    assert (await client.post("/api/v1/domains/zones/discover", headers=ops_headers, json={"provider": "aws_route53", "credential_id": 1})).json()["code"] == 40301
    assert (await client.delete("/api/v1/domains/zones/1", headers=ops_headers)).json()["code"] == 40301
```

- [ ] **Step 2: Run the API tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_api.py -q`

Expected: FAIL because `/domains` is not registered.

- [ ] **Step 3: Define request models and add the router in static-path-first order**

```python
class ZoneDiscoverRequest(BaseModel):
    provider: DomainProvider
    credential_id: int


class ZoneBindRequest(ZoneDiscoverRequest):
    selections: list[ZoneBindSelection] = Field(min_length=1, max_length=100)


class RecordSetCreateRequest(BaseModel):
    owner_name: str = Field(min_length=1, max_length=253)
    record_type: Literal["A", "AAAA", "CNAME", "MX", "TXT", "CAA", "SRV"]
    ttl: int = Field(default=300, ge=300, le=86400)
    values: list[str] = Field(min_length=1)


class RecordSetUpdateRequest(BaseModel):
    ttl: int = Field(ge=300, le=86400)
    values: list[str] = Field(min_length=1)
```

Declare `/credentials`, `/zones/discover`, `/zones/import-template`, `/zones/import`, and `/zones/export` before `GET /zones/{zone_id}`. Every serializer must explicitly choose fields: Zone list/detail returns `id`, provider, remote ID, Zone name, safe credential name, description, record count, status, last sync time/error, and timestamps; a record returns ID, name, type, TTL, values, read-only state/reason, and timestamps. Do not serialize `provider_meta` through the public API.

- [ ] **Step 4: Add full REST behavior tests and implement thin endpoints**

```python
async def test_discover_bind_sync_and_record_drift_contract(client, fake_adapter, admin_headers):
    discovered = await client.post("/api/v1/domains/zones/discover", headers=admin_headers, json={
        "provider": "aws_route53", "credential_id": fake_adapter.credential_id,
    })
    assert discovered.json()["data"]["items"] == [{"remote_zone_id": "Z1", "zone_name": "example.com"}]

    bound = await client.post("/api/v1/domains/zones", headers=admin_headers, json={
        "provider": "aws_route53", "credential_id": fake_adapter.credential_id,
        "selections": [{"remote_zone_id": "Z1", "description": "生产 Zone"}],
    })
    zone_id = bound.json()["data"]["items"][0]["zone_id"]
    assert bound.json()["data"]["items"][0]["status"] == "success"

    drifted = await client.put(f"/api/v1/domains/zones/{zone_id}/records/1", headers=admin_headers, json={"ttl": 300, "values": ["192.0.2.2"]})
    assert drifted.json()["code"] == 40901
```

Patch `domain_service.get_adapter` with the test fake; do not inject actual SDK credentials. Include tests for list filtering, description-only update, unbind deleting only local rows, read-only record rejection, paging/filtering records, `42201` credential mismatch, `50201` sanitized provider failure, and API output lacking `secret`, `secret_enc`, `login_user`, and `provider_meta`.

- [ ] **Step 5: Run focused API and security tests**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_api.py tests/test_rbac_api.py tests/test_job_api.py -q`

Expected: PASS; static endpoints resolve correctly, permissions are enforced server-side, API responses are secret-free, and remote failures remain sanitized.

- [ ] **Step 6: Commit the Domain API**

```bash
git add backend/app/api/v1/domains.py backend/app/api/v1/__init__.py backend/app/schemas/domain.py backend/tests/test_domain_api.py
git commit -m "feat(domain): 提供域名管理 REST 接口"
```

### Task 10: Add Zone Excel import/export and global search

**Files:**
- Create: `backend/app/services/domain_excel.py`
- Create: `backend/tests/test_domain_excel.py`
- Modify: `backend/app/api/v1/domains.py`
- Modify: `backend/app/services/domain_service.py`
- Modify: `backend/app/services/search_service.py`
- Modify: `backend/tests/test_domain_api.py`
- Modify: `backend/tests/test_search_api.py`

**Interfaces:**
- Consumes: `ZoneBindSelection`, `bind_zones`, `DnsZone`, `DnsRecordSet`, and `Credential` safe metadata.
- Produces: `build_zone_import_template()`, `parse_zone_import_rows()`, `export_zones()`, `import_zones()`, `search_zones()`, `/domains/zones/import-template`, `/domains/zones/import`, `/domains/zones/export`, and global-search key `domains`.

```python
@dataclass(frozen=True)
class ZoneExportRow:
    provider: str
    credential_name: str
    zone_name: str
    remote_zone_id: str
    description: str | None
    record_count: int
    sync_status: str
    last_synced_at: datetime | None
    last_sync_error: str | None


@dataclass(frozen=True)
class RecordExportRow:
    zone_name: str
    provider: str
    record_name: str
    record_type: str
    ttl: int | None
    values: list[str]
    read_only_reason: str | None
    last_synced_at: datetime | None
```

- [ ] **Step 1: Write failing workbook and global-search tests**

```python
async def test_zone_import_skips_existing_and_duplicate_file_rows(client, admin_headers, fake_adapter):
    file_bytes = _make_zone_xlsx([
        ["aws_route53", "route53-prod", "example.com", "Z1", "首次说明"],
        ["aws_route53", "route53-prod", "example.com", "Z1", "不得覆盖"],
    ])

    response = await client.post(
        "/api/v1/domains/zones/import", headers=admin_headers,
        files={"file": ("zones.xlsx", file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    data = response.json()["data"]
    assert data["success_count"] == 1
    assert data["skipped_rows"] == [{"row": 3, "reason": "文件内 Zone 重复"}]


async def test_domain_search_matches_only_zone_fields(client, db_factory, admin_headers):
    await _seed_zone_and_record(db_factory, zone_name="shop.example.com", description="电商生产", record_value="private-token-text")

    by_description = await client.get("/api/v1/search", params={"keyword": "电商"}, headers=admin_headers)
    by_record_value = await client.get("/api/v1/search", params={"keyword": "private-token-text"}, headers=admin_headers)

    assert by_description.json()["data"]["domains"]["total"] == 1
    assert by_record_value.json()["data"]["domains"]["total"] == 0
```

- [ ] **Step 2: Run the workbook/search tests to verify failure**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_excel.py tests/test_search_api.py -q -k "zone_import or domain_search"`

Expected: FAIL because the workbook service, import/export paths, and `domains` search segment are absent.

- [ ] **Step 3: Implement import parsing and import service orchestration**

```python
ZONE_IMPORT_COLUMNS = [
    ("服务商*", "provider", 20),
    ("凭据名称*", "credential_name", 24),
    ("Zone 名称*", "zone_name", 32),
    ("远端 Zone ID*", "remote_zone_id", 32),
    ("描述", "description", 36),
]
MAX_IMPORT_ROWS = 5000
```

`build_zone_import_template()` creates a `Zone 导入` worksheet with those exact five columns, existing blue header style, widths, and one removable example row. `parse_zone_import_rows()` reads rows 2 through 5001, rejects malformed workbook/empty required values, normalizes only cell whitespace, and returns row-numbered typed rows plus structural failures.

`import_zones()` groups valid rows by `(provider, credential_name)`, resolves the credential name once per group, discovers public Zones once per group, verifies both remote Zone ID and normalized Zone name, and invokes the same initial-bind behavior as Task 7. An already bound `(provider, remote_zone_id)` and a repeated file row are skips; neither changes description or triggers sync. Private/unavailable Zones, mismatched name/ID, incompatible credentials, and initial-sync failure are failed rows. Return:

```python
{
    "success_count": 0,
    "skipped_rows": [{"row": 2, "reason": "已绑定 Zone，已跳过"}],
    "failed_rows": [{"row": 3, "reason": "远端 Zone ID 与 Zone 名称不匹配"}],
}
```

- [ ] **Step 4: Implement two-sheet export and permission-gated Domain search**

```python
def export_zones(zones: list[ZoneExportRow], records: list[RecordExportRow]) -> bytes:
    workbook = Workbook(write_only=True)
    zone_sheet = workbook.create_sheet("Zone 台账")
    zone_sheet.append(["服务商", "凭据名称", "Zone 名称", "远端 Zone ID", "描述", "记录数", "同步状态", "最近同步时间", "错误摘要"])
    record_sheet = workbook.create_sheet("记录集")
    record_sheet.append(["Zone 名称", "服务商", "记录名称", "类型", "TTL", "值", "只读原因", "最近同步时间"])
    return _save_workbook(workbook)
```

Join `DnsZone` to `Credential` only to export the credential name. Write all TXT values joined by `\n`; never export any credential field except name. Add `search_zones(session, keyword, limit=5)` matching `DnsZone.zone_name`, `DnsZone.description`, or `DnsZone.provider`; `search_service.search()` adds `"domains": None` and populates it only for `domain:read`. Its item shape is `{id, zone_name, provider, description}`.

- [ ] **Step 5: Run Excel, API, and search tests**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_excel.py tests/test_domain_api.py tests/test_search_api.py -q`

Expected: PASS; import reports every row outcome, export contains exactly both worksheets and full TXT data, and global search never matches record values or exposes a Domain segment to an unauthorized user.

- [ ] **Step 6: Commit workbook and search support**

```bash
git add backend/app/services/domain_excel.py backend/app/services/domain_service.py backend/app/services/search_service.py backend/app/api/v1/domains.py backend/tests/test_domain_excel.py backend/tests/test_domain_api.py backend/tests/test_search_api.py
git commit -m "feat(domain): 支持 Zone 台账导入导出与全局搜索"
```

### Task 11: Build the Domain management frontend

**Files:**
- Create: `frontend/src/api/domain.ts`
- Create: `frontend/src/views/domain/DomainList.vue`
- Create: `frontend/src/views/domain/DomainDetail.vue`
- Modify: `frontend/src/router/index.ts`
- Modify: `frontend/src/layouts/BasicLayout.vue`
- Modify: `frontend/src/api/search.ts`

**Interfaces:**
- Consumes: the Task 9/10 API response shapes, `useUserStore().hasPerm`, existing `request`/`http`, `makeResizable`, and current Ant Design Vue table/modal conventions.
- Produces: routes `/domains` and `/domains/:id`, menu key `domains`, typed `DomainItem`, `DomainDetail`, `DnsRecordSet`, and `SearchResult.domains`.

- [ ] **Step 1: Add the typed Domain API client and prove type checking fails until views exist**

```ts
export interface DomainItem {
  id: number
  provider: 'tencent_dnspod' | 'aws_route53' | 'google_cloud_dns'
  remote_zone_id: string
  zone_name: string
  credential_name: string
  description: string | null
  record_count: number
  sync_status: 'success' | 'failed' | 'syncing'
  last_synced_at: string | null
  last_sync_error: string | null
}

export function listDomains(params: DomainQuery) {
  return request<PageResult<DomainItem>>({ url: '/domains/zones', method: 'get', params })
}

export function updateRecord(zoneId: number, recordId: number, data: RecordUpdateForm) {
  return request<null>({ url: `/domains/zones/${zoneId}/records/${recordId}`, method: 'put', data })
}

export interface DnsRecordSet {
  id: number
  record_name: string
  record_type: 'A' | 'AAAA' | 'CNAME' | 'MX' | 'TXT' | 'CAA' | 'SRV' | string
  ttl: number | null
  values: string[]
  read_only: boolean
  read_only_reason: string | null
}
```

Add API functions for credentials, discovery, bind, list/get/update/delete Zone, sync, list/create/update/delete record, import template, import, and export. Keep the module-local `downloadXlsx()` helper consistent with `cmdb.ts`; it must use `http.get(url, { params, responseType: 'blob' })` and revoke the object URL.

Working directory: `frontend`

Run: `npm.cmd run type-check`

Expected: FAIL after routes reference `@/views/domain/DomainList.vue` and `DomainDetail.vue` but before those files are added.

- [ ] **Step 2: Implement the Zone list and binding/import flows**

```ts
const canWrite = userStore.hasPerm('domain:write')
const canDelete = userStore.hasPerm('domain:delete')

async function onDiscover() {
  discovered.value = (await domainApi.discoverZones({
    provider: bindForm.provider,
    credential_id: bindForm.credential_id,
  })).items
}

async function onBind() {
  bindLoading.value = true
  try {
    bindResult.value = await domainApi.bindZones({
      provider: bindForm.provider,
      credential_id: bindForm.credential_id,
      selections: selectedRemoteZoneIds.value.map((remote_zone_id) => ({ remote_zone_id, description: bindForm.description || undefined })),
    })
    await loadList()
  } finally {
    bindLoading.value = false
  }
}
```

In `DomainList.vue`, use an existing-width Ant table with fixed action column and filters for keyword, provider, and sync status. Display Zone name, provider label, credential name, description, record count, sync status, last synced time, and the sanitized error summary. Support description-only edit, single-Zone manual sync, export, Excel import result tables with distinct skipped/failed rows, and local unbind confirmation. Do not show a remote Zone create/delete control.

The bind modal selects provider first, calls `/domains/credentials`, then calls discover only after credential selection. Display only discovered public Zones with checkbox selection; after submit, show each bind result. Disable write/delete controls based on their distinct permissions.

- [ ] **Step 3: Implement Zone detail, raw multiline values, and mandatory preview-confirmation actions**

```ts
function splitValues(value: string): string[] {
  return value.split(/\r?\n/).filter((line) => line.trim().length > 0)
}

function openRecordConfirm(kind: 'create' | 'update' | 'delete', record?: domainApi.DnsRecordSet) {
  pendingChange.value = {
    kind,
    before: record ? toPreview(record) : null,
    after: kind === 'delete' ? null : {
      record_name: record?.record_name ?? recordForm.owner_name,
      record_type: record?.record_type ?? recordForm.record_type,
      ttl: recordForm.ttl,
      values: splitValues(recordForm.values_text),
    },
  }
  confirmVisible.value = true
}
```

`DomainDetail.vue` loads Zone metadata and paged records by route ID, exposes name/type/read-only filters, and provides manual sync. Its record form keeps user input as a raw multiline `values_text` field and sends one nonblank line per value without trimming TXT content. New records default TTL to `300`; edits disable owner name and type to preserve record-set identity. Read-only rows show their reason and omit edit/delete actions.

Every create, update, and delete first opens a before/after confirmation modal. Confirming is the only code path that calls the write endpoint; lock its confirm button while the request is in flight. A `40901` response stays handled by the existing global error toast and leaves the form open so the operator can sync and compare again.

- [ ] **Step 4: Add routes, menu visibility, and Domain global-search rendering**

```ts
{
  path: 'domains',
  name: 'domains',
  component: () => import('@/views/domain/DomainList.vue'),
  meta: { title: '域名管理', perm: 'domain:read' },
},
{
  path: 'domains/:id',
  name: 'domain-detail',
  component: () => import('@/views/domain/DomainDetail.vue'),
  meta: { title: 'Zone 详情', perm: 'domain:read', menuKey: 'domains' },
},
```

Add a `GlobalOutlined` menu entry labeled `域名管理` under the main navigation with `domain:read`. Extend `SearchResult` with:

```ts
domains: SearchSegment<{ id: number; zone_name: string; provider: string; description: string | null }> | null
```

Update `searchEmpty` to include `r.domains`, add a `域名 Zone` group, and navigate each hit directly to `/domains/${item.id}` with `withKeyword=false`; record filters and values must never be passed through global search.

- [ ] **Step 5: Run frontend checks and manual UI acceptance**

Working directory: `frontend`

Run: `npm.cmd run type-check`

Expected: PASS with no TypeScript errors.

Working directory: `frontend`

Run: `npm.cmd run build`

Expected: PASS and emits the production bundle.

Start the existing frontend development server and manually verify, at desktop and narrow viewport widths, the menu permission state, discovery sequence, initial bind result table, list filters, export/import result tables, direct deep link `/domains/:id`, read-only record button suppression, and each before/after confirmation dialog.

- [ ] **Step 6: Commit the frontend module**

```bash
git add frontend/src/api/domain.ts frontend/src/views/domain/DomainList.vue frontend/src/views/domain/DomainDetail.vue frontend/src/router/index.ts frontend/src/layouts/BasicLayout.vue frontend/src/api/search.ts
git commit -m "feat(ui): 增加域名管理与 DNS 记录页面"
```

### Task 12: Synchronize documentation and run the full implementation verification

**Files:**
- Modify: `docs/01-产品需求文档PRD.md`
- Modify: `docs/02-技术架构设计.md`
- Modify: `docs/03-数据库设计.md`
- Modify: `docs/04-API设计.md`
- Modify: `docs/05-开发任务拆解.md`

**Interfaces:**
- Consumes: implemented endpoints, tables, permission constants, provider contract, test outcomes, and real non-production cloud test Zones.
- Produces: product and engineering documentation that describes only the delivered behavior, plus final verification evidence.

- [ ] **Step 1: Update each current-state document from the implementation rather than the proposal**

```markdown
| 权限 | 行为 |
| --- | --- |
| `domain:read` | 查看 Zone、记录快照和 Zone 导出 |
| `domain:write` | 发现、绑定、描述编辑、同步、导入与新建/修改记录集 |
| `domain:delete` | 解绑 Zone 与删除记录集 |
```

Document the two tables, unique keys, operation lease fields, no-physical-foreign-key decision, all REST paths, `50201`, credential compatibility rules, manual-only synchronization, provider-as-source-of-truth behavior, editable record types/TTL, drift `40901`, Excel columns/worksheets, and first-phase exclusions. Do not document scheduled sync, certificates, app linkage, remote Zone lifecycle, record import, advanced routing writes, or record-value global search.

- [ ] **Step 2: Run focused backend tests**

Working directory: `backend`

Run: `python -m pytest tests/test_domain_migration.py tests/test_dns_record_normalization.py tests/test_dns_providers.py tests/test_domain_service.py tests/test_domain_api.py tests/test_domain_excel.py tests/test_search_api.py tests/test_rbac_api.py tests/test_job_api.py -q`

Expected: PASS; all provider SDK interactions remain faked and no external credential is required.

- [ ] **Step 3: Run the complete backend and frontend suites**

Working directory: `backend`

Run: `python -m pytest -q`

Expected: PASS; record any unrelated pre-existing failure separately instead of weakening DNS tests.

Working directory: `frontend`

Run: `npm.cmd run type-check`

Expected: PASS.

Working directory: `frontend`

Run: `npm.cmd run build`

Expected: PASS.

- [ ] **Step 4: Verify migration and each provider against isolated non-production resources**

Run `alembic upgrade head` in an isolated Docker Compose environment, then confirm API and Worker health checks pass. For each provider, use a separate non-production public Zone and minimum-privilege credential to verify discovery, initial existing-record snapshot, create/update/delete only a disposable test record, external drift returning `40901`, manual sync, import, export, credential deletion protection, and local unbind. Delete only the disposable records created for this verification; do not use production DNS records as write-test targets.

- [ ] **Step 5: Commit documentation and verification-aligned changes**

```bash
git add docs/01-产品需求文档PRD.md docs/02-技术架构设计.md docs/03-数据库设计.md docs/04-API设计.md docs/05-开发任务拆解.md
git commit -m "docs(domain): 补充域名管理实施说明"
```

## Plan Self-Review

### Spec coverage

| Approved requirement | Planned task(s) |
| --- | --- |
| Zone-only asset ledger, description, snapshots, no physical foreign keys | Tasks 1 and 7 |
| Three real public-provider adapters and existing-record display | Tasks 2 through 5 and 7 |
| Existing encrypted credential reuse, safe compatible selection, deletion guard | Tasks 6, 7, and 9 |
| Manual synchronization, initial sync gate, leases, drift, and write/sync failure semantics | Tasks 7 and 8 |
| Read-only rules, supported simple types, owner/value grammar, TTL `300-86400` | Tasks 2 and 8 |
| Direct authorized writes with confirmation UI and full audit values | Tasks 8, 9, and 11 |
| Zone-only Excel import, two-sheet export, duplicate handling | Task 10 |
| Domain-only global-search fields and permission gate | Tasks 10 and 11 |
| Backend/API/RBAC tests, frontend checks, migration and non-production provider acceptance | Tasks 1 through 12 |
| Current-state documentation after runtime implementation | Task 12 |

### Placeholder scan

The plan contains no deferred implementation marker or unnamed interface. Every later task consumes an interface declared in the Shared Interfaces section or an explicitly named earlier task.

### Type consistency

- Provider adapters consume `ProviderCredential`, `ZoneRef`, `RemoteRecordSet`, and `RecordSetDraft` throughout Tasks 2 through 8.
- The persisted `DnsRecordSet` uses `record_key`, while adapters use the identically named opaque locator key to read the remote state.
- `domain_service` accepts Pydantic `ZoneBindSelection` and returns row-level `ZoneBindResult`; API and Excel tasks use those same names.
- The frontend transmits `RecordSetCreateRequest` fields for creates and `RecordSetUpdateRequest` fields for updates; updates intentionally do not carry owner/type.
