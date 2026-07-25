"""CQL migration manifest loading and statement splitting."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_cql_migrate() -> ModuleType:
    import sys

    path = REPO_ROOT / "deploy/scripts/cql_migrate.py"
    spec = importlib.util.spec_from_file_location("deploy.cql_migrate", path)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_manifest_has_streams_and_tiers() -> None:
    """Loads the real migration manifest with expected keyspace, streams, and a known prod migration."""
    cql_migrate = _load_cql_migrate()
    keyspace, migrations = cql_migrate.load_manifest(REPO_ROOT / "schema/migrations/manifest.toml")
    assert keyspace == "algorand_platform"
    streams = {m.stream for m in migrations}
    assert streams >= {"ledger", "chain", "app"}
    prod_app = [m for m in migrations if m.stream == "app" and m.tier == "prod"]
    assert any(m.version == "006" for m in prod_app)


def test_split_cql_strips_comments() -> None:
    """Splits a CQL script into statements and drops a leading line comment."""
    cql_migrate = _load_cql_migrate()
    statements = cql_migrate.split_cql_statements(
        "-- comment\nUSE ks;\n\nCREATE TABLE IF NOT EXISTS t (id text PRIMARY KEY);"
    )
    assert len(statements) == 2
    assert statements[0] == "USE ks"
    assert "CREATE TABLE" in statements[1]


def test_pending_respects_prod_tier() -> None:
    """Filtering pending migrations by the prod tier returns only prod-tier rows."""
    cql_migrate = _load_cql_migrate()
    keyspace, migrations = cql_migrate.load_manifest(REPO_ROOT / "schema/migrations/manifest.toml")
    assert keyspace
    pending = cql_migrate.pending_migrations(migrations, applied={}, tier_filter="prod")
    assert all(m.tier == "prod" for m in pending)
    assert any(m.stream == "app" and m.version == "006" for m in pending)
