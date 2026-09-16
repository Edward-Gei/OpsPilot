"""DNS Zone 快照模型与迁移的回归测试。"""
import importlib.util
from pathlib import Path

MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0019_domain_management.py"


class _Result:
    def __init__(self, value: bool):
        self.value = value

    def scalar(self):
        return self.value


class _Bind:
    def __init__(self, existing_tables: set[str]):
        self.existing_tables = existing_tables

    def execute(self, statement, params=None):
        assert "information_schema.tables" in str(statement)
        return _Result(params["t"] in self.existing_tables)


def _load_migration_module():
    assert MIGRATION_PATH.exists(), "0019 DNS Zone migration must exist"
    spec = importlib.util.spec_from_file_location("domain_management", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_domain_models_are_registered_in_metadata():
    from app.models import Base

    assert {"dns_zone", "dns_record_set"}.issubset(Base.metadata.tables)
    assert "uk_dns_zone_provider_remote" in {
        constraint.name for constraint in Base.metadata.tables["dns_zone"].constraints
    }


def test_domain_snapshot_contracts_and_upstream_error():
    from app.models import Base
    from app.core.constants import DnsSyncStatus, DomainProvider
    from app.core.response import BizError, Errors

    zone = Base.metadata.tables["dns_zone"]
    record_set = Base.metadata.tables["dns_record_set"]
    assert {"idx_dns_zone_credential", "idx_dns_zone_sync_status"} == {index.name for index in zone.indexes}
    assert {"idx_dns_record_set_zone", "idx_dns_record_set_zone_name_type"} == {
        index.name for index in record_set.indexes
    }
    assert zone.c.record_count.default.arg == 0
    assert zone.c.sync_status.default.arg == "success"
    assert record_set.c["values"].default.arg(None) == []
    assert record_set.c.provider_meta.default.arg(None) == {}
    assert [provider.value for provider in DomainProvider] == [
        "tencent_dnspod", "aws_route53", "google_cloud_dns"
    ]
    assert [status.value for status in DnsSyncStatus] == ["success", "failed", "syncing"]

    err = Errors.upstream("DNS provider unavailable")
    assert isinstance(err, BizError)
    assert (err.code, err.message, err.http_status) == (50201, "DNS provider unavailable", 502)


def test_upgrade_creates_only_missing_domain_tables(monkeypatch):
    migration = _load_migration_module()
    bind = _Bind(existing_tables={"dns_zone"})
    created: list[str] = []
    indexes: list[tuple] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "create_table", lambda name, *args, **kwargs: created.append(name))
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: indexes.append(args))

    migration.upgrade()

    assert created == ["dns_record_set"]
    assert indexes == [
        ("idx_dns_record_set_zone", "dns_record_set", ["zone_id"]),
        ("idx_dns_record_set_zone_name_type", "dns_record_set", ["zone_id", "record_name", "record_type"]),
    ]


def test_downgrade_drops_only_existing_domain_tables_in_dependency_order(monkeypatch):
    migration = _load_migration_module()
    bind = _Bind(existing_tables={"dns_zone", "dns_record_set"})
    dropped: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    migration.downgrade()

    assert dropped == ["dns_record_set", "dns_zone"]
