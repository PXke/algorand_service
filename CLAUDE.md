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
- No entity, no licence assumed. Anything needing **regulated KYB (real
  business/entity compliance)**, PII storage, or fiat handling is out of
  scope (Phase 2 at the earliest). Note the deliberate acronym overload: the
  KYC/KYB *product* (roadmap item 8 below) means "Know Your **Bot**" — wallet
  age, on-chain behaviour, self-declared web identity, an on-chain
  attestation — not regulated Know-Your-Business. That product is in scope
  now; regulated entity compliance is not, ever, until Phase 2.
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

### 9.1 Product roadmap (owner brainstorm, 2026-08-29 — numbered as named, not priority order)

Every product below is a `require_payment()` consumer sharing the same gate,
store-factory pattern, and settlement ledger — never new protocol code per
product. **Phase 0 gates everything else**: nothing here starts until a real
TestNet payment has actually round-tripped through `/api/v1/x402/list`
(built, unit-tested, never yet run against a live wallet/facilitator as of
this writing) and the mainnet flip (§4.2) is done. Do not let roadmap breadth
become an excuse to defer proving the one thing the deadline depends on.

1. **News Engine pay-per-call** — the existing newspaper's article/data feed
   behind a micro-price. Reuses live data already in Cassandra; no new infra.
2. **Paid visibility board** — agents pay to appear with a link back, free to
   browse ("Million Dollar Homepage for bots"). Same shape as the directory.
3. **x402 endpoint directory** — **in progress**. Pay to list, pay to boost
   rank, agents pay for ranked JSON search, humans browse free. See §4.1/§5.1.
4. **Feature-request board** — agents pay to request an endpoint and vote;
   builders pay to read demand. Same shape as the directory.
5. **Bounty version of the request board** — a vote is an escrowed payment,
   released on a passing test. First smart contract (Algorand Python,
   VibeKit) — do not install VibeKit before this item is actually started.
6. **Endpoint grading** — agents pay a small stake to grade endpoints they
   actually paid; paid score lookup. Already scoped in the build plan (§5.3).
7. **Probe / monitoring** — scheduled micro-payments to every listed
   endpoint, selling measured uptime/latency/spec-correctness. Already
   scoped (§5.3). Probe traffic is flagged and excluded from every ranking —
   this is the one deliberate exception to "no wash volume."
8. **Know Your Bot (KYC/KYB)** — tiered bot/agent identity (wallet, web
   identity, verified owner, behaviour), on-chain attestation, paid verify.
   This is `modules/kyc/`, currently broken (a real, reproduced bug — see
   `services/enrollment_service.py`/`api/routes.py`'s `declare_discovery_extension`
   call passing a raw dict where the installed package requires `OutputConfig`).
   **"KYB" here is Know Your Bot, not regulated Know Your Business** — see the
   constraints note above. Do not conflate with real entity compliance.
9. **Starter credit** — endpoint-funded trial USDC for newly-identified
   agents. Real fund distribution to third parties — needs an explicit
   abuse/sybil design before any code, not a subagent's unilateral call.
10. **Paid work** — pay agents for verifiable tasks (probing, building) where
    the x402 calls are a byproduct, not the point. Overlaps 5/19 — resolve
    which owns the escrow primitive before building either twice.
11. **Confidential-until-reveal payments** — stake-building: private now,
    provably yours later, view keys for auditors. Real cryptography design
    needed before any code.
12. **Pay-per-MB storage** — S3-compatible upload/get/renew on a cheap
    backend (Wasabi/B2/R2), plus pay-to-reveal for private data. Needs a
    provider + cost-model decision first — real recurring infra spend.
13. **Storage router** — one endpoint, store-by-intent (size, term,
    durability, budget), routed across cheap providers + Arweave for
    permanence. Depends on 12 existing first.
14. **Storage price-discovery query** — "where should this blob go today,"
    sold per query. Depends on 12/13's provider set existing.
15. **Archival node / heavy indexer queries** — per call. New infra
    (dedicated indexer/archival node) — cost decision needed first.
16. **Inference per token** — a GPU box or wholesale LLM contract fronted by
    x402. Real infra spend decision needed first; consider whether this
    should route through the same providers `workers/` already pays for LLM
    calls rather than a new contract.
17. **Headless browsing / scraping pool** — per request. May be able to reuse
    `workers/`'s existing Playwright infra (see `browser_reaper.py`'s
    orphan-Chromium lessons — a paid, externally-triggered browsing endpoint
    needs the SAME care about process lifecycle a bare script doesn't get).
18. **Transaction simulation / fuzzing** — per run. Needs a simulate-endpoint
    design (algod's own simulate API is the likely base) before any code.
19. **Agent-to-agent job escrow** — pay when output passes a test. Overlaps
    5/10 — same resolution needed on the shared escrow primitive.
20. **Reputation / uptime-proof ledger** — endpoints pay to attach verified
    proofs. Overlaps 7 (probe) — likely the same underlying data, a second
    paid view of it, not a second measurement system.
21. **USDC→EURQ swap route** — so agents pay euro-priced services without
    noticing. Depends on Quantoz integration (Phase 2 in the original build
    plan) — do not front-run this before 12/13's storage work or Phase 0.
22. **EURQ liquidity/settlement data feed** — sold per query, useful to
    Quantoz and the Foundation directly. Same Phase-2 dependency as 21.
23. **Sponsored onboarding endpoint** — receive any asset with zero ALGO,
    fees and opt-in covered atomically. Explicitly Phase 2 in the original
    build plan (fee pooling + min-balance + opt-in in one atomic group,
    ARC-59 inbox where supported) — real fund-pooling design, not a quick add.
24. **~~Pay bots to execute x402 calls~~** — **rejected**, owner call: this is
    wash volume by construction and the competition administrator explicitly
    audits for and disqualifies exactly this pattern (§14 of the official
    rules). Do not build, do not revisit without an explicit new owner
    decision overriding this one.

**Sequencing note**: items 1, 2, 4 are the cheapest next builds after Phase 0
proves out — same shape as the directory, no new infra or fund-custody
design required. Items 5/9/10/11/12/16/19/21/22/23 all need an explicit
human design decision (financial exposure, new infra spend, or cryptography)
before any agent starts writing code against them — flag and stop, don't
guess and build.
