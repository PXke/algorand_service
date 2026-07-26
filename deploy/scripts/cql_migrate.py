#!/usr/bin/env python3
"""Apply versioned Cassandra CQL migrations tracked in schema_migrations.

See docs/architecture/cql-migrations.md and schema/migrations/manifest.yaml.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from cassandra.cluster import Session as CassandraSession

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "schema" / "migrations" / "manifest.toml"


class Migration(NamedTuple):
    """One discovered migration file: its stream, version, path, and prod tier."""

    stream: str
    version: str
    file: Path
    tier: str
    status: str
    description: str

    @property
    def key(self) -> tuple[str, str]:
        """This migration's identity in the applied-migrations ledger."""
        return (self.stream, self.version)


def load_manifest(path: Path) -> tuple[str, list[Migration]]:
    """Parse manifest.toml into its keyspace name and ordered migration list."""
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    keyspace = str(raw.get("keyspace", "algorand_platform"))
    items: list[Migration] = []
    for entry in raw.get("migrations", []):
        rel = Path(entry["file"])
        items.append(
            Migration(
                stream=str(entry["stream"]),
                version=str(entry["version"]),
                file=REPO_ROOT / rel,
                tier=str(entry.get("tier", "prod")),
                status=str(entry.get("status", "active")),
                description=str(entry.get("description", "")),
            )
        )
    return keyspace, items


def checksum_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a migration file's bytes."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def split_cql_statements(cql: str) -> list[str]:
    """Split a CQL file's text into individual statements, stripping '--' comment lines."""
    statements: list[str] = []
    for part in cql.split(";"):
        lines = []
        for line in part.splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            lines.append(line)
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)
    return statements


def connect_cluster(hosts: list[str], keyspace: str) -> CassandraSession:
    """Connect to the Cassandra cluster and return a session bound to keyspace."""
    try:
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import EXEC_PROFILE_DEFAULT, Cluster, ExecutionProfile
        from cassandra.policies import DCAwareRoundRobinPolicy
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "cassandra-driver required: pip install cassandra-driver (or run from backend venv)"
        ) from exc

    # Ensure hosts is not empty
    if not hosts:
        hosts = ["cassandra"]

    local_dc = os.getenv("CASSANDRA_LOCAL_DC", "datacenter1")
    username = os.getenv("CASSANDRA_USERNAME", "")
    auth_provider = (
        PlainTextAuthProvider(username=username, password=os.getenv("CASSANDRA_PASSWORD", ""))
        if username
        else None
    )
    profile = ExecutionProfile(
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=local_dc),
    )
    cluster = Cluster(
        hosts,
        execution_profiles={EXEC_PROFILE_DEFAULT: profile},
        auth_provider=auth_provider,
    )
    return cluster.connect(keyspace)


def fetch_applied(session: CassandraSession) -> dict[tuple[str, str], dict]:
    """Return the applied-migrations ledger keyed by (stream, version), or {} if the table doesn't exist yet."""
    try:
        rows = session.execute(
            "SELECT stream, version, applied_at, checksum, tier, description, applied_by "
            "FROM schema_migrations"
        )
    except Exception as exc:
        if "schema_migrations" in str(exc).lower() or "unconfigured table" in str(exc).lower():
            return {}
        raise
    out: dict[tuple[str, str], dict] = {}
    for row in rows:
        out[(row.stream, row.version)] = {
            "applied_at": row.applied_at,
            "checksum": row.checksum,
            "tier": row.tier,
            "description": row.description,
            "applied_by": row.applied_by,
        }
    return out


def record_migration(
    session: CassandraSession,
    migration: Migration,
    *,
    checksum: str,
    applied_by: str,
) -> None:
    """Insert a row recording that migration has been applied."""
    session.execute(
        """
        INSERT INTO schema_migrations (
          stream, version, applied_at, checksum, tier, description, applied_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            migration.stream,
            migration.version,
            datetime.now(tz=UTC),
            checksum,
            migration.tier,
            migration.description,
            applied_by,
        ),
    )


def execute_file(session: CassandraSession, path: Path, *, dry_run: bool) -> None:
    """Execute each statement in a migration file (or just log it, in dry-run mode)."""
    statements = split_cql_statements(path.read_text(encoding="utf-8"))
    for stmt in statements:
        # Session is already on the manifest keyspace; skip redundant USE.
        if stmt.upper().startswith("USE "):
            if dry_run:
                logger.info("  [dry-run] skip %s", stmt[:80])
            continue
        if dry_run:
            logger.info("  [dry-run] %s%s", stmt[:120], "..." if len(stmt) > 120 else "")
            continue
        session.execute(stmt)


def pending_migrations(
    migrations: list[Migration],
    applied: dict[tuple[str, str], dict],
    *,
    tier_filter: str | None,
) -> list[Migration]:
    """Return active migrations not yet applied, optionally filtered to prod tier."""
    pending: list[Migration] = []
    for migration in migrations:
        if migration.status != "active":
            continue
        if tier_filter == "prod" and migration.tier != "prod":
            continue
        if migration.key in applied:
            continue
        pending.append(migration)
    return pending


def cmd_status(args: argparse.Namespace) -> int:
    """Print the manifest vs applied-ledger table and pending-migration counts."""
    keyspace, migrations = load_manifest(Path(args.manifest))
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    session = connect_cluster(hosts, keyspace)
    applied = fetch_applied(session)

    logger.info("keyspace=%s hosts=%s\n", keyspace, hosts)
    header = (
        f"{'STREAM':<8} {'VER':<5} {'TIER':<5} {'STATUS':<10} "
        f"{'APPLIED':<10} {'CHECKSUM':<12} DESCRIPTION"
    )
    logger.info(header)
    logger.info("-" * 100)
    for migration in migrations:
        row = applied.get(migration.key)
        applied_flag = "yes" if row else "no"
        checksum = (row or {}).get("checksum", "")[:12] if row else ""
        if row and row.get("checksum") and migration.file.is_file():
            current = checksum_file(migration.file)[:12]
            if row["checksum"][:12] != current:
                checksum = f"{checksum}!={current}"
        logger.info(
            "%-8s %-5s %-5s %-10s %-10s %-12s %s",
            migration.stream,
            migration.version,
            migration.tier,
            migration.status,
            applied_flag,
            checksum,
            migration.description,
        )

    pending = pending_migrations(migrations, applied, tier_filter=None)
    prod_pending = pending_migrations(migrations, applied, tier_filter="prod")
    logger.info("\nPending (all active): %d", len(pending))
    logger.info("Pending (prod tier only): %d", len(prod_pending))
    if args.tier == "prod":
        for migration in prod_pending:
            logger.info("  - %s/%s %s", migration.stream, migration.version, migration.file.name)
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply all pending active migrations for the selected tier, recording each in the ledger."""
    keyspace, migrations = load_manifest(Path(args.manifest))
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    session = connect_cluster(hosts, keyspace)
    applied = fetch_applied(session)
    pending = pending_migrations(migrations, applied, tier_filter=args.tier)

    if not pending:
        logger.info("No pending migrations.")
        return 0

    applied_by = args.applied_by or os.getenv("USER", "cql_migrate")
    for migration in pending:
        if not migration.file.is_file():
            logger.error("missing file %s", migration.file)
            return 1
        digest = checksum_file(migration.file)
        logger.info(
            "Applying %s/%s (%s) %s",
            migration.stream,
            migration.version,
            migration.tier,
            migration.file.name,
        )
        execute_file(session, migration.file, dry_run=args.dry_run)
        if not args.dry_run:
            record_migration(session, migration, checksum=digest, applied_by=applied_by)
            applied[migration.key] = {"checksum": digest}

    logger.info("Done.")
    return 0


def cmd_register_baseline(args: argparse.Namespace) -> int:
    """Mark a stream's migrations up through a version as applied without executing them."""
    keyspace, migrations = load_manifest(Path(args.manifest))
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()]
    session = connect_cluster(hosts, keyspace)
    applied = fetch_applied(session)

    stream = args.stream
    through = args.through
    selected = [
        m
        for m in migrations
        if m.stream == stream and m.status == "active" and m.version <= through
    ]
    if not selected:
        logger.error("no migrations for stream=%s through=%s", stream, through)
        return 1

    applied_by = args.applied_by or "register-baseline"
    for migration in selected:
        if migration.key in applied:
            logger.info("Skip (already recorded) %s/%s", migration.stream, migration.version)
            continue
        digest = checksum_file(migration.file) if migration.file.is_file() else "baseline"
        if args.dry_run:
            logger.info(
                "[dry-run] register %s/%s checksum=%s",
                migration.stream,
                migration.version,
                digest[:12],
            )
            continue
        record_migration(session, migration, checksum=digest, applied_by=applied_by)
        logger.info("Registered %s/%s", migration.stream, migration.version)

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with its status/apply/register-baseline subcommands."""
    parser = argparse.ArgumentParser(description="Cassandra CQL migration tool")
    parser.add_argument(
        "--manifest",
        default=str(MANIFEST_PATH),
        help="Path to manifest.toml",
    )
    parser.add_argument(
        "--hosts",
        default=os.getenv("CASSANDRA_HOSTS", "127.0.0.1"),
        help="Comma-separated Cassandra hosts (env CASSANDRA_HOSTS)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show manifest vs applied ledger")
    status.add_argument(
        "--tier",
        choices=("all", "prod"),
        default="all",
        help="Also list prod-tier pending at the end",
    )
    status.set_defaults(func=cmd_status)

    apply = sub.add_parser("apply", help="Apply pending active migrations")
    apply.add_argument(
        "--tier",
        choices=("all", "prod"),
        default="all",
        help="prod = skip dev-tier migrations (TestNet-only tables)",
    )
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--applied-by", default="")
    apply.set_defaults(func=cmd_apply)

    baseline = sub.add_parser(
        "register-baseline",
        help="Mark migrations as applied without executing SQL (existing monolith deploys)",
    )
    baseline.add_argument("--stream", required=True, help="ledger | chain | app")
    baseline.add_argument("--through", required=True, help="Last version to register, e.g. 006")
    baseline.add_argument("--dry-run", action="store_true")
    baseline.add_argument("--applied-by", default="register-baseline")
    baseline.set_defaults(func=cmd_register_baseline)

    return parser


def main() -> int:
    """Parse CLI args and dispatch to the selected subcommand."""
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
