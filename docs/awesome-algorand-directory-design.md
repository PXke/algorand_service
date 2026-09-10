# Algorand ecosystem directory (roadmap item 26) — design pass

> **Note (2026-09-10):** the `x402_board`, `x402_directory` and `x402_features` modules this design cites as code to reuse (§5 featured tier, submission shape) were removed in the x402 consolidation — see `docs/adr/ADR-0006-x402-consolidation.md`. The shipped registry (`backend/app/modules/ecosystem/`) is self-contained; the v1.1 featured-tier plan below needs an owner re-read before it is built.

Date: 2026-09-07. Read-only design pass; no code was changed, nothing was
deployed, nothing was posted anywhere. Answers `CLAUDE.md` §9.1 item 26's
"needs a submission/moderation design before any code" gate, with item 26b
(builder channel) as context. Every claim is tagged **[fact]** (cited to
current code or a dated doc) or **[proposal]**.

Owner framing this answers, verbatim: *"we don't have fucking visibility …
we need this kind of new products to increase visibility."* Visibility, not
revenue. Every trade-off below is resolved in that direction.

---

## 0. Recommendation in one paragraph

Build it **light, on the newspaper side, seeded from data we already
hold**, not as another `x402_*` product. **[fact]** The crawler already
ingests awesome-algorand, `algorand.co/case-studies`, DefiLlama and
Pera-verified assets daily and keeps a few hundred approved Algorand
project domains in `domain_tracking` / `service_registry` with liveness
timestamps — an ecosystem directory that nobody can see
(`workers/app/modules/crawler/ecosystem_sync.py:1-20`,
`workers/app/core/config.py:1302-1335`). **[proposal]** v1 = one new
backend module `modules/ecosystem/` shaped like the glossary (admin-curated
public reference pages with SSR + sitemap), plus a free, rate-limited,
human-reviewed submit form shaped like the contact form, plus an embeddable
"Listed on PXke" badge that turns every approved entry into a backlink.
Launch with 100-200 seeded entries, never with zero. **Featured tier: not
in v1** — the mechanism already exists as `x402_board` and can be attached
later with one field; shipping it at launch trades the only asset this
product has (credibility as a real community resource) for a few dollars.
Relay does not solicit submissions in v1.

---

## 1. What already exists (the part that changes the design)

### 1.1 We already consume the thing we'd be publishing

**[fact]** `sync_ecosystem_directories()` fetches
`ECOSYSTEM_DIRECTORY_URLS` (default: the awesome-algorand README on
GitHub), extracts every linked host minus forges/registries/socials/badges
(`_SKIP_HOSTS`, `ecosystem_sync.py:36-92`), liveness-probes each
(`_reachable`, `:104-117`), then approves the domain into the crawl
frontier, stamps `domain_tracking.metadata["ecosystem_listed"]="true"` +
`ecosystem_source=<directory url>`, and registers it as a monitored service
(`_ingest_domain`, `:119-168`; `ensure_monitored_service`,
`domain_tracker.py:818-860`). The same daily beat also ingests
`algorand.co/case-studies` (the institutional/impact class), DefiLlama
protocols and Pera-verified assets' on-chain `url` params
(`sync_ecosystem_case_studies`, `sync_ecosystem_apis`,
`ecosystem_sync.py:247-410`). Beat: `celery_app.py:248-251`.

**[fact]** Admin rejects are sovereign — a listing never resurrects a
`dead_end` domain (`_ingest_domain`, `:131-139`). The listed set is cached
and read by both services as a relevance anchor
(`shared/algorand_shared/ecosystem_directory.py`,
`ARTIFACT_ECOSYSTEM_LISTED_BOOST`, `workers/app/core/config.py:1632-1637`).

**[fact]** What this data lacks for a public directory: a human name
(`service_registry.display_name` is just the domain for auto-registered
rows — `ensure_monitored_service` writes `domain` as the name,
`domain_tracker.py:844-848`), a one-line description, and a category
(`domain_tracking.category` is the crawler's `"service"`, not an ecosystem
taxonomy). What it has that a GitHub README can never have:
`last_online_at` per domain (migration 017, `domain_tracking`), and the
newspaper's own coverage — every article carries a `service_id`
(`backend/app/schemas.py:33`) and there is already a point lookup for it,
`ArticlesStmts.FIND_BY_SERVICE_ID` (SAI query, published-filter applied in
Python — `shared/algorand_shared/article_statements.py:319`,
`article_matching.py:20-25`), so "covered by PXke" needs no new table.

### 1.2 Existing submission / moderation patterns, and which one fits

| Pattern | Where | Shape | Fit for item 26 |
|---|---|---|---|
| Contact form | `backend/app/modules/contact/api/routes.py` | Unauthenticated POST, honeypot field, per-IP hourly Redis budget (fail open), admin-only inbox read, **no outbound email anywhere in the stack** (module docstring) | **Reuse the shape** for the submit route: same three gates, same "status is read from a page, not emailed" consequence. |
| Feature-request free filing | `x402_features/services/rate_limit.py`, `feature_service.py:80-111` | Free, anonymous, per-IP submit budget on its own key via the shared `incr_with_expiry` (`core/rate_limit.py`), `client_ip` from `core/request_headers.py` | **Reuse the helpers** (not contact's older private Redis copy — `core/rate_limit.py`'s docstring says new code reaches for the shared primitive). |
| Crawl-frontier domain approve/reject | `admin/api/routes.py:1533-1620` (`_admin_set_domain_compute`), `DomainsTab.svelte` (683 lines) | Held → admin approve/reject with a reason; approval has side effects (seed crawl, register service); decision logged as classifier training feedback | **Reuse the decision shape** (pending / approved / rejected + reviewer wallet + reason + timestamp). Do not reuse the tab itself — it is crawl-centric. |
| Glossary | `glossary/store.py`, `glossary/api/routes.py`, `admin/api/routes.py:475-523`, `GlossaryTab.svelte` (384 lines), `Glossary.svelte` + `GlossaryTerm.svelte`, `seo/render.py:1075-1153`, `seo/sitemap.py:305-326` | Admin-curated reference entries with `draft`/`published` status, public JSON, SSR pages with schema.org, sitemap entries, per-entry "referenced in" article list, 8-language translation task | **This is the closest overall template**: a curated public reference with real SEO plumbing. Item 26 = glossary + a public intake queue. |
| Classifier review queue | `workers/app/modules/crawler/classifier_review_store.py` | 1-slot gate on page classification, `pending` → completed, blocks the pipeline while pending (`llm_diff_check.py:52`) | **Wrong shape** — it is a pipeline back-pressure gate, not a content queue. Do not reuse. |
| x402_social moderation cases | `x402_social/services/moderation_service.py` (804 lines) | Paid report → community vote → quorum 5 → uphold ratio → consequences; flag-gated off (`x402_social_moderation_enabled=False`) | **Wrong shape** — paid, agent-facing, needs a voter base this directory will not have for months. Do not reuse. |
| x402 directory | `x402_directory/` (2851 lines) | Paid listing, closed category enum projected via a reserved `category:` tag partition (`models/domain.py:44-72`), LWT first insert, ownership by payer, `source: paid / auto_discovered` split | **Reuse two ideas**: closed category enum as a by-category projection; the `source` column so seeded rows and builder-submitted rows are never confused. Not the payment path. |
| x402 board | `x402_board/` (1718 lines) | Paid tile with `boosted_until_epoch`, `category`, free `/go` click redirect with counter + coarse-referrer time series (migration 120) | **This *is* the featured tier**, already built — see §5. |
| Service suggestions | `suggestions/` — `suggestions_enabled=False` | Treasury-ALGO-payment-gated "suggest a service" with wallet upvotes | **Precedent, disabled**: pay-to-submit was built once and never turned on. Do not repeat it for submissions. |

### 1.3 Visibility plumbing that already works

**[fact]** SSR documents for `/x402*` and `/glossary*` are rendered
server-side from the same service-layer reads the JSON routes use, cached
5 min (`seo/api/routes.py:599-660`); glossary pages ship schema.org
`DefinedTerm` + breadcrumbs (`render.py:1118-1153`); static sitemap entries
are appended in `_static_entries` (`sitemap.py:305-326`); `robots.txt`
disallows `/api/` except `/api/v1/img` (`sitemap.py:79-121`); `llms.txt` /
`llms-full.txt` exist (`sitemap.py:122-180`). Memory note
`locale-seo-findings`: translated pages outrank English in GSC. All of
this is directly reusable by adding one index renderer, one detail
renderer, and one sitemap block.

**[fact]** Our own outreach experience of curated directories:
`docs/outreach/gold-402-entry.md` and `docs/outreach/x402-list-submission.md`
(both drafts, never sent) document what a good gate looks like from the
submitter's side — "every submitted endpoint is actually probed before
merge", "one factual sentence, no marketing language", "a failing one gets
a friendly fix-it note, not a silent rejection". That is the acceptance bar
to copy.

**[fact]** Relay/OpenClaw: separate host `92.222.76.121`, posts on Clawstr
and Moltbook to an *agent* audience; its self-reports are embellished
(`docs/x402-moltbook-presence-research.md` §1: claimed cross-posts that the
verified post log shows never happened; repeated "Task completed" for
unverified posts). Treat as leads, not facts.

**[fact]** NFT domains / NFD are referenced only as newspaper research
tooling (`workers/tests/test_nfd_directory_and_app_store_metrics.py`); the
operator's ".algo names as a visibility play" is outside this repo. No
coupling needed.

---

## 2. Positioning — what makes this worth a visitor's click

**[fact]** `github.com/awesome-algorand/awesome-algorand` exists and we
consume it. A clone with the same content gets zero visibility and reads as
a clone. The name "Awesome Algorand" is theirs; ours should not be.
**[proposal]** Call it the **Algorand Ecosystem Directory** at
`algorand.pxke.me/ecosystem`, credit its seeds ("seeded from
awesome-algorand, Algorand Foundation case studies, DefiLlama and Pera
verified assets"), and differentiate on the four things a README cannot do:

1. **Liveness.** "Last seen online <date>" per entry, from
   `domain_tracking.last_online_at` the crawler already maintains for every
   monitored domain — awesome-list rot is real (`ecosystem_sync.py:105`
   cites algoamm.com black-holing). Dead projects sink or get flagged
   automatically; nobody has to prune a markdown file.
2. **Coverage.** Each entry links the newspaper's articles about that
   project (`service_id` join) — "PXke has covered this project 4 times,
   latest 2026-08-30". Articles link back to the entry. This is the loop
   that makes the directory and the newspaper feed each other's SEO.
3. **A page per project, indexed, in 9 locales eventually.** Long-tail
   queries ("<project> algorand", "algorand wallet", "algorand oracle")
   land on us, not on a GitHub README with no per-project URL.
4. **Self-service in hours, not a PR review in weeks.** A builder submits a
   form; a human approves; the page is live. The badge (below) gives them a
   reason to link back the same day.

**[proposal] The badge is the visibility engine.** Serve
`GET /api/v1/ecosystem/:slug/badge.svg` ("Listed on PXke Algorand"), show
the markdown snippet on every approved entry's page and on the approval
status page. Every README that embeds it is a backlink plus an impression
on every repo view — the exact mechanism `awesome.re` badges use (our own
skip-list knows the host, `ecosystem_sync.py:47`). Zero infra, zero
moderation cost, compounding. This is the single highest-leverage item in
the whole design; do not cut it from v1.

**[proposal] Also make it pollable for agents.** `GET /api/v1/ecosystem`
JSON with `?category=` and an `llms.txt` line — the one concrete ask a real
agent gave Relay: "directories win when they expose a simple listing feed
agents can poll" (`x402-moltbook-presence-research.md` §1, reply 2). Costs
nothing beyond the route that the SPA needs anyway.

---

## 3. Submission model

### 3.1 Free, minimal, and honest about what happens next

**[proposal]** `POST /api/v1/ecosystem/submit`, unauthenticated. Required:

| Field | Rule | Why |
|---|---|---|
| `name` | 2-60 chars | display |
| `url` | https, syntactically valid, **reachable at submit time** (GET, 5 s timeout, SSRF-guarded, `< 500` counts as alive — same bar as `_reachable`), not a `_SKIP_HOSTS` social/badge host. GitHub repo URLs **are** allowed (a dev tool's home is its repo). | dead links are the #1 rot vector; check before a human ever sees it |
| `description` | 20-200 chars, one factual sentence, **no HTML** — reuse the reject-don't-strip rule from `x402_social/services/markdown_guard.py` | the gold-402 bar; a reviewer edits wording, never bounces for it |
| `category` | one of the closed enum in §4 | single-partition facet, browsable nav |

Optional: `repo_url`, `tags` (≤5, normalized the way
`listing_service.normalize_tag` does), `x402_url` (if the project has a
paid x402 endpoint — cross-links to item 3's listing when one exists),
`contact` (email **or** wallet; stored private, admin-only, never rendered
— same posture as the contact inbox; not identity verification, so not a
KYB concern), `website` (**honeypot**, hidden — the contact form's
mechanism verbatim, `contact/api/routes.py:84-86`).

Response: `{submission_id, status: "pending", status_url}`. **[fact]**
There is no outbound email in the stack, so the status page
(`GET /api/v1/ecosystem/submissions/:id` → pending / approved+slug /
rejected+reason) *is* the notification. The submit page says so: "Reviewed
by a human, usually within 48 h. Bookmark this link."

### 3.2 Spam / quality gates, cheapest first

1. **Honeypot** — bots fill it, we answer `{ok:true}` and store nothing.
2. **Per-IP hourly submit budget** — `incr_with_expiry` on
   `algorand:ecosystem:submit_rl:<ip>`, default 3/h, fail open with a log
   line (CLAUDE.md §2.9). Its own key and its own setting
   (`ecosystem_submit_rate_limit_per_hour` in `backend/app/core/config.py`).
3. **One entry per registrable domain** — LWT `INSERT IF NOT EXISTS` on
   `domain` (the x402 directory's first-insert pattern). A second
   submission for an existing domain is answered with "already listed /
   already pending: <link>" instead of a new row. Subdomain rule: key on
   eTLD+1 the way `domain_from_url` does, except for shared hosts
   (`github.com/<org>/<repo>`, `*.github.io`) which key on the full path
   host — otherwise every GitHub-hosted tool collides.
4. **Liveness at submit** (above). Reject with a friendly, specific
   message; the submitter fixes and retries.
5. **Admin-reject memory** — a domain the crawler admin has already
   `dead_end`-ed (`domain_tracking`) is auto-rejected with reason
   "previously rejected"; the same sovereignty rule `_ingest_domain`
   applies in reverse.
6. **Human review, always, for anything not already trusted.** Expected
   volume is tens per month, not thousands — the risk here is
   *under*-submission, not flood. A queue an admin clears in five minutes
   a day is the right tool; an automated quality classifier is not.
7. **Fast lane, not auto-publish**: a submission whose domain is already
   `ecosystem_listed` (awesome-algorand / Pera / DefiLlama / case study) or
   already has published articles is flagged "trusted source" in the queue
   so the reviewer can one-click approve. It still passes through a human.

### 3.3 Review queue

**[proposal]** One admin tab, "Ecosystem" in the Content group of
`AdminHub.svelte`, modelled on `InboxTab.svelte` (159 lines) + the
approve/reject decision from the domains flow: filters pending / approved
/ rejected; per row: name, url (opens in new tab), description (editable
inline — the reviewer fixes marketing language rather than bouncing),
category (editable), tags, trusted-source flag, liveness result, existing
coverage count; actions **Approve**, **Reject (reason required)**,
**Delete**. Every decision writes `reviewed_by` (admin wallet, via
`verified_admin_wallet`), `reviewed_at`, `reason`. `require_admin_wallet`
first on every handler (CLAUDE.md §4).

Rejection reasons are a short closed list shown to the submitter on the
status page: `not_algorand` / `unreachable` / `duplicate` / `low_quality
(one-liner needed)` / `spam_or_scam` / `other`. Approvals go live
immediately (SSR cache is 5 min).

### 3.4 Identity, edits, claims

**[proposal]** v1: submissions are anonymous; edits are admin-only;
"request a change" reuses the submit form with an `existing_slug` field
and lands in the same queue. **v1.5**: a builder signs with a wallet via
the existing `auth_nonce` / `auth_verify` session flow
(`modules/auth/api/routes.py:19-74`) to **claim** an entry (seeded or
submitted); the claim is a queue item too; a claimed entry shows a
"maintained by <wallet / NFD name>" line and its owner can edit
description/tags/urls without re-review (url changes re-run liveness).
This is the same claim shape `x402_features` uses (`FEATURE_STATUS_CLAIMED`)
and needs no new auth code. Keep it out of v1 — the launch problem is
content volume, not ownership.

### 3.5 Content rules (put them on the submit page)

- One factual sentence. No "revolutionary", "the first", "best". Reviewer
  rewrites; repeat offenders get `low_quality`.
- Must be an Algorand project *or* a multi-chain project with a live
  Algorand deployment (say which in the description).
- Working URL. Dead → rejected, resubmit when fixed.
- No token-sale pages, no "claim airdrop" pages, no wallet-drainer patterns
  (the crawler's own scam heuristics are a reviewer aid, not a gate).
- Listing is free and stays free. Sponsored placement (if it ever ships) is
  always labelled and never changes whether or where a free entry appears
  in its category.

---

## 4. Category taxonomy

**[fact]** The x402 enums (`LISTING_CATEGORIES`, `BOARD_CATEGORIES`: data /
ai / finance / identity / storage / compute / social / tooling / other) are
for callable paid endpoints and are too coarse for "what does an Algorand
builder look for". **[proposal]** A separate closed enum, one category per
entry (single-partition facet), free tags for everything else, `other` as
the escape hatch. Seventeen top-level categories, ordered as they'd appear
in the nav:

| # | Category (slug) | What goes here — examples of the *kind* |
|---|---|---|
| 1 | Wallets & Key Management (`wallets`) | Mobile/desktop/web wallets, hardware support, multisig, account abstraction, wallet SDKs (Pera, Defly, Lute, Kibisis class) |
| 2 | DeFi — Exchanges & AMMs (`defi-exchange`) | DEXs, AMMs, aggregators, order books, liquidity tooling |
| 3 | DeFi — Lending, Stablecoins & Yield (`defi-lending`) | Lending/borrowing, liquid staking, stablecoin issuers (EURQ/USDQ class), yield, synthetic assets |
| 4 | NFTs & Digital Collectibles (`nfts`) | Marketplaces, minting tools, ARC-3/19/69 tooling, generative/art platforms, shuffles |
| 5 | Gaming & Metaverse (`gaming`) | Games, game engines/SDKs, in-game asset tooling |
| 6 | Identity, Names & Credentials (`identity`) | Name services (NFDomains class), DIDs, verifiable credentials, KYA/reputation, attestations |
| 7 | Real-World Assets & Tokenization (`rwa`) | Real estate, commodities, carbon credits, securities, tokenization platforms |
| 8 | Payments, Commerce & Remittances (`payments`) | Payment rails, POS, remittances, payroll, subscriptions, x402 services |
| 9 | Infrastructure & Node Services (`infrastructure`) | Node/API providers, indexers, RPC, relays, archival, conduit pipelines |
| 10 | Developer Tools & SDKs (`devtools`) | AlgoKit, Algorand Python/Puya, TEALScript, SDKs in every language, IDEs, testing, templates, VibeKit |
| 11 | Explorers, Analytics & Data (`analytics`) | Block explorers, portfolio trackers, dashboards, data APIs, DefiLlama adapters |
| 12 | Governance, DAOs & Community (`governance`) | xGov tooling, DAO frameworks, voting, community hubs, grant programs |
| 13 | Oracles, Bridges & Interop (`interop`) | Price/data oracles, cross-chain bridges, messaging layers |
| 14 | Security, Auditing & Compliance (`security`) | Auditors, monitoring, scam/rug checkers, formal verification, compliance tooling |
| 15 | AI & Autonomous Agents (`agents`) | Agent frameworks, x402-native agents/services, agent marketplaces, LLM tooling on-chain — our own home turf |
| 16 | Enterprise, Impact & Institutional (`enterprise`) | Supply chain, humanitarian/aid (the case-study class), government pilots, CBDC work, corporate integrations |
| 17 | Education, Media & Content (`media`) | Courses, docs projects, newsletters, podcasts, news outlets (PXke itself lives here), communities |
| — | Other (`other`) | escape hatch; reviewer reassigns when a pattern emerges |

Why this shape, versus the rough list in the ask:

- **DeFi is split in two** because "exchange" and "lending/stable" are the
  two things people actually search for, and one bucket would hold a third
  of the directory.
- **Identity is separated from wallets** — NFDomains, credentials and KYA
  are a distinct builder concern and the newspaper covers them as such.
- **RWA, payments, enterprise/impact** get their own rows because that is
  where Algorand's real differentiation and the Foundation's case studies
  sit; a directory that files HesabPay under "DeFi" is not useful.
- **Agents** is its own category on purpose: it is the one category where
  PXke is a participant, and it cross-links directly to item 3's paid
  directory for entries that carry an `x402_url`.
- Seventeen fits a two-row nav on desktop and a scrollable chip row on
  mobile (`X402.svelte` already renders a chip row of tabs).

Orthogonal facets, stored as columns not categories: `stage`
(`live` / `beta` / `sunset`), `open_source` (bool), `x402_enabled` (bool,
derived from `x402_url`), `source` (`seeded` / `submitted` / `claimed` —
the directory's `SOURCE_PAID` / `SOURCE_AUTO_DISCOVERED` lesson: keep
provenance on the row so a later reader cannot confuse them). Map to the
x402 enum only for the cross-link (`payments`/`defi-*` → `finance`,
`identity` → `identity`, `devtools`/`infrastructure` → `tooling`,
`agents` → `ai`, `analytics` → `data`).

---

## 5. Featured tier — defer, and here is the exact hook for later

**Recommendation: not in v1.** Ship a free, admin-chosen `editor_pick`
boolean instead. Revisit the paid tier only after the directory has
organic traffic (GSC impressions on `/ecosystem/*`) and ≥ ~150 approved
entries. Reasons, in order of weight:

1. **Credibility is the product.** A directory that launches with 40
   entries and three "Featured" tiles reads as pay-to-play before it has
   earned anything. The operator's own doctrine for the newspaper already
   says it: *"Readers must always see what is news vs what is paid
   placement"* (`docs/modules/advertisements.md`). The value of item 26 is
   that it is *not* item 3.
2. **The mechanism already exists; there is nothing to build now.**
   **[fact]** `x402_board` is "pay to be seen": paid tile, `category`,
   `boosted_until_epoch`, free `/go` click-through with counter and a
   30-day referrer time series (migration 120). **[proposal]** A featured
   directory entry is *a board placement with an `entry_slug`* — one
   optional field on `StoredPlacement`, validated pre-gate against an
   approved entry, rendered as a clearly labelled **Sponsored** strip above
   the category list (never interleaved, never affecting free order,
   `rel="sponsored"` on the link). It goes through the same
   `require_payment()` gate, the same ledger, the same catalog entry, the
   same promo/probe exclusions. No new protocol code, per §9's rule.
3. **A human builder cannot pay it today.** **[fact]** Every paid route is
   an x402 402-offer flow that needs an x402 client; the "scan with an
   Algorand wallet and pay" human UX is a proposal in
   `docs/x402-marketplace-product-redesign.md`, not shipped. The buyer of
   a featured tile is a human founder, not an agent. Until the human
   pay-flow exists the tier has no buyers.
4. **Wash-volume rule.** PXke's own projects (the newspaper, the x402
   products) must never be "featured" by paying ourselves; an
   `editor_pick` flag has no such problem and lets the operator highlight
   what they find interesting — which is also better editorial.

When it does ship: same price and term as a board tile (config-owned,
`x402_board_*` settings), max N featured per category (default 2), an
expired feature just stops rendering — the free entry underneath never
moves.

---

## 6. Architecture — module versus lightweight

### 6.1 Options considered

| | A. Full `x402_*`-style product | B. Markdown/JSON file in the repo + read route | **C. Glossary-shaped light module (recommended)** |
|---|---|---|---|
| Shape | stores base/memory/cassandra + factory, service, routes, models, catalog entry, admin tab, SPA tab, SSR | one committed file, PR-based curation, route reads it at boot | one Cassandra table + one by-category projection, Protocol store with memory backend for tests, service, 6 routes, one admin tab, two public pages, SSR + sitemap |
| Size (by analogy) | 2.5-3k lines (`x402_directory` 2851, `x402_board` 1718) | ~300 lines | ~1.5-2k lines (glossary is 246 backend + 384 admin + 506 public; add the intake queue) |
| Submissions | API | **PRs into a repo builders cannot see** (origin is `PXke/algorand_service`, single-owner) — or a form that writes a file and needs a deploy per approval | form → queue → approve → live in 5 min |
| Liveness / coverage links | yes | no (static) | yes — reads `domain_tracking` and articles-by-`service_id` |
| Infra | zero new | zero new | zero new (Cassandra tables, Redis rate-limit keys only) |
| Deploy per content change | no | **yes** (`deploy.sh` ships a build) | no |
| Contradicts "visibility, not revenue" | yes — a payment product's ceremony (catalog, promo, probe exclusions) for a free page | no, but it also forfeits every differentiator in §2 | no |

**B looks lightest and is a trap**: the differentiators in §2 (liveness,
coverage links, per-project SSR pages, same-day self-service) are exactly
what a static file cannot do, and a private repo cannot take community
PRs. **A is over-built** for a product with no payment path in v1.

### 6.2 Recommended shape (C)

**Backend** — `backend/app/modules/ecosystem/` (not `x402_*`; not under
`news`). Follow the house `stores/{base,memory,cassandra}.py` + `factory`
pattern so submission/decision logic is unit-testable without Cassandra
(tests are no-network, CLAUDE.md §6). Tables (one migration, add to
`schema/migrations/manifest.toml`, next number after 120):

- `ecosystem_projects (slug PK)` — name, domain, url, repo_url, x402_url,
  description, category, tags set, stage, open_source, source,
  status (`pending`/`approved`/`rejected`), editor_pick, submitted_at,
  reviewed_at, reviewed_by, reject_reason, contact (private), service_id
  (nullable link to `service_registry`).
- `ecosystem_projects_by_domain (domain PK → slug)` — the LWT dedupe key.
- `ecosystem_projects_by_category ((category), approved_at DESC, slug)` —
  written only on approve, deleted on reject/delete; the `?category=` read.
  An `all` pseudo-partition (the x402 directory's `DIRECTORY_PARTITION`
  pattern) for the unfiltered index; same "shard when it grows" note.
- `ecosystem_submissions_by_status ((status), submitted_at DESC, slug)` —
  the admin queue read; a submission moves partition on decision.

Routes: `POST /api/v1/ecosystem/submit` (free; §3), `GET /api/v1/ecosystem`
(approved only, `?category=`, `?tag=`, LIMIT, cached like the SSR reads),
`GET /api/v1/ecosystem/:slug`, `GET /api/v1/ecosystem/:slug/badge.svg`,
`GET /api/v1/ecosystem/submissions/:id`, and admin
`GET /api/v1/admin/ecosystem?status=`, `POST /api/v1/admin/ecosystem/:slug/decision`,
`PUT /api/v1/admin/ecosystem/:slug`, `DELETE /api/v1/admin/ecosystem/:slug`.
Every list has a LIMIT, no `ALLOW FILTERING`, `require_admin_wallet` first.
Settings in `backend/app/core/config.py`: `ecosystem_enabled`,
`ecosystem_submit_rate_limit_per_hour`, `ecosystem_read_rate_limit_per_hour`,
`ecosystem_liveness_timeout_seconds`, `ecosystem_max_tags`.

**Frontend** — `algorand.pxke.me/ecosystem` (index: category chips, search
box over name/description client-side, entry cards with liveness dot and
coverage count), `/ecosystem/:slug` (detail: description, links, category,
tags, stage, "last seen online", "Covered by PXke" article list, x402
cross-link, badge snippet, "suggest a change"), `/ecosystem/submit` (form).
Admin: `routes/admin/tabs/EcosystemTab.svelte`. Nine locale files gain
identical key sets (CLAUDE.md §5); English blurbs only in v1 — the
glossary's per-language translation task
(`translate_glossary_term_task`) is the v1.5 path for 200-char blurbs.
Sanitize nothing via `{@html}` — render text nodes only; badge markdown is
shown in a `<pre>`.

**SEO** — `render_ecosystem_index` (schema.org `ItemList`) and
`render_ecosystem_entry` (`SoftwareApplication` or `Organization` by
category, `sameAs` = url/repo, breadcrumbs), registered next to the
glossary routes in `seo/api/routes.py`; `_static_entries` gains
`/ecosystem` + one entry per approved slug; `_is_known_app_path` learns the
prefix; `llms.txt` gains a line. The badge SVG is fetched by browsers and
GitHub's camo proxy, not by crawlers, so the `/api/` robots disallow is
irrelevant to it.

**Workers** — nothing in v1. Liveness comes from `domain_tracking.
last_online_at` for domains the crawler already monitors; for a submitted
domain the crawler does not know, the submit-time check is the v1 truth
and `ensure_monitored_service` on approval (the same call
`_ingest_domain` makes) puts it under the existing daily watch — so
approval also feeds the newspaper's discovery. That is one call, not a
new beat.

**Domain choice** — the *news* domain, not `x402.pxke.me`: the audience is
human builders and search engines; the product redesign doc reserves the
subdomain for the agent-facing marketplace. One shared backend either way.

### 6.3 Seeding — launch with content, not an empty page

**[proposal]** A one-off admin action (`POST /api/v1/admin/ecosystem/seed`,
idempotent, the `import-discovered` pattern from
`x402_directory/api/routes.py:925-960`) that reads every `service_registry`
row whose domain is `ecosystem_listed` (a few hundred, per the cache
comment in `ecosystem_sync.py:405-406`) and creates **pending** entries
with `source=seeded`, `name = display_name` (mostly the bare domain —
needs a human), `category = other`, `ecosystem_source` recorded. The admin
then works the queue: fix name, pick category, write the one-liner,
approve. To make that tractable, an admin-only "draft blurb" button can
ask DeepSeek for a one-sentence factual description **grounded only in
the crawled homepage text already in `crawled_page_store`**, shown as a
suggestion the reviewer edits — never auto-published (the newspaper's own
fabrication incidents apply here at small scale). Budget: 200 entries ×
one short call ≈ negligible.

Target at launch: 100-200 approved entries across most categories, so the
first visitor sees a directory, not a form.

---

## 7. Relay's role — separate channel in v1, narrow scripted role later

**Recommendation: Relay does not solicit submissions in v1.**

1. **Audience mismatch.** **[fact]** Relay works Clawstr and Moltbook,
   whose population is agents and the humans who run them. Algorand
   builders are on forum.algorand.co, the Algorand Discord, X, and GitHub.
   Relay has no presence on any of those and giving it one is a new
   trust surface.
2. **Reliability.** **[fact]** Relay's self-reports diverge from its
   verified post log (`x402-moltbook-presence-research.md` §1). Every
   solicitation it claimed would need manual verification — more work than
   posting it yourself.
3. **Credibility.** A directory whose entries were solicited by a bot
   reads as spam to exactly the builders it needs; the first fifty entries
   should come from the operator's own hand and from seeds.

**What acquires entries in v1 instead**:

- **Seeds** (§6.3) — the directory is full on day one.
- **The badge** (§2) — each approved builder is handed a reason to link
  back the same day.
- **The "claim / suggest a change" CTA on every seeded page** — a builder
  who searches their own project name finds our page about them and
  completes it. This is the passive engine and it is what SEO pays for.
- **Operator-personal outreach**: one post on forum.algorand.co, one in
  the Algorand Discord builder space, one on X, each linking `/ecosystem`
  and `/ecosystem/submit`. Item **26b** (a PXke-run builder channel) is the
  human acquisition channel that pairs with this product: the directory
  gives the channel a pinned reason to exist, and the channel gives the
  directory its first human submissions. Sequence: directory first (it is
  the thing to point at), channel second.
- **The newspaper**: every article about a listed project links its entry;
  the entry lists the articles.

**Relay's narrow, later role (v1.5+)**, each item verifiable against the
submission log rather than Relay's word:

- When asked "is there a list of Algorand projects / x402 services on
  Algorand?", answer with the `/ecosystem` URL and the JSON feed. Zero risk.
- When Relay encounters an Algorand-based service on Clawstr/Moltbook, it
  may **point them to the submit URL**, never submit on their behalf
  (a Relay-filed submission is indistinguishable from spam and would need
  its own `source=relay` provenance to keep out of rankings — not worth
  building).
- Use the pollable feed itself as the agent-side surface — that is the
  form of "discovery" the one agent that answered Relay actually asked
  for.

---

## 8. v1 scope — explicit in / out

**In**: `modules/ecosystem/` per §6.2; free submit with the §3.2 gates;
admin queue tab with approve/reject/edit/delete; public index + detail +
submit pages; SSR + sitemap + llms.txt; badge SVG; category enum per §4;
`editor_pick`; seed action + grounded blurb-draft helper; coverage links
via `service_id`; liveness via `domain_tracking.last_online_at`;
`ensure_monitored_service` on approve; JSON feed with `?category=`;
regression tests on: honeypot drop, rate limit fail-open, domain dedupe
LWT, liveness reject, admin-reject memory, decision writes projection,
rejected entries never appear in public reads, seeded rows keep
`source=seeded`.

**Out (v1.5 / v2)**: wallet-signed claims and owner edits; paid featured
tier (§5); blurb translations; a dedicated liveness beat; a Typesense index
over entries (client-side filter is fine below ~500); Relay involvement;
a Discord bot; any notion of ranking beyond category + recency +
`editor_pick` first.

---

## 9. Decisions the owner needs to make before code starts

1. **Name and path — DECIDED 2026-09-07**: "Algorand Open Registry", not
   "Algorand Ecosystem Directory" (this doc's earlier recommendation) and
   not "Awesome Algorand". Path recommendation (`algorand.pxke.me/registry`
   or similar under the existing site) still open — pick at build time.
2. **Contact field**: keep an optional private email/wallet on submissions
   (recommended — the only way to answer a rejected submitter), or none.
3. **Seed scope**: seed from all `ecosystem_listed` domains (recommended,
   ~hundreds) or only those with published articles (~dozens).
4. **LLM blurb drafts**: allow the admin-only, grounded DeepSeek draft
   helper (recommended, negligible cost, never auto-publishes) or write
   every seed blurb by hand.
5. **Featured tier**: accept "defer, `editor_pick` in v1, board-attached
   tile later" (recommended), or insist on paid featured at launch.
6. **Category list — DECIDED 2026-09-07**: the 17 categories in §4
   approved, **plus multiple free tags per entry** (already this doc's
   proposed shape — one closed category + free tags, not single-tag) and a
   **liveness check** confirming the listed page actually responds (also
   already in §3's submit-time check — extend to a periodic re-check on
   the same cadence pattern as the x402 directory's probe, not built yet,
   flag as an explicit v1 requirement now that it's owner-confirmed).
7. **Check whether PXke itself is on awesome-algorand.** We consume their
   list; if we are not listed, a PR adding PXke (news + x402 marketplace)
   is the cheapest visibility action available and needs a GitHub account
   the operator holds, not an agent.

---

## 10. Observed, not fixed (pre-existing, outside this design's scope)

- `contact/api/routes.py:52-70` keeps its own `redis.from_url` +
  incr/expire copy; `core/rate_limit.py`'s docstring documents this as a
  deliberate non-migration. New ecosystem code must use the shared
  primitive, not copy contact's.
- `suggestions/` is a disabled, treasury-payment-gated "suggest a service"
  feature (`suggestions_enabled=False`, `VITE_SUGGESTIONS_ENABLED`) with
  its own public page and API client. It is the closest prior art to item
  26 and is dead; worth deciding whether to remove it when item 26 ships so
  two "suggest a project" surfaces do not coexist.
- `ensure_monitored_service` registers auto-discovered services with the
  bare domain as `display_name` (`domain_tracker.py:844-848`); the
  Seeds admin tab therefore shows hundreds of domain-named services. Item
  26's seed pass is a natural moment to backfill real names into
  `service_registry`, but that is a separate, explicit task.
- `render.py`'s `X402_TABS` omits `"endpoints"` while `App.svelte` and
  `_is_known_app_path` special-case it — consistent today, but a new
  `/ecosystem` prefix must be added to `_is_known_app_path` or its SSR
  requests will be recorded as not-found analytics.
