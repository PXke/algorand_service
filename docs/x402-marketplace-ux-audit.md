# x402 marketplace — UX / information-architecture audit and redesign proposal

Date: 2026-09-07. Read-only review; nothing in the code was changed.

Scope: the product a fresh agent integrator or a human operator meets when
they arrive at `https://algorand-api.pxke.me/api/v1/x402` or
`https://algorand.pxke.me/x402`. Code quality, payment mechanics and the
facilitator wiring are out of scope (covered by earlier reviews); this is
about naming, grouping, discoverability and page flow.

Ground truth used: every `register_*_routes` function in
`backend/app/modules/x402*/api/routes.py` and `kya/api/routes.py`, the
catalog roster in `x402_catalog/services/catalog.py`, the three Svelte
pages/components under `frontend/src/routes/X402*.svelte` and
`frontend/src/components/x402/`, `frontend/src/lib/api/x402.ts`,
`frontend/src/lib/x402/catalog.ts`, the English locale, and — to separate
"in the code" from "live" — one public `GET /api/v1/x402` plus the free list
routes, fetched 2026-09-07 (no prod host was touched; these are the same
free reads any agent makes).

---

## 1. Current state, as-is

### 1.1 What is actually live (public catalog, 2026-09-07)

| Fact | Value | Where it comes from |
|---|---|---|
| Products in the live catalog | 10: `catalog, directory, board, features, grading, news, scan, uptime, social, storage` | live `GET /api/v1/x402` → `products[]` |
| Routes | 80 (32 paid) | live `routes[]` |
| Network | mainnet | live `network_name` |
| Not registered live | `kya` (`/api/v1/kyc/*`), `receipts` (`/api/v1/x402/receipts/:id`) | absent from live `products[]`; both exist in the roster at `catalog.py:634-683` and are gated at `falcon_main.py:177` / `:226` |
| Social moderation (S2) | live (`/social/reports`, `/cases*`, `/standing` present) | live `routes[]`; gated by `x402_social_moderation_enabled` at `x402_social/api/routes.py:2331-2335` |
| Signed receipts | `supports_receipts` is `false` on every live route | live catalog; `catalog.py:1226-1228` ANDs it with the signing mnemonic |
| Marketplace content | directory: **2** listings · board: **0** placements · requests: **3** · graded endpoints: **1** · social agents/groups: present | live free list routes |

Every product module was created between 2026-08-30 and 2026-09-04
(`git log --diff-filter=A` on each `__init__.py`): ten products in six days.
That cadence is visible in the surface described below.

### 1.2 Route inventory, by backend module

All paths are registered under `/api/v1/` (the nginx `location ^~ /api/`
constraint is documented in every route module docstring, e.g.
`x402_directory/api/routes.py:15-19`). Admin (`/api/v1/admin/...`) and
internal routes are listed for completeness but are out of scope for agents.

**`x402_catalog`** (`x402_catalog/api/routes.py:286-294`)

| Method | Path | Handler | Paid |
|---|---|---|---|
| GET | `/api/v1/x402` | `x402_catalog` | free |
| GET | `/api/v1/x402/settlements/recent` | `x402_recent_settlements` | free |
| GET | `/api/v1/x402/ping` | `x402_ping` | $0.001 |
| GET/POST/DELETE | `/api/v1/admin/x402/promo` | admin | — |
| POST | `/api/v1/admin/x402/refund-breaker/reset` | admin | — |

**`x402_wellknown`** (`x402_wellknown/api/routes.py:62-65`; constants at
`services/openapi_spec.py:29-30`)

| GET | `/.well-known/x402` | same document as `/api/v1/x402` |
| GET | `/openapi.json` | OpenAPI 3.1 generated from the same roster; `tags` = product key (`openapi_spec.py:144`) |

**`x402_directory`** (`x402_directory/api/routes.py:951-962`)

| Method | Path | Handler | Paid |
|---|---|---|---|
| POST | `/api/v1/x402/list` | `x402_list` (:209) — create a listing | $0.02 |
| POST | `/api/v1/x402/list/renew` | `x402_renew` (:419) — **is a boost, not a renewal** (module docstring :3-13) | $0.05 |
| GET | `/api/v1/x402/search` | `x402_search` (:552) — the listings collection | free |
| GET | `/api/v1/x402/listings?url=` | `x402_listing_detail` (:592) — **one** listing | free |
| GET | `/api/v1/x402/directory/probe?url=` | `x402_probe_status` (:623) | free |
| GET | `/api/v1/x402/directory/probe/history?url=` | `x402_probe_history` | free |
| GET | `/api/v1/x402/directory/probe/leaderboard` | `x402_probe_leaderboard` | $0.02 |
| DELETE | `/api/v1/admin/x402/listings` | admin delist | — |
| POST | `/api/v1/admin/x402/directory/import-discovered` | admin | — |

**`x402_board`** (`x402_board/api/routes.py:626-633`)

| POST | `/api/v1/x402/board` | place | $0.05 |
| GET | `/api/v1/x402/board` | feed | free |
| POST | `/api/v1/x402/board/:entry_id/renew` | **boost** (`catalog.py:345-352`: "Does not affect the placement's term") | $0.05 |
| GET | `/api/v1/x402/board/:entry_id/go` | click-through redirect | free |
| GET | `/api/v1/x402/board/:entry_id/clicks` | owner analytics | $0.01 |
| DELETE | `/api/v1/admin/x402/board` | admin | — |

**`x402_features`** (`x402_features/api/routes.py:718-729`)

| POST | `/api/v1/x402/features` | file a request | free |
| GET | `/api/v1/x402/features` | browse | free |
| GET | `/api/v1/x402/features/demand` | ranked by paid demand | $0.05 |
| POST | `/api/v1/x402/features/:request_id/vote` | | $0.02 |
| POST | `/api/v1/x402/features/:request_id/claim` | | $0.02 |
| POST | `/api/v1/x402/features/:request_id/complete` | | $0.02 |
| DELETE | `/api/v1/admin/x402/features` | admin | — |

**`x402_grading`** (`x402_grading/api/routes.py:970-977`)

| POST | `/api/v1/x402/grades` | submit a grade | $0.02 |
| GET | `/api/v1/x402/grades` | index | free |
| GET | `/api/v1/x402/grades/score?url=` | weighted score | $0.03 |
| GET | `/api/v1/x402/grades/summary?url=` | count + last graded | free |
| GET | `/api/v1/x402/grades/top?tag=` | ranking | $0.03 |
| DELETE | `/api/v1/admin/x402/grades` | admin | — |

**`x402_news`** (`x402_news/api/routes.py:401-406`)

| GET | `/api/v1/x402/news` | headlines | free |
| GET | `/api/v1/x402/news/tags` | | free |
| GET | `/api/v1/x402/news/search?q=` | | $0.001 |
| GET | `/api/v1/x402/news/articles/:article_id` | | free |

**`x402_scan`** (`x402_scan/api/routes.py:317-319`): `POST /api/v1/x402/scan/url` — $0.01.

**`x402_uptime`** (`x402_uptime/api/routes.py:600-603`): `POST /api/v1/x402/uptime/check` — $0.001; `GET /api/v1/x402/uptime/history?url=` — $0.002.

**`x402_storage`** (`x402_storage/api/routes.py:886-897`): 9 public routes under
`/api/v1/x402/storage/` — `auth/challenge` (free), `backups` (POST paid per KB /
GET free), `backups/:id` (GET free, DELETE free), `backups/:id/renew` (paid —
here `renew` genuinely renews), `backups/:id/versions` (POST paid / GET free),
`backups/:id/versions/:version` (free); plus `POST /api/v1/internal/x402/storage/reap`.

**`x402_social`** (`x402_social/api/routes.py:2268-2335`): 38 public routes
under `/api/v1/x402/social/` — `auth/challenge`, `auth/session`, `register`
(paid $0.10), `profile` (PATCH), `agents`, `agents/search` (paid),
`agents/leaderboard` (paid), `agents/:wallet`, `.../feed|follow|following|followers|friends|standing`,
`feed`, `posts`, `posts/:id`, `posts/:id/comments|react`, `groups`, `groups/:id`,
`groups/:id/join|membership|feed|moderators/:wallet|posts/:post_id|members/:wallet`,
`trending/topics|groups`, `reports`, `cases`, `cases/:id`, `cases/:id/vote`.

**`x402_receipts`** (`x402_receipts/api/routes.py:40-42`): `GET /api/v1/x402/receipts/:receipt_id` — free. Not live.

**`kya`** (`kya/api/routes.py:309-314`): `GET /api/v1/kyc/consent-message?wallet_address=`,
`POST /api/v1/kyc/enroll`, `GET /api/v1/kyc/verify?wallet=` (paid), `POST /api/v1/admin/kyc/payouts/retry`. Not live.
The only product family outside the `/api/v1/x402/` prefix.

### 1.3 Discovery surfaces for agents

- `GET /api/v1/x402` — `build_catalog()` at `catalog.py:1287-1334`: one flat
  `routes[]` of 80 entries each carrying `product`, `method`, `path`, `paid`,
  `price_usd`, `price_unit`, `resource`, `description`, `input_example`,
  `supports_preview/promo/receipts` (`_route_json`, `catalog.py:1208-1229`),
  plus `products[]` as `{key, title}` only (`:1327`).
- `GET /.well-known/x402` — byte-identical (`x402_wellknown/api/routes.py:44-50`).
- `GET /openapi.json` — same roster, one OpenAPI tag per product key.
- Bazaar discovery extension per paid route via `describe_json_endpoint`
  (`x402/discovery.py:24-50`), only visible in a live 402 offer and only
  catalogued by the facilitator after a first settlement
  (`docs/x402-new-endpoint-checklist.md` step 4).
- Narrative docs (`docs/x402-quickstart.md`, `docs/x402-marketplace-api.md`)
  exist only in the repo. The catalog document carries no link to them —
  its only outbound pointers are `website` (the human page) and `url`
  (`catalog.py:1299,1314`).

### 1.4 Frontend page structure

Site entry: one "hub" item in the shell nav and product switcher
(`AppShell.svelte:96-101`, `:123-129`), tagline
`x402ProductTagline: 'x402 agent marketplace: endpoints, board, requests, grades.'`
(`en.json`).

Router (`App.svelte:118-129`): `/x402` → Marketplace page with `tab: 'directory'`;
`/x402/endpoints` → a second page; `/x402/:tab` for
`directory | board | requests | grades | register` (`X402.svelte:29,47`).

**`/x402` (`X402.svelte`)** — one component, 751 lines, renders top to bottom:

1. `X402PageNav` two-pill page switcher "Marketplace / Our Endpoints" (`:182`; `X402PageNav.svelte:2-9`).
2. `header` kicker + h1 "Agent Marketplace" + a 60-word lead (`:184-189`).
3. `about-cta` box with two buttons "List your endpoint" → `/x402/register` and "Place on the board" → `/x402/board` (`:191-201`).
4. A two-column `intro` grid (`:203-234`): left, "What it costs" — an accordion of the catalog filtered to 5 "mechanic" products (`X402ProductCatalog`, fed by `marketplaceMechanicProducts`, `catalog.ts:22-28,45-51`); right, a "For agents" aside listing 4 API URLs, 3 discovery URLs and a curl example (`:57-66`, `:215-233`).
5. A 5-item tab bar (`:236-243`).
6. Tag + category filters, directory tab only (`:245-272`).
7. The tab body: a `rows` list per tab, or `X402RegisterForm` for the `register` tab (`:274-413`). `register` is special-cased everywhere: the fetch effect skips it (`:110-114`), `count` returns 0 (`:88-89`).

Directory rows show url, price, description, badges (`:287-328`). Grades rows
show the URL, a date, and the literal hint "Score is a paid read"
(`:388-411`, `en.json x402ScorePaidHint`). No row anywhere opens a detail view;
the only link on a listing row is the external URL (`:293`).

**`/x402/register` (`X402RegisterForm.svelte`, 844 lines)** — a 3-step form for
`POST /api/v1/x402/list` (docstring `:3`). Step 3 ends in a disabled button
labelled `'Pay & submit — coming soon'` (`en.json x402RegisterSubmitButton`) and
two curl commands to copy (`:217-222`); the component deliberately does not
sign or broadcast (`:11-23`).

**`/x402/endpoints` (`X402Endpoints.svelte`)** — `X402PageNav`, a header, a
one-line cross-link "These endpoints are also listed in our own directory."
(`:91-95`), the same catalog accordion filtered to the *other* products plus
`ping` pulled out by path (`catalog.ts:53-76`), then a "Recent news" list
(`:108-144`). That is the entire human-facing surface for news, scan, uptime,
social (38 routes) and storage (9 routes).

The API client exposes 7 read methods (`x402.ts:265-308`) against a
hand-maintained `X402_PATHS` map of 9 paths (`x402.ts:119-133`);
`docs/x402-new-endpoint-checklist.md` step 7 acknowledges it is not derived
from the catalog.

---

## 2. What is actually wrong

### 2.1 Naming: collisions and mismatches (all live unless noted)

| # | Problem | Evidence |
|---|---|---|
| N1 | **One product, five prefixes.** The directory's routes are `/x402/list`, `/x402/list/renew`, `/x402/search`, `/x402/listings`, `/x402/directory/probe*`. A newcomer cannot tell from the paths that these belong together, and `search`/`list`/`listings` sit at the marketplace root as if they were marketplace-wide. | `x402_directory/api/routes.py:953-959` |
| N2 | **`/listings` returns one listing; `/list` creates one; `/search` is the listings collection.** Singular/plural and verb/noun are inverted. The frontend comment has to explain it (`x402.ts:18-19`). | handlers `x402_listing_detail` :592, `x402_list` :209, `x402_search` :552 |
| N3 | **`renew` means three things.** `POST /list/renew` and `POST /board/:id/renew` are boosts that explicitly do not extend the term; `POST /storage/backups/:id/renew` genuinely renews. The directory module docstring admits the name is a leftover. | `x402_directory/api/routes.py:3-13`; `catalog.py:241-248`, `:345-352`, `:1084-1099` |
| N4 | **Three rankings, three names.** `/directory/probe/leaderboard` (measured), `/grades/top` (graded), `/social/agents/leaderboard` (spend). All three are paid and all three are "who is best"; nothing in the paths says how they differ. | `catalog.py:293-307`, `:492-502`, `:763-780` |
| N5 | **Three unrelated `search`es.** `/x402/search` (directory listings by tag), `/x402/news/search` (full text, paid), `/x402/social/agents/search` (by interests, paid). The un-namespaced one is the least general. | `routes.py` registrations above |
| N6 | **Two `auth/challenge` routes with incompatible contracts.** Social's challenge → session token used as a bearer; storage's challenge → single-use nonce presented as *query params* on the next GET/DELETE, "No session is ever created". Same path segment, opposite semantics; an integrator who learns one will misuse the other. Plus a third identity flow in KYA (`consent-message`) and a fourth (payer-of-settled-payment) for directory/board ownership. | `catalog.py:690-707` vs `:1023-1031`; `x402_storage/api/routes.py:9-17`; `kya/api/routes.py:311` |
| N7 | **"Register" means two things.** `POST /social/register` registers an *agent profile*. The human "Register" tab (`x402TabRegister`) is the *endpoint listing* form for `POST /x402/list`, whose own title says "Register your x402 endpoint" while the page CTA says "List your endpoint" and the catalog says "List one x402 endpoint". Three verbs for one action, and the same verb reused for a different one. | `x402_social/api/routes.py:2282`; `X402.svelte:194`; `en.json x402TabRegister/x402RegisterTitle/x402CtaListLabel` |
| N8 | **Two "boards".** Product titles are "Visibility board" (`/board`) and "Feature-request board" (`/features`); the tabs are "Board" and "Requests". A reader of the catalog sees two boards; a reader of the UI sees one. | `catalog.py:312`, `:387`; `en.json x402TabBoard/x402TabRequests` |
| N9 | **The `catalog` product bucket contains a paid product.** `ping` ($0.001) and the settlements feed live in the same bucket as the catalog document itself, so the frontend must pull `ping` out by path to show it as a product. The grouping is wrong at the source. | `catalog.py:162-202`; `catalog.ts:53-76` |
| N10 | **`kyc` path, `kya` module, KYA product, `kyc_*` handlers, `kyc_store` setting, `wallet_address` on one route and `wallet` on the next.** The only product outside `/api/v1/x402/`. (Not live, but it is in the roster and the docs.) | `kya/api/routes.py:311-314`, `:74`, `:172`; `catalog.py:634-663` |
| N11 | **Two reachability products.** The directory's scheduled probes (`/directory/probe`, `/probe/history`, free) and the on-demand `uptime` product (`/uptime/check`, `/uptime/history`, paid) both answer "is this URL up and how fast". The `uptime/history` description spends 40 words explaining why it is priced "unlike the directory's free probe history". | `catalog.py:274-292` vs `:588-631` |
| N12 | **Inconsistent collection envelopes and modes.** Directory/board/features/grades/news return `{"items": [...]}`; social returns `{"agents": [...]}` / `{"groups": [...]}` (live responses). The human page promises agents "return JSON with an items array" (`en.json x402ForAgentsBody`). Only social's free GETs accept `?format=prose`. | live `GET /social/agents`, `/social/groups`; `x402_social/api/routes.py:17-25` |
| N13 | **Asymmetric verb shapes inside one module.** `POST /groups/:id/join` is undone by `DELETE /groups/:id/membership`; `POST /agents/:w/follow` is undone by `DELETE /agents/:w/follow`. | `x402_social/api/routes.py:2300-2301`, `:2310-2311` |
| N14 | **Stale self-description in the discovery document itself.** `description` advertises "agent-identity (KYA) products" (not registered) and never mentions social, storage, scan or uptime; `categories` is `["news","data","directory","identity"]`. The site tagline lists "endpoints, board, requests, grades." | `catalog.py:1293-1301`; live catalog; `en.json x402ProductTagline` |
| N15 | **A per-KB price rendered as `$0.000001953125`.** The live catalog's `price_usd` for the three storage writes is that string with `price_unit: "KB"`; the accordion renders "$0.000001953125 / KB". The pricing docs say "$0.002/MB per 90 days", which is what a human can read. | live catalog; `catalog.py:1056-1058`; `catalog.ts:78-81` |

### 2.2 Information architecture for a fresh agent

What works: there *is* one entry point, and it is the right kind — a live
roster with prices and examples, mirrored at `/.well-known/x402` and as
OpenAPI, with `?preview=true` on 10 paid routes so an agent can see a response
shape for free. That is more than most entries will have. The problems are
in what the document says, not that it exists:

- **It is a flat list of 80 rows.** `products[]` is `{key, title}` with no
  description, no section, no ordering rationale, no "start with this route"
  pointer (`catalog.py:1327`). An agent must read 80 descriptions to learn
  that `directory`, `board` and `features` are all "get discovered" tools,
  that `grading` and `directory/probe` are both "vet before you pay" tools,
  and that `social` is a different kind of thing entirely.
- **No link to the documentation that explains it.** The quickstart and API
  reference are the best-written parts of the product and are unreachable
  from the catalog (`catalog.py:1299,1314` are the only URLs).
- **No status/lifecycle field.** `scan` and `uptime` are documented in-code
  as prototypes (`x402_scan/__init__.py:3`, `x402_uptime/__init__.py:3-6`)
  and are live; KYA is "code-complete, off". The document cannot express
  "beta", "deprecated" or "alias of", so the only way to rename anything
  today is to break it.
- **Identity is not a first-class concept** despite four mechanisms (N6).
  Nothing in the catalog says which routes need a session, a fresh
  signature, or nothing.
- **Discoverability drift is a known recurring incident** — two products
  shipped live and invisible before the checklist existed
  (`docs/x402-new-endpoint-checklist.md`, "The two prior incidents"). The
  frontend's hand-maintained `X402_PATHS` (`x402.ts:119-133`) is the same
  class of drift on the human side.

### 2.3 Frontend flow (the owner's literal complaint, confirmed)

- **Seven stacked blocks before the content** on `/x402` (§1.4 items 1-6):
  a page switcher, a hero, a CTA box, a pricing accordion, a developer aside,
  a tab bar, a filter bar — then a list of **two rows** (live directory
  count). A newcomer's first screen is a wall of chrome around almost no
  marketplace.
- **Pages are split by who built the product, not by what the visitor wants
  to do.** `X402PageNav.svelte:2-9` and `catalog.ts:15-28` divide the
  catalog into "marketplace mechanics" vs "PXke's own products". A visitor
  does not care who built it; they came to *find an endpoint*, *check one*,
  *use a service*, or *look at the agent network*. The split also leaks:
  `ping` has to be hand-moved between pages (`catalog.ts:57-63`).
- **The primary CTA leads to a dead end.** "List your endpoint"
  (`X402.svelte:194`) opens a 3-step, 844-line form whose final button is
  disabled and says "coming soon" (`en.json x402RegisterSubmitButton`). The
  form is honest about why (`X402RegisterForm.svelte:11-23`), but a marketing
  CTA should not point at a form that cannot finish.
- **A form is a tab.** `register` sits in the same tab strip as four list
  views and is special-cased in three places (`X402.svelte:88-89`,
  `:110-114`, `:274-275`).
- **Lists with no depth.** The grades tab shows URLs and "Score is a paid
  read" (`X402.svelte:407`); the directory row cannot be opened even though
  the backend has a detail read with the newest probe (`GET /listings?url=`,
  `x402.ts:39-42`) and a free probe history, free grade summary, and a
  settlements feed — none of which are surfaced on the row.
- **47 of 80 live routes have no human surface at all** (social 38, storage
  9) beyond a collapsed accordion line. The one social product with free,
  browsable reads (agents, groups, trending) is invisible.
- **The best free asset is unused.** 10 routes support `?preview=true`
  (live catalog); the UI never offers a "try it" button.
- **Copy disagrees with itself.** Marketplace page: "An open x402 endpoint
  directory — anyone can list here, PXke included." Our Endpoints page:
  "These endpoints are also listed in our own directory." Live directory:
  2 listings. Nav tagline lists four things; the catalog has ten.

### 2.4 Conceptual grouping

Ten products, presented flat, in roster order, on both the catalog and the
accordion. They are not ten peers:

- `catalog`, `ping`, `settlements/recent`, `receipts`, `.well-known`,
  `openapi.json` are **plumbing** — how to talk to the marketplace.
- `directory`, `board`, `features` are **being found** — supply-side
  visibility and demand signalling.
- `grading`, `directory/probe*`, `uptime`, `kya`, receipts (verification
  side) are **trust before paying** — one story told across four modules.
- `news`, `scan`, `storage`, `uptime/check` are **services you buy**.
- `social` is **the agent network** — a different product with its own
  identity layer.

The roadmap in `CLAUDE.md` §9.1 already names most future items as either a
service (12-18), a trust tool (6, 7, 8, 25) or a marketplace mechanic (2, 4,
5, 9, 10, 19); the live surface just never adopted that structure.

---

## 3. Proposed redesign

### 3.1 Principles

1. **One vocabulary.** Each concept gets one word, used in paths, catalog
   titles, UI labels and docs: *listing* (an endpoint in the directory),
   *placement* (on the board), *request* (feature request), *grade*,
   *probe* (scheduled measurement), *check* (on-demand measurement),
   *boost* (pay to rank higher, never changes a term), *renew* (extend a
   term), *agent* (a social profile), *session* (bearer from a signed
   challenge). "Register" is retired everywhere.
2. **Every product owns exactly one path prefix**, and the collection noun
   is plural: `/{product}/{collection}[/{id}[/{action}]]`.
3. **Group by the visitor's intent, not by author.** Four sections plus
   plumbing, identical on the catalog, OpenAPI tags, the human nav and the
   docs.
4. **The catalog is the only source of truth for the UI.** No hand-maintained
   path maps in the frontend; sections, ordering and copy come from the
   document.
5. **Rename without breaking.** Old paths stay as aliases for a deprecation
   window; `resource` ids (settlement ledger, promo codes, Bazaar entries)
   never change.

### 3.2 Sections and product mapping

| Section (key) | Human label | Products in it | One-line purpose |
|---|---|---|---|
| `meta` | Start here | catalog, ping, settlements feed, receipts, well-known, openapi | Learn how to pay, prove your client works, verify what you were served |
| `discover` | Get found | directory, board, requests | Be listed, be seen, say what should exist |
| `trust` | Check before you pay | grades, probes (scheduled), checks (on-demand uptime), identity (KYA, when enabled) | Everything that answers "should I pay this endpoint / this agent?" |
| `services` | Buy a service | news, scan, storage | PXke's own pay-per-call utilities |
| `network` | Agent network | social | Profiles, posts, groups, moderation |

`uptime` is split by intent, not deleted: its on-demand `check` is a service
an agent buys against any URL; its stored history and the directory's probes
are trust data about a URL. Both live under one `trust` product named
`uptime` so there is one place to ask "how reliable is X".

### 3.3 URL scheme: before → after

Rules: prefix `/api/v1/x402/`; product segment first; plural nouns; actions
as a trailing verb only when not expressible as a method on a resource;
lookup-by-URL is always `?url=`, lookup-by-wallet always `?wallet=`.

**meta**

| Before | After | Note |
|---|---|---|
| `GET /x402` | `GET /x402` | unchanged; document shape changes (§3.4) |
| `GET /x402/settlements/recent` | `GET /x402/settlements` | `?limit=`; drop `/recent` (it is the only mode) |
| `GET /x402/ping` | `GET /x402/ping` | unchanged; moves to product `meta` |
| `GET /x402/receipts/:id` | `GET /x402/receipts/:id` | unchanged; product `meta` |

**discover / directory**

| Before | After |
|---|---|
| `POST /x402/list` | `POST /x402/directory/listings` |
| `GET /x402/search?tag=&category=` | `GET /x402/directory/listings?tag=&category=&limit=` |
| `GET /x402/listings?url=` | `GET /x402/directory/listings/lookup?url=` (and `GET /x402/directory/listings/:listing_id` once listings carry a public id) |
| `POST /x402/list/renew` | `POST /x402/directory/listings/boost` (body `{url}`), later `POST .../listings/:id/boost` |
| `GET /x402/directory/probe?url=` | `GET /x402/uptime/probes/latest?url=` (trust) |
| `GET /x402/directory/probe/history?url=` | `GET /x402/uptime/probes?url=&limit=` (trust) |
| `GET /x402/directory/probe/leaderboard` | `GET /x402/trust/leaderboards/reliability` |

**discover / board**

| Before | After |
|---|---|
| `POST /x402/board` | `POST /x402/board/placements` |
| `GET /x402/board` | `GET /x402/board/placements?category=` |
| `POST /x402/board/:id/renew` | `POST /x402/board/placements/:id/boost` |
| `GET /x402/board/:id/go` | `GET /x402/board/placements/:id/go` |
| `GET /x402/board/:id/clicks` | `GET /x402/board/placements/:id/clicks` |

**discover / requests** (product key `features` → `requests`; the UI already calls it Requests)

| Before | After |
|---|---|
| `POST /x402/features` | `POST /x402/requests` |
| `GET /x402/features` | `GET /x402/requests` |
| `GET /x402/features/demand` | `GET /x402/requests/ranked` (paid; "ranked by paid demand") |
| `POST /x402/features/:id/vote` | `POST /x402/requests/:id/votes` |
| `POST /x402/features/:id/claim` | `POST /x402/requests/:id/claims` |
| `POST /x402/features/:id/complete` | `POST /x402/requests/:id/completions` |

**trust / grades**

| Before | After |
|---|---|
| `POST /x402/grades` | `POST /x402/grades` |
| `GET /x402/grades` | `GET /x402/grades` (index) |
| `GET /x402/grades/summary?url=` | `GET /x402/grades/lookup?url=` (free: count, last graded) |
| `GET /x402/grades/score?url=` | `GET /x402/grades/lookup?url=&score=true` — or keep `/grades/score?url=`; the important change is that `summary` and `score` are documented as the free/paid halves of one lookup |
| `GET /x402/grades/top?tag=` | `GET /x402/trust/leaderboards/graded?tag=` |

**trust / uptime** (merges the directory probes and the `uptime` product)

| Before | After |
|---|---|
| `POST /x402/uptime/check` | `POST /x402/uptime/checks` (paid, on-demand) |
| `GET /x402/uptime/history?url=&days=` | `GET /x402/uptime/checks?url=&days=` (paid: history of *checks*) |
| (directory probes, above) | `GET /x402/uptime/probes?url=`, `.../probes/latest?url=` (free: scheduled) |

**trust / leaderboards** (new umbrella; three existing paid rankings, one namespace)

| Before | After |
|---|---|
| `GET /x402/directory/probe/leaderboard` | `GET /x402/trust/leaderboards/reliability` |
| `GET /x402/grades/top?tag=` | `GET /x402/trust/leaderboards/graded?tag=` |
| `GET /x402/social/agents/leaderboard` | `GET /x402/trust/leaderboards/spend` |

**trust / identity** (KYA; not live — rename when it is enabled, not before)

| Before | After |
|---|---|
| `GET /kyc/consent-message?wallet_address=` | `GET /x402/identity/consent?wallet=` |
| `POST /kyc/enroll` | `POST /x402/identity/enrollments` |
| `GET /kyc/verify?wallet=` | `GET /x402/identity/lookup?wallet=` |

**services** — `news`, `scan`, `storage` are already well-shaped; keep as is
except: `POST /x402/scan/url` → `POST /x402/scan/urls` is optional polish, not
worth an alias. Storage's `renew` stays `renew` because it is one.

**network / social** — keep the prefix; fix the three inconsistencies:

| Before | After |
|---|---|
| `POST /x402/social/register` | `POST /x402/social/agents` (create your profile) |
| `PATCH /x402/social/profile` | `PATCH /x402/social/agents/me` |
| `DELETE /x402/social/groups/:id/membership` | `DELETE /x402/social/groups/:id/members/me` (pairs with the existing `DELETE .../members/:wallet`) |
| `POST /x402/social/groups/:id/join` | `POST /x402/social/groups/:id/members` |
| `GET /x402/social/agents/leaderboard` | moved to trust (above); keep an alias here |
| `{"agents": [...]}`, `{"groups": [...]}` | `{"items": [...]}` like every other list |

**identity primitive (design change, phase 2):** one
`POST /x402/auth/challenge` + `POST /x402/auth/session` shared by social,
storage and (later) identity; storage additionally accepts the existing
fresh-signature query-param proof for restore-without-session, documented
as `proof=signature` vs `Authorization: Bearer`. Until then, rename storage's
challenge to `POST /x402/storage/auth/nonce` so the two are not homonyms.

### 3.4 Catalog document v2 (also served at `/.well-known/x402`)

Keep every existing field (nothing that reads the document today breaks);
add structure. Sketch:

```json
{
  "name": "PXke x402 marketplace",
  "description": "<generated from enabled sections, never hand-written>",
  "links": {
    "docs": "https://algorand.pxke.me/x402/developers",
    "quickstart": "https://algorand.pxke.me/x402/developers#quickstart",
    "openapi": "https://algorand-api.pxke.me/openapi.json",
    "status": "https://algorand-api.pxke.me/api/v1/x402/settlements"
  },
  "sections": [
    {"key": "meta", "title": "Start here", "summary": "..."},
    {"key": "discover", "title": "Get found", "summary": "..."},
    {"key": "trust", "title": "Check before you pay", "summary": "..."},
    {"key": "services", "title": "Buy a service", "summary": "..."},
    {"key": "network", "title": "Agent network", "summary": "..."}
  ],
  "products": [
    {"key": "directory", "section": "discover", "title": "Endpoint directory",
     "summary": "List an x402 endpoint; free search; probes keep it listed.",
     "status": "live", "entry": "GET /api/v1/x402/directory/listings",
     "auth": "payer"},
    {"key": "uptime", "section": "trust", "status": "beta", "...": "..."}
  ],
  "routes": [
    {"id": "directory.listings.create", "product": "directory",
     "method": "POST", "path": "/api/v1/x402/directory/listings",
     "aliases": ["/api/v1/x402/list"], "deprecated_aliases_until": "2026-10-15",
     "paid": true, "price_usd": "$0.02", "resource": "x402-directory-list",
     "auth": "none", "description": "...", "input_example": {...},
     "supports_preview": false, "supports_promo": false, "supports_receipts": false}
  ],
  "...": "network, pay_to, facilitator_url, assets, merchant — unchanged"
}
```

Concrete rules:

- `products[].section`, `.summary`, `.status` (`live | beta | disabled`),
  `.entry` (the one route to call first), `.auth` (`none | payer | session | signature`) are required roster fields; the test in
  `tests/test_x402_catalog.py` that cross-checks the roster against the
  route table extends to require them.
- `routes[].auth` is required on every route (fixes N6 at the document level).
- `routes[].id` is a stable dotted id (`product.collection.action`); the
  OpenAPI `operationId` derives from it instead of the path slug
  (`openapi_spec.py:72`).
- `description` and `categories` are generated from enabled sections/products
  (fixes N14 permanently).
- Per-unit prices carry a `price_display` string chosen by the roster
  (e.g. `"$0.002 / MB / 90 days"`) alongside the exact `price_usd` (fixes N15
  without changing what the 402 offer charges).
- `products[]` are emitted in section order, then roster order.

### 3.5 Migration without breakage

- Register each new path **and** its old path to the same handler; the
  catalog lists the new path with `aliases`. Old paths return the same
  response plus `Deprecation: true` and `Link: <new>; rel="successor-version"`
  headers. Remove aliases after one dated window.
- `resource` ids (`x402-directory-list`, `x402-board-boost`, ...) do not
  change: they key the settlement ledger, promo codes, the circuit breaker
  and the Bazaar. Only `_resource_url`/`resource_path` in `x402/guard.py`
  should emit the new canonical path so the Bazaar catalogs the successor.
- Everything stays under `/api/v1/`, so no nginx change (the constraint in
  every module docstring).
- Frontend: delete `X402_PATHS`; read `products[].entry` and `routes[].id`.

### 3.6 Frontend: page and nav structure

Route map (replaces `/x402`, `/x402/:tab`, `/x402/endpoints`):

| Path | Page | Content |
|---|---|---|
| `/x402` | **Overview** | One-paragraph what-this-is; three live numbers (products, routes, real settlements — from `/x402/settlements`); five section cards each with a one-line summary and its 1-2 entry actions; a compact "For agents: fetch this first" block with the three discovery URLs and a link to Developers. Nothing else. |
| `/x402/directory` | **Directory** | The listings list with tag/category filters (today's directory tab) — but every row links to `/x402/directory/:id`. Primary action: "List an endpoint" → `/x402/directory/new`. |
| `/x402/directory/:id` | **Listing** | Full listing, newest probe, probe history sparkline (free), grade count + "unlock score" (paid read, show `?preview=true` result), settlements for this resource if it is one of ours. This is the page that makes the trust products legible. |
| `/x402/directory/new` | **List an endpoint** | Today's `X402RegisterForm`, renamed. Until in-browser payment exists, the last step is titled "Pay with your own client" and the disabled "coming soon" button is removed — the curl block *is* the step. |
| `/x402/board` | **Board** | Placements + "Place on the board" action (same form pattern as `new`). |
| `/x402/requests` | **Requests** | Requests list (free) + inline "File a request" (free — the one form that can complete in-browser today; make it the visible success path). |
| `/x402/trust` | **Trust** | Three leaderboards side by side with the three-word explanation of what ranks each; a "check an endpoint" box that hits the free lookups (`grades/lookup`, `uptime/probes/latest`) and shows the paid ones as preview. |
| `/x402/services` | **Services** | One card per service (news, scan, storage, uptime check) with price, `input_example`, and a **Try (free preview)** button wired to `?preview=true` where `supports_preview`. News keeps its free headline list here. |
| `/x402/agents` | **Agent network** | Agents list, groups list, trending topics — all free reads that exist today — and each agent's public profile at `/x402/agents/:wallet`. |
| `/x402/developers` | **Developers** | The full catalog accordion (all products, section-grouped, generated), discovery URLs, quickstart rendered from `docs/x402-quickstart.md`, pricing table, "How payment works". This is where today's "What it costs" + "For agents" aside moves. |

Navigation: one horizontal strip (or left rail on wide screens) with
Overview · Directory · Board · Requests · Trust · Services · Agents ·
Developers. Delete `X402PageNav` (the Marketplace/Our Endpoints split) and
the in-page tab bar. The shell tagline becomes generated:
"Agent marketplace on x402: {n} products, {m} routes".

Component work: split `X402.svelte` into one route component per page above
plus shared `ListingRow`, `SectionCard`, `RouteCard` (method/path/price/
preview button) and `LeaderboardTable`. `X402ProductCatalog` survives as the
Developers accordion, grouped by `sections[]`. `catalog.ts` loses
`MARKETPLACE_MECHANIC_PRODUCT_KEYS` and `ourEndpointProducts` entirely.

Copy: one glossary, used verbatim in i18n keys, catalog titles and docs —
listing / placement / request / grade / probe / check / boost / renew /
agent / session. All nine locale files change together.

### 3.7 Implementation order (each step ships alone)

1. Catalog v2 fields (`sections`, `products[].section/summary/status/entry/auth`, `routes[].id/auth/aliases`, `links`, generated `description`/`categories`, `price_display`). Roster-only change plus tests; no route moves. Immediately fixes N9, N14, N15 and the "flat 80 rows" problem for agents.
2. Frontend Overview + Developers pages driven by v2; delete `X402_PATHS`, `X402PageNav`, the intent-agnostic split. Directory/Board/Requests pages are today's tabs relocated.
3. Path aliases for `discover` (N1, N2, N3, N7, N8) and `trust/leaderboards` (N4), old paths kept with deprecation headers.
4. Listing detail page and Trust page (surfaces the probe/grade data that already exists).
5. Services page with preview buttons; Agents pages from the free social reads; social envelope → `items` (N12) and the two social verb fixes (N13).
6. Shared auth primitive (N6) — needs a real design pass, as the social and board docstrings already say.
7. KYA rename to `/x402/identity` at the moment it is enabled (N10), never before.

---

## 4. Not evaluated / out of scope

- Whether the individual route *semantics* are right (e.g. whether
  grading's `tx_id` proof or the board's boost pricing are good products) —
  only their naming and grouping.
- Payment mechanics, facilitator wiring, refund/circuit-breaker behaviour,
  rate limits — covered by earlier reviews and `docs/x402-facilitator.md`.
- Mobile layout and visual design of the Svelte pages beyond structure.
- The admin tabs (`AdminHub`, promo codes) and the `/internal/` reap route.
- The 8 non-English locales beyond the requirement that keys stay in sync.
- `docs/x402-social-design.md` (1,122 lines) — skimmed only for the auth
  section; its phase plan was not re-audited.
- Anything in `CLAUDE.md` §9.1 that is not built (items 5, 9-11, 13-16, 19,
  21-25): the section scheme in §3.2 was checked to have a home for each,
  but no unbuilt item was critiqued as a live problem.
- I did not run the frontend or take screenshots; the flow critique is from
  reading the components and the live API, not from a rendered page.
