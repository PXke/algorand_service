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

1. `package.sh`: builds Flutter web (skippable with `SKIP_FRONTEND_BUILD=1`)
   and tars `backend workers schema conduit/schema deploy frontend_web/`.
2. rsync upload + sha256 verification.
3. Backs up `releases/current` → `releases/previous`, unpacks the new release.
4. Shared venv at `TARGET_PATH/venv` with the union of backend+workers deps.
5. Bootstraps `TARGET_PATH/shared/{backend,workers}.env` from
   `deploy/env/*.env.example` on first deploy (chmod 600, `INGEST_API_KEY`
   generated) and symlinks them into the release. Later deploys never
   overwrite them — edit on the host, then restart units.
6. Applies CQL migrations with the credentials from `shared/backend.env`.
7. Installs/updates systemd units + nginx site, restarts, waits for
   `/health/ready`.

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
| `DEPLOY_SKIP_MIGRATE` | `0` | |
| `DEPLOY_HEALTH_URL` | `http://127.0.0.1:$APP_PORT/health/ready` | checked from the host |
| `DEPLOY_CONFIRM` | `0` | must be `1` for provision/deploy/rollback |

## Migration ledger

```bash
python deploy/scripts/cql_migrate.py status
python deploy/scripts/cql_migrate.py apply --tier prod
```

See [docs/architecture/cql-migrations.md](../docs/architecture/cql-migrations.md).
