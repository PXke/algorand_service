# Hosts and processes

Where things actually run. `deploy.sh` only ships the **platform** host.
The OpenClaw agent is a **separate** machine and is not part of this
repo's deploy path.

## Hosts

| Role | Address | How this repo reaches it |
|------|---------|--------------------------|
| **Platform** (newspaper + x402 API + workers) | `5.135.131.229` | `deploy/deploy.conf` `TARGET_HOST`. Public names: `algorand.pxke.me`, `algorand-api.pxke.me`. |
| **OpenClaw** marketing / seller / tester agent | `92.222.76.121` | **Not** in `deploy.sh`. SSH as `root`. Lives outside this release tree. |
| **This laptop / workspace** | `/opt/g/algorand` | Git working copy. Uncommitted work is not what prod runs. |

The platform box is a shared nginx host: other vhosts (`eatilla.pxke.me`,
`ritmo.biekos.com`, retired `blog.pxke.me` / `pxke.me` 410 tombstones)
already exist. Deploy installs **our** site file only and does not touch
the others.

`algod` already binds **8080** on the platform host, so gunicorn listens
on **9080** (`APP_PORT` in `deploy.conf`). nginx proxies 443 → that port.

## Platform host — systemd units this repo owns

All except Conduit run as `SERVICE_USER` (`guillaume`) from
`/home/guillaume/algorand-platform/releases/current/`. Env files are
`shared/{backend,workers}.env`, symlinked into the release. Restart via
`deploy.sh`; do not un-pause compose or bounce Celery unless the task
says so.

| Unit | What it is |
|------|------------|
| `algorand-platform-backend` | Falcon app under Gunicorn gthread (`deploy/scripts/run_backend.sh`). JSON API + SSR. |
| `algorand-platform-celery` | Celery worker: queues `default,scrape,pipeline,chain,security`. Cold stop (`SIGQUIT`). |
| `algorand-platform-celery-beat` | Celery beat (chain tail, crawler drains, x402 probe if enabled, storage reaper if token set, …). |
| `algorand-platform-celery-translate` | Isolated translate worker: queue `translate`, `--concurrency=1 --pool=solo`. |
| `algorand-platform-conduit` | Algod → Cassandra indexer. Separate user/path in the unit file (`algorand`, `/opt/algorand-platform/…`). |

Host services we depend on but do not ship: **nginx**, **Cassandra**,
**Redis**, **Typesense**. `provision` checks they are already running.

## Platform host — process roles (what talks to what)

```
Internet
  → nginx :443  (algorand.pxke.me SPA + SSR, algorand-api.pxke.me /api/)
      → gunicorn :9080  (backend)
            ↳ Cassandra, Redis, Typesense
            ↳ x402 facilitator (settlement), algod/indexer (chain reads)
  → celery worker + beat + translate
            ↳ same Cassandra / Redis; DeepSeek for compose
            ↳ Playwright for browser scrape
  → conduit
            ↳ algod → Cassandra chain tables
```

Two products share that API: the **newspaper** and the **x402 marketplace**.
Nothing custodial. x402 `payTo` is receive-only; its key is not on this
agent, in the repo, or in any container.

## OpenClaw agent (`92.222.76.121`)

A marketing / seller / tester **OpenClaw** instance. It is a client of
the marketplace (and a way to talk to other agents), not a platform
service.

- SSH: `root@92.222.76.121` (OpenClaw itself runs as user `ubuntu`)
- systemd user unit: `openclaw-gateway.service` (Linger=yes), gateway `:18789`
- **Not** deployed, restarted, or env-managed by `deploy.sh`
- Pays (if at all) through OpenClaw + `@goplausible/openclaw-algorand-plugin`
  (`x402_fetch` / MCP `make_http_request_with_x402`), not through
  `x402-client/` in this repo
- Wallet and model keys live **on that box**, not here
- Web search: plugins `@openclaw/parallel-plugin` + `@openclaw/duckduckgo-plugin`
  (both 2026.8.1). Default `web_search` provider is `parallel-free`; DuckDuckGo
  is installed/enabled as a second provider (OpenClaw only routes the agent
  tool through one at a time).
- **Verifying what the agent actually did:** read the sqlite transcript
  store — `~/.openclaw/agents/main/agent/openclaw-agent.sqlite`, table
  `transcript_events` (JSON events incl. `toolCall`/`toolResult`) — or the
  workspace scripts' own logs (`scripts/.moltbook-posts.jsonl`). Do **not**
  judge from `journalctl`: at the current log level it records *failed*
  tool calls only, nothing for successful ones, and tool-subprocess network
  I/O is invisible to it — a perfectly healthy run greps as "nothing
  happened" (this false verdict already occurred once, 2026-09-04; see
  `docs/openclaw-reliability-investigation.md`).

**Wash volume:** CLAUDE.md section 9. This agent must not pay our own
x402 endpoints from our own wallets to inflate rankings or Volume.
The only deliberate self-traffic is the labelled **probe**, excluded
from ranking. Tester calls against our catalog are fine as *manual
checks*; they are not a second probe product and must not be wired
into any ranking path.

## Local / test (not prod)

`docker-compose.yml` is **testing only** (Cassandra, Redis, Typesense,
optional localnet). It is not how `5.135.131.229` runs.

## Pointers

- How to ship the platform: `deploy/README.md`
- Unit files: `deploy/systemd/`
- Celery queues / beat: `workers/app/celery_app.py`, `deploy/scripts/run_celery.sh`
- x402 catalog (what the OpenClaw would fetch): `GET https://algorand-api.pxke.me/api/v1/x402`
