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
- Hosts and OS processes: `docs/ops-hosts.md`. `deploy.sh` only ships **5.135.131.229** (algorand.pxke.me / algorand-api.pxke.me). The OpenClaw marketing/seller/tester agent is a **separate** box at `92.222.76.121` (SSH as root) — not in this deploy path, not wash volume.

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

A second product, `backend/app/modules/x402/` (shared payment plumbing) plus
the three product modules `x402_news/`, `x402_scan/`, `x402_storage/` and
the `x402_catalog/` + `x402_wellknown/` discovery surface, sharing the same
backend/Cassandra/Redis/deploy pipeline as the newspaper but a **separate
concern** — do not conflate its rules with sections 1-8 above, and do not
let newspaper work block it or vice versa.
Reference: `docs/x402-facilitator.md` (verified facilitator/CAIP-2/tag
mechanics — work from that file, not memory or the official docs' literal
wording, which is wrong about the challenge tag).

Non-negotiable constraints (verbatim from the build plan, owner-approved):

- Nothing custodial: never hold user funds. Payouts come only from the
  dedicated hot wallet; the x402 `payTo` address is receive-only and its key
  is never in the repo, the agent's reach, or any container.
- No entity, no licence assumed. **KYB (Know Your Business — regulated
  real-entity/company compliance), PII storage, and fiat handling are out of
  scope, Phase 2 at the earliest.** Separately, **KYA (Know Your Agent —
  wallet age, on-chain behaviour, self-declared web identity, an on-chain
  attestation; roadmap item 8 below) was built as `modules/kya/` and then
  REMOVED in the 2026-09-10 consolidation (ADR-0006) — it no longer ships.**
  These are two different things with easily-confused acronyms — neither
  KYA nor KYB ships today, and KYB never does without an explicit new owner
  decision.
- Cassandra + Redis only, via `StoreFactory[T]` and a `Protocol` per store.
  Memory backend for dev/test only.
- TestNet until Phase 0 acceptance passes; mainnet is a config flip, done
  once, deliberately.
- Every settlement logged: asset id, amount, tx id, payer, resource, UTC
  timestamp, EUR value at time of settlement. This is the bookkeeping ledger.
- No wash volume, ever. Nothing in the codebase may pay our own endpoints
  from our own wallets except a labelled operator probe (wallets listed in
  `X402_PROBE_PAYERS`), which the settlement ledger excludes from the public
  settlements feed and from any ranking. The competition administrator
  explicitly audits for and disqualifies this — it is not just good practice.
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

**Consolidation, owner decision 2026-09-10** (`docs/adr/ADR-0006-x402-consolidation.md`):
the marketplace mechanics were removed — directory (3), visibility board
(2), feature-request board (4), grading (6), probe/monitoring (7, and 20
with it), KYA (8), the agent social network, the uptime check, fulfillment
receipts and the ping route. Kept and live: News Engine (1), sandboxed scan
(18b), backup storage (12). Do not rebuild any removed item without a new
owner decision. The struck-through items below keep their original text so
the history stays readable; they are not open work.

Every product below is a `require_payment()` consumer sharing the same gate,
store-factory pattern, and settlement ledger — never new protocol code per
product. Phase 0 (a real payment round-tripping through the shared gate
against the live facilitator) **passed 2026-08-30** and the mainnet flip is
done — see `docs/x402-facilitator.md`.

1. **News Engine pay-per-call** — **LIVE** (`x402_news`). The existing
   newspaper's article/data feed behind a micro-price: free headlines, tags
   and full article reads, paid full-text search. Reuses live data already
   in Cassandra; no new infra.
2. ~~**Paid visibility board** — agents pay to appear with a link back, free to
   browse ("Million Dollar Homepage for bots"). Same shape as the directory.~~
   **REMOVED 2026-09-10** (ADR-0006): shipped as `x402_board`, 0 real
   placements ever, deleted with its tables.
3. ~~**x402 endpoint directory** — **LIVE** (verified 2026-09-01 — the most
   complete product here: full CRUD, category/tag search, probe +
   probe-history reads, LWT-guarded first-insert). Pay to list, pay to boost
   rank, agents pay for ranked JSON search, humans browse free. See §4.1/§5.1.~~
   **REMOVED 2026-09-10** (ADR-0006): shipped as `x402_directory`; the only
   listing ever stored was our own search route. Deleted with its tables.
   Do not confuse with item 26 (the Algorand Open Registry, a separate
   free ecosystem showcase that stays).
4. ~~**Feature-request board** — agents pay to request an endpoint and vote;
   builders pay to read demand. Same shape as the directory.~~
   **REMOVED 2026-09-10** (ADR-0006): shipped as `x402_features`; 3
   requests ever, 2 of them our own probes. Deleted with its tables.
5. **Bounty version of the request board** — a vote is an escrowed payment,
   released on a passing test. First smart contract (Algorand Python,
   VibeKit) — do not install VibeKit before this item is actually started.
6. ~~**Endpoint grading** — agents pay a small stake to grade endpoints they
   actually paid; paid score lookup. Already scoped in the build plan (§5.3).~~
   **REMOVED 2026-09-10** (ADR-0006): shipped as `x402_grading`, deleted
   with its tables.
7. ~~**Probe / monitoring** — scheduled probing of every listed endpoint
   (reachability, latency, 402 validity). Probe traffic is flagged and
   excluded from every ranking — this is the one deliberate exception to "no
   wash volume." **LIVE, and the read side is deliberately FREE, not
   paid** (owner call 2026-08-31, commit `10b3dd5`): real measured uptime
   history works better as a trust signal an agent can point to for free
   than as its own paid product — the same "don't charge for what's already
   effectively public" reasoning as the News Engine's free article read, and
   a direct answer to a real agent's ask on Moltbook. Every listing detail
   read already surfaces the newest probe result automatically. **Item 20
   below is merged into this item, not a separate product** — see 20.~~
   **REMOVED 2026-09-10** (ADR-0006), together with item 20: the
   `workers/app/modules/x402_probe/` beat and its tables are gone with the
   directory it probed. The separate paid uptime check (`x402_uptime`) was
   removed at the same time; its `check_target` helper survives only as
   `ecosystem/services/checker.py`, serving the Open Registry's liveness
   check (item 26). `X402_PROBE_PAYERS` (operator wallets excluded from the
   settlements feed) is the one piece of "probe" vocabulary that stays.
8. ~~**Know Your Agent (KYA)** — tiered bot/agent identity (wallet, web
   identity, verified owner, behaviour), on-chain attestation, paid verify.
   This is `modules/kyc/` (module directory name predates the KYA/KYB
   terminology split — rename opportunistically if touching this module, not
   as its own task). The `declare_discovery_extension`/`OutputConfig` bug
   once described here is fixed (verified 2026-09-01 — see
   `app/modules/x402/discovery.py`'s shared wrapper and its regression
   test); the module is code-complete (enrollment, tiered trust signals,
   paid verify/payout) and registered live, but gated off in prod by owner
   decision, and still has no real on-chain attestation write — only
   off-chain indexer-derived signals.~~
   **REMOVED 2026-09-10** (ADR-0006): the `kya` module (formerly `kyc/`),
   its Flutter frontend and its tables are deleted; it never ran in prod.
   **This was KYA, not KYB** — see the constraints note above. Do not
   conflate with regulated Know-Your-Business/entity compliance, which
   stays excluded.
9. **Starter credit** — endpoint-funded trial USDC for newly-identified
   agents. Real fund distribution to third parties — needs an explicit
   abuse/sybil design before any code, not a subagent's unilateral call.
10. **Paid work** — pay agents for verifiable tasks (probing, building) where
    the x402 calls are a byproduct, not the point. Overlaps 5/19 — resolve
    which owns the escrow primitive before building either twice.
11. **Confidential-until-reveal payments** — stake-building: private now,
    provably yours later, view keys for auditors. Real cryptography design
    needed before any code.
12. **Pay-per-MB storage** — **LIVE** as Agent backup storage
    (`x402_storage`): opaque, versioned backup blobs on a local-disk
    connector on the API host, priced per KB per retention term, wallet-
    signature-authenticated free reads/deletes, a reaper beat for expiry.
    The original idea (S3-compatible upload/get/renew on a cheap backend
    such as Wasabi/B2/R2, plus pay-to-reveal for private data) still needs a
    provider + cost-model decision before the connector moves off local disk
    — real recurring infra spend.
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
18b. **Sandboxed file/tarball scan** (owner idea, 2026-08-31) — **LIVE** as
    `x402_scan` (`POST /api/v1/x402/scan/url`): an agent hands us a URL to a
    file it's wary of opening itself; we download it (bounded, SSRF-pinned),
    run it through ClamAV, file-type, entropy, indicator extraction and a
    zip/tar-bomb-safe member listing inside a network-isolated container
    that never executes the input, and report back a verdict plus contents.
    Sells the same shape as 17: a paid, externally-triggered job needing real
    process-lifecycle discipline. Static analysis only in this version; any
    dynamic/execution sandbox is a new design decision, not an extension.
19. **Agent-to-agent job escrow** — pay when output passes a test. Overlaps
    5/10 — same resolution needed on the shared escrow primitive.
20. **~~Reputation / uptime-proof ledger~~** — **merged into item 7, not a
    separate product** (owner decision 2026-09-02), and **removed with it
    2026-09-10** (ADR-0006): the original idea was "endpoints pay to attach
    verified proofs," but item 7's free probe data already surfaced
    automatically on every listing, which covered the same value without a
    second payment flow. Do not build a separate paid "attach a proof"
    route — if this is ever revisited, keep the measured layer itself free
    forever (charging endpoints to influence what's presented as
    measurement would poison the one trust signal a marketplace owns).
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
25. **Formal-verification safety-contract proofs** (owner idea, 2026-09-01,
    genuinely just a "wondering if possible" — no build authorized) — a
    paying agent currently has no way to know an endpoint does what it
    claims before paying. A general "prove the output is correct" proof is
    not achievable (e.g. proving a malware scanner's verdict is "right" is a
    category error — there's no formal ground truth for "malicious"). What
    IS provable: narrow, deterministic SAFETY-CONTRACT properties a route's
    code obeys — payment always precedes product work, a resource cap is
    enforced before the expensive operation runs, a sandbox never executes
    its input. Realistic shape: formally verify (Lean 4, e.g. via Mistral's
    Leanstral agent, or an equivalent tool) a small translated control-flow
    slice of one of these properties, publish the proof as an attached,
    checkable artifact on the endpoint's marketplace listing. Needs a real
    design pass (which properties, how a translated slice stays honest to
    the actual running code, how a payer verifies the proof) before any
    code — same "needs explicit human design decision" flag as the items
    below.
26. **Algorand Open Registry** (owner idea, 2026-09-07, named 2026-09-07) —
    a GitHub-style curated list (like an "awesome-X" list) where builders
    submit their own Algorand project/product with a category, and PXke
    publishes it as a reference/visibility page. Purpose is visibility, not
    revenue — the operator's own words: "we don't have fucking visibility."
    Distinct from the removed item 3 x402 directory (which was paid and
    x402-native endpoints only): this is a broader Algorand-ecosystem
    showcase, open to any project regardless of x402 support, and it stays
    (`backend/app/modules/ecosystem/`, `workers` `ecosystem_probe`). Design done (see
    `docs/awesome-algorand-directory-design.md`): 17 categories, multiple
    free tags per entry, a submit-time + periodic liveness check on every
    listed page, free/anonymous human-reviewed submissions, featured tier
    deferred to v1.1. No build started yet — remaining owner calls before
    code are in that doc's §9 (contact field, seed scope, LLM blurb
    helper, exact path).
26b. **Algorand builder Discord/community channel** (owner idea, 2026-09-07)
    — operator's read: the official Algorand Discord's builder space is
    thin (one subchannel) and over-moderated. Idea is a PXke-run builder
    channel as an alternative gathering point, likely feeding item 26's
    directory. Not scoped, not started — a community/ops decision, not an
    engineering task yet.

**Sequencing note** (rewritten 2026-09-10): the three live products are 1,
12 and 18b; work on them means depth (reliability, pricing, the storage
connector moving off local disk), not breadth. Items 2/3/4/6/7/8/20 are
removed and are not "next builds" under any sequencing — a new owner
decision reopens them, nothing else. Items 5/9/10/11/13/14/15/16/17/18/19/
21/22/23/25 all need an explicit human design decision (financial
exposure, new infra spend, or cryptography) before any agent starts
writing code against them — flag and stop, don't guess and build. Item 26
(Open Registry) is a separate, free, non-x402 product with its own design
doc and proceeds on its own track.
