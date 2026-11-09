"""真实 MySQL 完整迁移：当前空库，以及由提交前源码生成的旧结构升级。"""
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pymysql
import pytest

pytestmark = pytest.mark.integration


def _connect(database=None):
    return pymysql.connect(host=os.environ.get("MYSQL_HOST", "mysql"), user="root", password=os.environ.get("DATABASE_TEST_ROOT_PASSWORD", "local-fixture-root"), database=database, autocommit=True)


def _upgrade(database, source, revision="head"):
    environment = {**os.environ, "MYSQL_USER":"root", "MYSQL_PASSWORD":os.environ.get("DATABASE_TEST_ROOT_PASSWORD", "local-fixture-root"), "MYSQL_DB": database, "PYTHONPATH":str(source)}
    outcome = subprocess.run([sys.executable, "-m", "alembic", "upgrade", revision], cwd=source, env=environment, capture_output=True, text=True, timeout=120)
    assert outcome.returncode == 0, outcome.stderr[-10000:]


@pytest.mark.parametrize("baseline", [False, True], ids=["fresh-full-chain", "pre-feature-0025-upgrade"])
def test_database_real_migration_paths(baseline):
    if not os.environ.get("DATABASE_TEST_INTERNAL_URL"):
        pytest.skip("requires isolated MySQL fixture")
    old_source = Path(os.environ.get("DATABASE_TEST_OLD_SOURCE", "/baseline/backend"))
    if baseline and not (old_source / "alembic").exists():
        pytest.fail("missing archived pre-feature source; generate via run_checks.ps1")
    database = "db_migration_" + uuid.uuid4().hex[:16]
    with _connect() as admin:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4")
    try:
        if baseline:
            _upgrade(database, old_source, "0025")
            with _connect(database) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SHOW TABLES LIKE 'database_instance'")
                    assert cursor.fetchone() is None
                    cursor.execute("INSERT INTO application(name,deploy_type) VALUES ('migration-preserved-app','shell')")
        _upgrade(database, Path("/app"))
        _upgrade(database, Path("/app"))
        with _connect(database) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                assert cursor.fetchone()[0] == "0026"
                cursor.execute("SHOW TABLES LIKE 'database_%'")
                assert len(cursor.fetchall()) == 9
                if baseline:
                    cursor.execute("SELECT name FROM application WHERE name='migration-preserved-app'")
                    assert cursor.fetchone()[0] == "migration-preserved-app"
                cursor.execute("SHOW COLUMNS FROM config_task LIKE 'retry_count'")
                assert cursor.fetchone() is not None
    finally:
        with _connect() as admin:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")
