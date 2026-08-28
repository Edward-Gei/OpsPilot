"""credential_refs 补偿迁移的回归测试。"""
import importlib.util
from pathlib import Path


MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "0016_repair_credential_refs.py"


class _Result:
    def __init__(self, value: bool):
        self.value = value

    def scalar(self):
        return self.value


class _Bind:
    def __init__(self, existing_columns: set[tuple[str, str]]):
        self.existing_columns = existing_columns
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        if "information_schema.columns" in sql:
            return _Result((params["t"], params["c"]) in self.existing_columns)
        self.statements.append(sql)
        return _Result(False)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("repair_credential_refs", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_adds_missing_credential_ref_columns(monkeypatch):
    migration = _load_migration_module()
    bind = _Bind(set())
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "execute", bind.statements.append)

    migration.upgrade()

    assert bind.statements == [
        "ALTER TABLE ticket_template ADD COLUMN credential_refs JSON NULL "
        "COMMENT '引用凭据 [{alias, credential_id}]'",
        "ALTER TABLE ticket ADD COLUMN credential_refs JSON NULL "
        "COMMENT '引用凭据快照 [{alias, credential_id, credential_name}]'",
    ]


def test_upgrade_skips_credential_ref_columns_that_already_exist(monkeypatch):
    migration = _load_migration_module()
    bind = _Bind({("ticket_template", "credential_refs"), ("ticket", "credential_refs")})
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "execute", bind.statements.append)

    migration.upgrade()

    assert bind.statements == []
