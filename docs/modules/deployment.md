# Brick: Deployment

## Goal

Repeatable packaging, upload, unpack, and service restart for TestNet and production hosts.

## Status

`done`

## Features (should do)

- Single entrypoint with subcommands: `deploy.sh provision | deploy | status`, configured by `deploy/deploy.conf`
- Two-user model: `ROOT_USER` for apt/certbot/nginx/systemd, `SERVICE_USER` runs services and owns `TARGET_PATH` (no sudo)
- `provision` (idempotent): apt deps, host service checks, `TARGET_PATH` dirs, one LE cert for `SITE_DOMAIN`+`API_DOMAIN`, own nginx site file (never touches the host's other vhosts)
- Build a versioned release tarball + `sha256` (`deploy/package.sh`: Flutter web build + backend/workers/schema/conduit-schema)
- Rsync archive to target host, verify checksum, backup `releases/current` → `releases/previous`
- Shared venv (union of backend+workers deps) and `shared/{backend,workers}.env` bootstrapped once from `deploy/env/*.env.example`, symlinked into each release
- Apply CQL migrations before restart (`deploy/scripts/cql_migrate.py`, tier via `DEPLOY_CQL_TIER`) using `CASSANDRA_USERNAME`/`CASSANDRA_PASSWORD` from the shared env (prod cluster requires auth; role setup in `deploy/README.md`)
- Restart backend and Celery systemd units, wait for `/health/ready`, verify units are active
- Rollback from `releases/previous` (`deploy/rollback.sh`)
- Safety gate: `DEPLOY_CONFIRM=1` for provision/deploy/rollback

## Good to have

- Full ordered checklist (chain admin, Mistral key, smoke tests): [deployment-checklist.md](deployment-checklist.md)
- Pre-deploy checklist in `deploy/README.md` (Conduit, beat)
- Separate deploy steps for Conduit vs API vs workers

## Future improvements

- Zero-downtime / blue-green swap behind a load balancer
- Automated TestNet deploy from CI on release tag
- Secrets pulled from vault (not only `.env` on host)

## Standards & RFCs

[systemd.service](https://www.freedesktop.org/software/systemd/man/systemd.service.html) units under `deploy/systemd/`. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#deployment).

## Depends on

- Built artifacts on host (shared Python venv at `$TARGET_PATH/venv`, prod: `/home/guillaume/algorand-platform/venv`)
- systemd, `rsync`, `ssh`

## Code map

- `deploy/package.sh`, `deploy/deploy.sh`, `deploy/deploy.conf`, `deploy/rollback.sh`, `deploy/README.md`
- `deploy/nginx/algorand-platform.conf`, `deploy/env/*.env.example`
- `deploy/systemd/algorand-platform-backend.service`
- `deploy/systemd/algorand-platform-celery.service`
- `deploy/systemd/algorand-platform-celery-beat.service`
- `deploy/systemd/algorand-platform-conduit.service`
