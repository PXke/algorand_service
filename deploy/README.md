# Deployment

> **Not Docker.** Local integration and pytest use [docker/README.md](../docker/README.md) (`docker-compose.yml`, testing only). This folder packages and runs the platform on a real host.

Everything is driven by `deploy/deploy.sh`, configured by `deploy/deploy.conf`
(every value can be overridden from the environment):

```bash
DEPLOY_CONFIRM=1 ./deploy/deploy.sh provision   # one-time host setup (as ROOT_USER)
DEPLOY_CONFIRM=1 ./deploy/deploy.sh deploy      # build + ship a release
./deploy/deploy.sh status                       # units + health at a glance
DEPLOY_CONFIRM=1 ./deploy/rollback.sh           # restore releases/previous
```

Two SSH users are involved:

- `ROOT_USER` (default `root`) — apt packages, certbot, nginx site, systemd units.
- `SERVICE_USER` (e.g. `guillaume`) — owns `TARGET_PATH`, runs the services,
  performs uploads/unpacks/venv/migrations. No sudo required.

## Smart deploy (default)

`deploy.sh` compares your working tree to the **last successful deploy**
(`git_sha` in `releases/current/BUILD_INFO.txt` on the server, or
`deploy/build/.last-deploy-sha` locally) and only does the work that changed:

| If git diff touches… | Action |
|---|---|
| `frontend_flutter/` | Flutter build + precompress + rsync web assets |
| `backend/` | Ship backend + restart API |
| `workers/` | Ship workers + restart Celery |
| `schema/` or `conduit/schema/` | Run CQL migrations |
| `*/pyproject.toml` | Refresh `requirements.lock.txt` + restart Python services |
| `frontend_flutter/pubspec.yaml` | `flutter pub upgrade` + rebuild |
| `deploy/nginx/` or `deploy/systemd/` | Reinstall nginx/systemd units |

Frontend-only deploys **do not restart** backend or Celery. The script prints a
scope plan before packaging.

```bash
DEPLOY_CONFIRM=1 ./deploy/deploy.sh deploy
```

Escape hatch — ignore git diff and deploy everything:

```bash
DEPLOY_FORCE_FULL=1 DEPLOY_CONFIRM=1 ./deploy/deploy.sh deploy
```

## What `provision` does (idempotent)

1. apt: python3, venv, build tools, rsync, curl.
2. Verifies nginx / redis / cassandra are already running on the host.
3. Creates `TARGET_PATH` owned by `SERVICE_USER`.
4. Obtains one Let's Encrypt cert for `SITE_DOMAIN` + `API_DOMAIN`
   (skipped when `/etc/letsencrypt/live/$SITE_DOMAIN` exists; picks the most
   recently used ACME account when the host has several).
5. Installs `deploy/nginx/algorand-platform.conf` as its own site file —
   the host's existing vhosts are never touched.

## What `deploy` does

1. `detect_changes.sh`: infer scope from git diff since last deploy.
2. `sync_deps.sh`: refresh locks only when `pubspec.yaml` or `pyproject.toml` changed.
3. `package.sh`: assemble `deploy/build/stage/` (Flutter/fonts skipped when unchanged).
4. rsync the stage tree to the host (incremental via `--link-dest`).
5. Backs up `releases/current` → `releases/previous`, activates the new tree.
6. Shared venv at `TARGET_PATH/venv` with the union of backend+workers deps.
7. Bootstraps `TARGET_PATH/shared/{backend,workers}.env` from
   `deploy/env/*.env.example` on first deploy (chmod 600, `INGEST_API_KEY`
   generated) and symlinks them into the release. Later deploys never
   overwrite them — edit on the host, then restart units.
8. Applies CQL migrations when schema changed.
9. Restarts only the systemd units that need it; reinstalls nginx/systemd when
   deploy config changed; waits for `/health/ready` when services restart.

## Cassandra role (one-time admin setup)

The production cluster runs `PasswordAuthenticator`, so the app connects with
a dedicated role (host convention: one role per app, like `oak`). Before the
first deploy, an admin creates the keyspace and role:

```sql
-- cqlsh as a superuser
CREATE KEYSPACE IF NOT EXISTS algorand_platform
  WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1};
CREATE ROLE IF NOT EXISTS algorand WITH PASSWORD = '<generated>' AND LOGIN = true;
-- If the cluster uses CassandraAuthorizer (ours runs AllowAllAuthorizer, so skip):
-- GRANT ALL PERMISSIONS ON KEYSPACE algorand_platform TO algorand;
```

Then set `CASSANDRA_USERNAME` / `CASSANDRA_PASSWORD` in **both**
`shared/backend.env` and `shared/workers.env`. The deploy aborts with a hint
if the password is empty. Backend, workers, `deploy/scripts/cql_migrate.py`
and `deploy/scripts/seed_service_registry.py` all honour these variables.

## Key settings (deploy.conf or environment)

| Variable | Default | Notes |
|---|---|---|
| `TARGET_HOST` | — | required |
| `SERVICE_USER` | — | required; runs the services |
| `ROOT_USER` | `root` | apt / systemd / nginx / certbot |
| `TARGET_PATH` | `/home/guillaume/algorand-platform` | releases/, shared/, venv/ live here |
| `SITE_DOMAIN` / `API_DOMAIN` | algorand.pxke.me / algorand-api.pxke.me | DNS must point at `TARGET_HOST` |
| `APP_PORT` | `9080` | 8080 is taken by algod on the prod host |
| `DEPLOY_CQL_TIER` | `all` | use `prod` to skip dev-tier tables |
| `DEPLOY_FORCE_FULL` | `0` | `1` = full deploy, ignore git diff |
| `DEPLOY_HEALTH_URL` | `http://127.0.0.1:$APP_PORT/health/ready` | checked from the host |
| `DEPLOY_CONFIRM` | `0` | must be `1` for provision/deploy/rollback |
| `PACKAGE_OUTPUT` | `stage` | `archive` for CI tarballs |

## Migration ledger

```bash
python deploy/scripts/cql_migrate.py status
python deploy/scripts/cql_migrate.py apply --tier prod
```

See [docs/architecture/cql-migrations.md](../docs/architecture/cql-migrations.md).

## Flutter web build (`package.sh`)

Production builds use:

```bash
flutter build web --release --wasm --no-web-resources-cdn \
  --pwa-strategy=none -O4 --no-source-maps \
  [--no-pub] \
  --dart-define-from-file=deploy/build/flutter_defines.json
```

- **`deploy/scripts/write_flutter_defines.sh`** — writes `flutter_defines.json` from
  `FRONTEND_*` env vars (set by `deploy.sh` / `deploy.conf`).
- **`--no-pub`** — skipped when `pubspec.lock` is unchanged since the last build.
- **Fingerprint skip** — entire Flutter compile is skipped when sources + defines are
  unchanged (`deploy/build/.frontend-build.sha256`).
- **Post-build prune** — removes unused canvaskit variants, source maps, and symbols.
