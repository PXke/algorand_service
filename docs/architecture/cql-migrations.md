# Cassandra CQL migrations

We maintain **two schema streams** in one keyspace (`algorand_platform`). A single ledger table records what has been applied so deploys only run **new** CQL.

## Streams

| Stream | Purpose | Migration dir |
|--------|---------|---------------|
| `ledger` | `schema_migrations` table | `schema/migrations/ledger/` |
| `chain` | Conduit on-chain index | `conduit/schema/migrations/chain/` |
| `app` | Robyn API / workers | `backend/schema/migrations/app/` |

**Registry:** [`schema/migrations/manifest.toml`](../../schema/migrations/manifest.toml) — order, tier, and lifecycle status for every version.

**Do not** apply the legacy monolith files on hosts that use this tool:

- `conduit/schema/cassandra.cql` → replaced by `chain/001`–`006`
- `backend/schema/cassandra_app.cql` → replaced by `app/001`–`003`

Those files remain as a readable “full picture” snapshot; new changes must add a **new numbered migration** and a manifest entry.

## Tiers (prod vs testing)

| Tier | Meaning |
|------|---------|
| `prod` | Required for production. Do not drop tables without an ADR and a new forward migration. |
| `dev` | TestNet / local only. Safe to `DROP` and re-apply before prod if the design is still moving. |

**Prod deploy** (skip dev-tier CQL):

```bash
./deploy/scripts/cql-migrate.sh apply --tier prod
```

**Full TestNet** (includes dev tables such as `service_registry`):

```bash
./deploy/scripts/cql-migrate.sh apply
```

To retire a dev experiment: set `status: retired` in the manifest, drop the table manually if needed, and add a new migration if the prod shape changes.

## Commands

```bash
# What is applied vs pending (checksum drift shown with !=)
./deploy/scripts/cql-migrate.sh status

# Apply everything still pending (active only)
./deploy/scripts/cql-migrate.sh apply

# Prod cutover: only tier: prod migrations
./deploy/scripts/cql-migrate.sh apply --tier prod

# Plan without writes
./deploy/scripts/cql-migrate.sh apply --dry-run
```

Environment:

- `CASSANDRA_HOSTS` (default `127.0.0.1`)
- Keyspace from manifest (`algorand_platform`) — connect uses `CASSANDRA_KEYSPACE` via backend settings if you run Python directly

Requires `cassandra-driver` and `pyyaml` (backend venv).

## Existing databases (monolith already applied)

If Conduit `auto_migrate` or a one-shot `cassandra.cql` / `cassandra_app.cql` already created tables, **register** the equivalent versions instead of re-running `CREATE`:

```bash
# Example: chain tables from old monolith through receiver index
./deploy/scripts/cql-migrate.sh register-baseline --stream chain --through 006 \
  --applied-by "legacy monolith 2026-06"

# Example: app suggestions + upvotes only (no service_registry yet)
./deploy/scripts/cql-migrate.sh register-baseline --stream app --through 002

# Always ensure ledger exists first (or apply it once)
./deploy/scripts/cql-migrate.sh apply --tier prod   # runs ledger/000 if needed
```

Then run `status` and `apply` to pick up **only** migrations added after baseline.

## Conduit `auto_migrate`

The cassandra exporter can still run embedded CQL when `auto_migrate: true`. **Prefer `cql-migrate` in shared environments** so:

1. App and chain schemas stay in one ledger.
2. Dev-tier tables are not created in prod by accident.
3. Operators can see pending vs applied.

Set `auto_migrate: false` in production `conduit.yml` after baseline registration.

## Adding a migration

1. Add `NNN_short_name.cql` under the correct `migrations/` directory.
2. Append an entry to `schema/migrations/manifest.toml` (`tier`, `status: active`, description).
3. Run `status` locally, then `apply` on TestNet.
4. For prod: use `apply --tier prod` after the dev window, or promote `tier` from `dev` to `prod` once stable.

## Version matrix (current)

| Stream | Ver | Tier | Description |
|--------|-----|------|-------------|
| ledger | 000 | prod | `schema_migrations` |
| chain | 001–006 | prod | Keyspace → receiver index |
| app | 001–002 | prod | Suggestions, upvotes |
| app | 003–007 | prod | `service_registry`, newspaper tables, `service_events` |
| app | 008 | prod | `price_metric_samples`, `price_metrics_brief` ([price-metrics-mistral.md](../modules/price-metrics-mistral.md)) |
