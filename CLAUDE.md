# Agent instructions — PXke Algorand monorepo

These rules apply to every coding agent and sub-agent working in this repo.
They exist because a 2026-08-28 audit found the same bug classes recurring
(finished work silently discarded, copy-pasted fanout with divergent error
handling, gates that only run on one provider, stale docs asserting the
opposite of the code). Follow them literally.

## 0. Orientation (read before touching code)

- Trunk is `design/newspaper-desks`. `master` is 364 commits stale — never branch from or compare against it.
- Services: `workers/` (Celery pipeline), `backend/` (Falcon + gunicorn, NOT Robyn — ignore docstrings saying Robyn), `frontend/` (Vite + Svelte 5). Storage is Cassandra + Redis + Typesense **only**; never introduce another datastore.
- Live LLM provider is DeepSeek (`deepseek-v4-flash`). Mistral is retired. `MISTRAL_*` env names survive as legacy knobs; do not add new ones.
- Removed lanes, do not restore: Discord/Reddit/Telegram ingest, external push, weekly digest, Flutter frontend, `publish_queue` table.
- `.claude/worktrees/` is agent scratch — never read, grep, or count it as codebase.
- No git remote → CI has never run. "CI is green" means nothing. Verify locally (section 6).

## 1. Scope discipline

- Do exactly the task. No drive-by refactors, renames, or "while I'm here" cleanups outside the files the task requires. Put anything else you notice in your final report under **Observed, not fixed**.
- Never delete, truncate, unpublish, or migrate production data without an explicit instruction naming the target.
- Never un-pause compose (`AUTO_COMPOSE_PAUSED`), pin/select artifacts, or restart celery in prod unless the task says so.
- Deploying is a separate, explicit ask. Other agents deploy this repo independently — uncommitted local work is not "safe".

## 2. Pipeline invariants (workers)

Every change to `workers/app/modules/newspaper/`, `ai/`, `crawler/`, `gatekeeper/` must preserve:

1. **A finished compose is never discarded.** Any path that has paid for an LLM compose must end in `insert_article` / `_hold_for_review` storage or an explicit re-raise for retry. If you add a veto after compose, it stores-and-holds; it does not `return {"status": ...}` and drop the draft.
2. **Store before mark.** Write the durable artifact first, then the snapshot/flag/review-completion that says "done". (`ingest_signal`: artifact before snapshot. `recompose_published`: apply before `complete_classifier_review`.)
3. **Every gate runs on every provider.** No `if provider == "deepseek": return early` around a safety check. If a gate needs digest output, produce it in raw mode too.
4. **No ungrounded fallback compose.** Owner decision 2026-07-14. If compose fails, raise. Never fall back to a tool-less `chat_json_object`.
5. **Beats that can overlap must be `single_flight`-locked with `expires=` set**, and lock TTL ≥ the task's soft time limit.
6. **State transitions in `finally`.** `_resolve_artifact`-style bookkeeping must run on `SoftTimeLimitExceeded` too.
7. **Flags set before dispatch must be cleared on failure** (`deep_classify_queued`, `processing` rows, cooldown stamps).
8. **Empty is not "none found".** A tool helper that hits an error returns `{"error": ...}`, never `[]` / `""` / `None` that the writer will read as ground truth.
9. Cooldown/lock/budget checks that touch Redis fail **open** with a log line — one Redis blip must not crash a beat.

## 3. Code rules (Python)

- **No new `except Exception: pass`.** Every broad except logs at ≥ warning with context, and either re-raises or returns a value the caller can distinguish from success.
- **No new function-local `from app.… import`** as a DI seam. Import at module top; inject via parameters when tests need a seam.
- **No new copies of existing logic.** Before writing: publish fanout → `_finalize_publish` path; Redis → the shared cached client (add `get_redis()` if it doesn't exist yet rather than `redis.from_url` per call); Playwright → existing launch/settle helper; CQL → a `_Stmt` in `core/statements.py` (prepared, bound params, never f-strings, never `IN %s`); shared statements → `shared/algorand_shared/*_statements.py`, not both `statements.py`.
- **Config has one owner.** New settings go in `workers/app/core/config.py` (or backend `Settings`) and are read from there — never raw `os.getenv` in `celery_app.py` or task modules. Delete a setting you make unread.
- Functions > 150 lines: don't grow them; extract before adding a branch.
- **Docstrings describe current behaviour only.** Incident history goes in the commit message or `docs/adr/`. Never leave a comment that asserts what the code *should* do if it doesn't.
- New CQL migration → add the `manifest.toml` entry; no semicolons inside CQL comments.
- `articles_feed` partial UPDATEs create phantom rows — always full INSERT via the store helpers.

## 4. Code rules (backend)

- Every admin handler calls `require_admin_wallet` first; the `X-Admin-Wallet` header is never trusted.
- Never return `str(exc)` on a 500. Bound request bodies. Stream remote fetches and abort past the cap.
- No `ALLOW FILTERING` on non-key columns; add a lookup table instead.
- No unbounded listings — every list query has a LIMIT.

## 5. Code rules (frontend)

- Anything rendered via `{@html}` goes through the sanitizer (DOMPurify allowlist). No exceptions, including admin tabs.
- Every async fetch in a component uses AbortController or a sequence guard; every dangling promise has a `.catch`.
- `npm run check` must be clean (currently 4 errors — fix, don't add).
- Reuse the shared async-state / date-format helpers; do not hand-roll `loading/error/try/catch` in a new tab.
- All 9 locale files must keep identical key sets.

## 6. Verification — required before reporting done

Run in the touched service(s) and paste the tail of the output in your report:

```
cd workers  && .venv/bin/ruff check . && .venv/bin/ruff format --check <changed files> && .venv/bin/pytest -q
cd backend  && .venv/bin/ruff check . && .venv/bin/ruff format --check <changed files> && .venv/bin/pytest -q
cd frontend && npm run check && npm test && npm run build
```

- Tests are no-network by design; fake Redis/Cassandra at the seam, never mock the network.
- Every bug fix ships with a regression test on the exact path (the hot paths currently have none: `_finalize_publish`, `_resolve_artifact`, `_run_digest_gap_fill`, `_attempt_revision`, `_select_provider`, `scrape_from_queue_item`).
- Report failures verbatim. Never say "tests pass" for a suite you did not run.

## 7. Sub-agent protocol

When you spawn sub-agents:

- One sub-agent per independent file set; never two agents editing the same module.
- Give each: the exact files in scope, the invariants from sections 2-5 that apply, and the verification command it must run.
- Sub-agents are **read-only** unless the prompt explicitly grants edits, and never touch `.claude/worktrees`, prod hosts, or `deploy/`.
- Require a report shaped as: **Changed** (file:line) / **Verified** (command + tail) / **Observed, not fixed** / **Unsure**. Treat a sub-agent's claim as a lead, not a verdict — re-read the cited lines yourself before relaying.
- Do not fan out for single-fact lookups; grep it yourself.

## 8. Final report shape (every task)

1. What changed (file:line list).
2. Verification output tail.
3. Observed-not-fixed list.
4. Anything you assumed because the task was ambiguous.

## 9. x402 Agent Marketplace (Algorand Global x402 Challenge, deadline Sept 1)

A second product, `backend/modules/x402/` + `backend/modules/kyc/` and its
successors, sharing the same backend/Cassandra/Redis/deploy pipeline as the
newspaper but a **separate concern** — do not conflate its rules with
sections 1-8 above, and do not let newspaper work block it or vice versa.
Reference: `docs/x402-facilitator.md` (verified facilitator/CAIP-2/tag
mechanics — work from that file, not memory or the official docs' literal
wording, which is wrong about the challenge tag).

Non-negotiable constraints (verbatim from the build plan, owner-approved):

- Nothing custodial: never hold user funds. Payouts come only from the
  dedicated hot wallet; the x402 `payTo` address is receive-only and its key
  is never in the repo, the agent's reach, or any container.
- No entity, no licence assumed. Anything needing KYB, PII storage, or fiat
  handling is out of scope (Phase 2 at the earliest).
- Cassandra + Redis only, via `StoreFactory[T]` and a `Protocol` per store.
  Memory backend for dev/test only.
- TestNet until Phase 0 acceptance passes; mainnet is a config flip, done
  once, deliberately.
- Every settlement logged: asset id, amount, tx id, payer, resource, UTC
  timestamp, EUR value at time of settlement. This is the bookkeeping ledger.
- No wash volume, ever. Nothing in the codebase may pay our own endpoints
  from our own wallets except the probe, which is labelled as such and
  excluded from any ranking. The competition administrator explicitly audits
  for and disqualifies this — it is not just good practice.
- Multi-asset `accepts` in the 402 offer from day one, even if only USDC is
  enabled initially. Note: the competition's Volume score is USDC-specific;
  other assets help the Innovation score, not Volume — don't over-invest
  before a working USDC endpoint exists.
- Rate limit every free endpoint per wallet and per IP.
- Docstrings say Falcon. Robyn is gone; delete any mention.
- Every paid product is a plugin/module behind the shared `require_payment()`
  gate. No paid route lives in core without going through it.

Entry category: **Composite** (several endpoints, one `payTo`, individually
discoverable) — confirmed against the official category definitions, not
Standard or Orchestrator. See `docs/x402-facilitator.md` for why.

Registration itself requires real personal identity/legal attestation — an
agent drafts and verifies the technical prerequisites, but does not submit
the registration form unattended.
