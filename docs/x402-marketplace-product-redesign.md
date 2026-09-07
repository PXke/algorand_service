# x402 marketplace — product redesign: boundaries, flows, subdomain

Date: 2026-09-07. Read-only design pass; no code was changed. Builds on
[x402-marketplace-ux-audit.md](x402-marketplace-ux-audit.md) (same day),
which holds the full 80-route inventory and the naming critique — that
inventory is not repeated here, only re-sorted.

Owner framing this answers (verbatim intent): split into *big products*
(News, Marketplace, Social) and *single-use x402 endpoints* that live
inside the Marketplace catalog; registration should be "scan with an
Algorand wallet and pay", not a form; the marketplace gets its own
subdomain, `x402.pxke.me` (decided, not debated here).

Two constraints added mid-task by the operator, applied throughout:

1. **One shared backend API.** The subdomain is a frontend/nav/design
   separation only. Every domain calls the same backend at
   `algorand-api.pxke.me` (one gunicorn on `:9080` behind one nginx
   upstream, `deploy/nginx/algorand-platform.conf:6`; `docs/ops-hosts.md:44-59`).
   No second API deployment, no per-product API host.
2. **One design system.** Shared Svelte components, tokens, typography and
   layout patterns across News and Marketplace, so the two domains read as
   one company's products.

Every claim below is tagged as **[fact]** (cited to current code) or
**[proposal]**. Roadmap items from `CLAUDE.md` §9.1 are named as such.

---

## 1. Product boundaries

### 1.1 The three products, and the substrate under them

| Product | Public domain | Audience | What it is |
|---|---|---|---|
| **News** | `algorand.pxke.me` | humans (+ agents via the paid News Engine) | The newspaper: articles, topics, glossary, SSR pages, feeds. Unchanged by this document. |
| **Marketplace** | `x402.pxke.me` (new) | agents first, humans second | Where x402 endpoints are listed, found, vetted and bought — third-party listings *and* PXke's own pay-per-call services, in one catalog with categories. |
| **Social** | none (API only, on purpose) | agents only | Wallet-identity profiles, posts, groups, moderation — `x402_social`. No human UI now, by owner decision; this document proposes none. |

Under all three sits one **substrate** that is not a product and must stay
single: one backend, one `payTo`, one `require_payment()` gate
(`backend/app/modules/x402/guard.py:108-221`), one settlement ledger
(`x402/settlement.py:57-74`: tx id, asset, amount, payer, resource, UTC,
EUR value), one facilitator merchant, one catalog/`.well-known/x402`
document (`x402_catalog/services/catalog.py:1287-1334`), one Cassandra +
Redis. **[fact]** all of this already exists once; **[proposal]** it stays
once — a product boundary is a *domain and a nav*, never a fork of the gate,
the ledger or the API.

### 1.2 Module → product sort (every module from the audit's §1.2)

| Backend module | Routes (audit) | Product | Role inside the product |
|---|---|---|---|
| `x402_catalog` | `GET /x402`, `/settlements/recent`, `/ping`, admin promo/breaker | **substrate** (merchant manifest) — surfaced on Marketplace *Developers* page | The document describes everything PXke sells under one `payTo`, social included. It is the merchant's manifest, not the marketplace's directory. |
| `x402_wellknown` | `/.well-known/x402`, `/openapi.json` | substrate | Same document, discovery URIs. Mirrored on both site hosts today (`algorand-platform.conf:366-372`, `:652`). |
| `x402_receipts` | `/x402/receipts/:id` (not live) | substrate | Verification of a settlement; belongs with the ledger. |
| `x402_directory` | list, boost, search, detail, probe, probe history, leaderboard | **Marketplace — core** | The listings store: third-party endpoints (`source: "paid"`), facilitator-imported stubs (`source: "auto_discovered"`, `x402_directory/api/routes.py:163-173`). This is *the* marketplace. |
| `x402_board` | place, feed, boost, go, clicks | Marketplace — "get seen" | Paid visibility tiles. Secondary. |
| `x402_features` | file, browse, demand, vote, claim, complete | Marketplace — "say what should exist" | Demand board. Secondary. |
| `x402_grading` | grade, index, score, summary, top | Marketplace — trust layer | Paid opinion about listed endpoints. |
| `x402_uptime` | check, history | Marketplace — **listed service** (category `tooling`) and trust layer (history) | Single-use endpoint. |
| `x402_scan` | scan/url | Marketplace — listed service (`tooling`) | Single-use endpoint. |
| `x402_storage` | 9 backup routes | Marketplace — listed service (`storage`) | Single-use endpoint with its own signature auth. |
| `x402_news` | headlines, tags, search, article | Marketplace — listed service (`data`), **data owned by News** | The News Engine is a Marketplace *listing* whose content comes from the newspaper. The product boundary is: News owns the articles; Marketplace owns the paid access route. |
| `kya` (`/api/v1/kyc/*`, off) | consent, enroll, verify | Marketplace — trust layer, when enabled | Roadmap item 8. Not live; nothing here depends on it. |
| `x402_social` | 38 routes | **Social** | Own product. Only its *existence* (prefix + entry route) is mentioned on the Marketplace Developers page. |
| `x402` (gate, client, promo, refund, replay, receipts, price oracle) | — | substrate | Never per-product. |

Consequence for the owner's point 5 **[proposal]**: `news`, `scan`,
`uptime`, `storage` (and `ping`) stop being "Our Endpoints" on a separate
page (`frontend/src/routes/X402Endpoints.svelte`, `X402PageNav.svelte:2-9`,
`lib/x402/catalog.ts:22-28`). They become **rows in the directory**, each
with a category and a `PXke` operator badge, alongside third-party
listings. Mechanically that means an *operator* source for directory rows
(today's enum is `paid` / `auto_discovered`, `routes.py:163-173`), inserted
by a seed/beat with **no payment** — an operator row is free and labelled,
so it is not wash volume (CLAUDE.md §9: nothing pays our own endpoints from
our own wallets except the labelled probe). The catalog roster
(`catalog.py:162-1100`) stays the source of truth for *our* prices; the
directory row for a PXke service should be derived from the roster, not
hand-typed, so the two cannot drift.

### 1.3 What is deliberately *not* split

- **The API path prefix.** `/api/v1/x402/*` stays as is on
  `algorand-api.pxke.me`, social included. Renaming social to
  `/api/v1/social/` would make the product boundary visible in URLs, but it
  is the audit's §3.5 alias problem again (ledger `resource` ids, Bazaar
  entries keyed on absolute URL, `guard.py:95-105`). Optional, later, never
  as part of the subdomain move.
- **The catalog document.** One document for one merchant. It gains
  `sections` (audit §3.4) so Social is a section, not a stranger, but it is
  not split into per-product manifests — a Bazaar/peer-directory crawler
  expects one `.well-known/x402` per merchant.
- **A social domain.** Agent-only product → API host only. Do not reserve
  or wire `social.pxke.me` until a human surface is decided.

---

## 2. The three real flows

### 2.1 Flow A — an agent discovering endpoints (API-first)

**Current state [fact].**

- Entry: `GET https://algorand-api.pxke.me/api/v1/x402` → `build_catalog()`
  (`catalog.py:1287-1334`): flat `routes[]` (80), `products[]` as
  `{key,title}` only (`:1327`), hand-written `description` and `categories`
  (`:1293-1301`) that still advertise KYA and omit social/storage/scan/uptime.
- Mirrors: `/.well-known/x402`, `/openapi.json` (`x402_wellknown`).
- The marketplace proper: `GET /api/v1/x402/search?tag=&category=&limit=`
  (`x402_directory/api/routes.py:552`), `GET /api/v1/x402/listings?url=`
  (`:592`, one listing + newest probe), free probe reads (`:955-957`).
- What an agent can learn about *a listing* before paying: the listing's
  self-declared `price`, `assets`, `schema`, `reimburses`, `contact`
  (`_listing_json`, `:141-178`), the newest probe (reachable, latency,
  `served_valid_402`, `payto_seen`; `:181-191`), free grade summary, and
  the paid weighted score / measured leaderboard.
- Rate limits on free reads per IP (`search_rate_limited`, `:82`).

**What is broken [fact, from the audit + this read].**

1. The catalog answers "what does PXke sell" and the directory answers
   "what does anyone sell", and nothing tells an agent which document to
   read for which question. `description` (`catalog.py:1293-1298`) says
   "marketplace" but the directory (the actual marketplace) is one product
   among ten in it.
2. There is no *dispatch* entry: an agent with a need ("FX quote", "scan
   this file") has to know to call `/search?category=finance` — the
   category enum is only visible inside one route's discovery extension
   (`routes.py:300-308`) and in the frontend mirror (`x402.ts:150-160`).
3. Our own services are not in the directory, so a `?category=storage`
   search returns nothing even though PXke sells storage.

**Proposed flow [proposal].**

```
1. GET /.well-known/x402   (either host; one document)
   → sections[], products[] with .section/.status/.entry/.auth (audit §3.4)
   → links.directory = "/api/v1/x402/search", links.docs, links.openapi
2. GET /api/v1/x402/search?category=<c>&tag=<t>      (free)
   → items[] each with url, price, assets, category, source (paid|auto_discovered|operator),
     newest probe inline, grade summary inline, boosted flag
3. GET /api/v1/x402/listings?url=<u>                 (free)  → full record, probe history pointer
4. (optional) GET /api/v1/x402/grades/score?url=<u>  (paid)  → weighted opinion
5. Call the listed endpoint directly; it is not ours.
```

Concrete changes, all inside the existing API host and modules:

- Catalog v2 fields exactly as the audit §3.4 specifies; plus a top-level
  `directory` block: `{"search": ".../search", "categories": [...],
  "count": n}` so the dispatch entry is *in* the discovery document.
- `search` items carry `probe` and `grade_summary` inline (both already
  computed for the detail route and the SSR page; `seo/api/routes.py:600-640`
  reads the same services), so an agent ranks in one call.
- Operator-sourced rows for PXke's own services (§1.2), so category search
  is complete.
- `x402.pxke.me/.well-known/x402` mirrors the same document (one more nginx
  `location =`, identical to `algorand-platform.conf:366-372`), because peer
  directories crawl the *site* domain a merchant advertises as `website`
  (`catalog.py:1299` → becomes `https://x402.pxke.me`).

A human web page is secondary for this flow: the Developers page (§4)
shows the same three URLs and nothing else.

### 2.2 Flow B — an agent (or a human with a wallet) listing an endpoint

**Current state, traced end to end [fact].**

Backend — `POST /api/v1/x402/list` (`x402_directory/api/routes.py:209-380`):

1. Circuit breaker check (`:235`).
2. Offer built: price `settings.x402_listing_price` ($0.02 per
   `docs/x402-quickstart.md` table), `resource="x402-directory-list"`,
   description, Bazaar discovery extension with `body_type="json"`
   (`:244-332`). Required body fields are only `url` and `price` (`:324`).
3. `challenge_if_unpaid(request, **offer)` (`:336`; primitive at
   `x402/paid_request.py:121-159`): a request with no `PAYMENT-SIGNATURE`
   header gets the **402 offer before its body is parsed**.
4. With a payment header: body decoded and validated **before** the gate
   (`:340-351`) so a doomed request is never charged.
5. `require_paid_request(request, **offer)` (`:353`; `paid_request.py:161`)
   → `require_payment` (`guard.py:108-221`): the x402 package verifies the
   payload with the GoPlausible facilitator, then **settles** it (`:196`),
   and returns `payer`, `payment_txid`, `asset_id`, `network`, `amount`
   (`:213-221`). Replay protection and the ledger row happen inside
   `require_paid_request`.
6. `run_with_refund(...)` wraps the product write (`:357-369`);
   `listing_service.create(...)` stores the row with
   `payer=result.payer` as the **owner** (`:403-415`; first-claim-wins at
   `listing_service.py:331-341`). Ownership refusal is a `DirectoryError`
   → 403/409, payment kept, never refunded (`:219-233`).
7. `mark_fulfilled` (`:373`) then `200` with `settlement_tx_id` and the
   settlement headers (`:374-380`).

So **registration is already fully x402-native pay-to-list through the
shared gate — the same path every other paid route uses.** It has been
proven live: Phase 0 acceptance on TestNet round-tripped exactly this
route (`docs/x402-facilitator.md:292-303`), and mainnet is live
(`x402_network` mainnet, audit §1.1). Nothing bypasses payment for a
non-technical reason on the backend.

Frontend — `/x402/register` → `X402RegisterForm.svelte` (844 lines):

- A 3-step form that validates, previews the JSON body, offers a free
  "already listed?" check, shows price/`payTo`/assets from the live catalog
  (`:71-75`, `:460-477`), then prints two `curl` commands (`:216-224`,
  `:483-503`) and a **disabled** button "Pay & submit — coming soon"
  (`:505-510`; `en.json:444-445`).
- The bypass is *deliberate and documented* (`:11-23`): the codebase's only
  signing pattern signs a never-broadcast 0-ALGO self-payment for login
  (`lib/auth/arc0025.ts:22-36`, `pera.ts:120-131`), and nobody wanted to
  write a first real ASA-transfer-in-an-atomic-group against a live mainnet
  wallet unsupervised. **That is a safety decision, not a protocol gap** —
  and it is the whole reason the owner's "you can't register with a wallet"
  complaint is true today.

**Is wallet-scan-and-pay achievable with the existing machinery? Yes,
without any new backend payment path.** What the browser has to do is
precisely what every x402 client does — the Python one in this repo
(`x402-client/pxke_x402/`) and the installed `x402-avm` client
(`x402/mechanisms/avm/exact/client.py`, `create_payment_payload`):

1. `POST /api/v1/x402/list` with no payment → 402 with `accepts[]`
   (asset, amount, `payTo`, network, `extra.feePayer` injected from the
   facilitator's `/supported` — `x402/mechanisms/avm/exact/server.py:150-154`;
   the gasless leg is confirmed working, `x402-facilitator.md:300-303`).
2. Build a 2-txn atomic group with `algosdk` (already a dependency,
   `frontend/package.json:31`): txn 0 = fee-payer 0-ALGO self-payment,
   sender `feePayer`, pooled flat fee, **left unsigned**; txn 1 = ASA
   transfer `payer → payTo` of `amount` of `asset`, fee 0, flat. Assign
   group id. This is the same construction `client.py` does in ~40 lines.
3. Ask the wallet to sign **only txn 1**. `@perawallet/connect`'s
   `signTransaction([[{txn: t0, signers: []}, {txn: t1, signers: [addr]}]])`
   is the documented partial-group contract and the same call the login
   flow already makes (`pera.ts:125-127`); Defly mirrors it
   (`defly.ts:62`); Lute's ARC-0001 `signTxns` accepts `signers: []` the
   same way. The Algorand TypeScript x402 skill in this repo documents
   exactly this wallet-backed `ClientAvmSigner` shape
   (`skills/algorand-x402-typescript/references/create-typescript-x402-client.md:52-98`).
4. Base64 the group (`[unsigned t0, signed t1]`, `paymentIndex: 1`), wrap
   it in the x402 v2 `PaymentPayload` (`x402_version`, `accepted` = the
   chosen requirement echoed back, `resource`, extensions — the shape the
   `@x402/core` client emits; `use-typescript-x402-core-avm-reference.md`),
   set it as the `PAYMENT-SIGNATURE` header and **re-POST the same body to
   the same route**. The backend does everything else it already does.

The "scan" the owner describes is the WalletConnect pairing QR that
`peraConnect()` already shows on desktop (`pera.ts:83-101`; bridge and
chain id from `lib/config.ts:27-34`), and the deep link on mobile
(`walletconnect.ts`, `peraWakeTransportBurst` for the return trip). The
human sees: *scan → approve "send 0.02 USDC to <payTo>" in Pera → listed*.
The payer wallet becomes the listing owner automatically (`routes.py:411`),
which is also how Social treats identity ("the payment IS the identity
proof", `x402_social/api/routes.py:538-549`) — no account, no login.

**What "no form" can honestly mean [proposal].** `url` and `price` are the
only required fields (`routes.py:324`). Everything else a good listing needs
is *already published by the endpoint itself in its own 402 offer* —
price, assets, description, `payTo`. The probe fleet already parses those
offers (`served_valid_402`, `payto_seen`, `routes.py:186-189`). So:

```
x402.pxke.me/list
  [ https://api.example.com/v1/quote ]   ← the one field
  → free GET /api/v1/x402/directory/inspect?url=   (new free route, proposal:
     fetch the URL unpaid, parse its 402, return {price, assets, description,
     pay_to, served_valid_402})
  → prefilled card: "quote · $0.01 · USDC · 'Live FX quote…' · payTo …ABCD"
    [category ▾ (default from a tag heuristic, editable)]  [tags, optional]
  → [ Scan with your wallet to list for $0.02 ]  → Pera QR / deep link
  → "Listed. Owner: <payer>. Settlement <txid>. Probed in ≤30 min."
```

An endpoint that serves no valid 402 gets a clear stop ("we could not read
a 402 offer at this URL — fix that first; listing it would fail probes") —
which is also a free pre-check that protects the payer from paying $0.02 to
list something the probes will delist (`x402_listing_term_days`).

Rules for the implementation (so it stays inside existing invariants):

- **Reuse the gate; add nothing to the backend payment path.** The only
  new backend code is the free `inspect` read (rate-limited per IP like
  `search`). The paid call stays `POST /api/v1/x402/list` byte-for-byte.
- **Nothing custodial**: the browser never sees a key; the wallet signs;
  `payTo` is the existing receive-only address. Unchanged.
- **Verify before shipping, against a live wallet, on mainnet, supervised**
  (this is the concern `X402RegisterForm.svelte:11-23` raised and it stands):
  (a) the wallet signs a group with an unsigned fee-payer txn (partial
  signing), (b) the payload shape the backend's `x402-avm==2.0.2` accepts
  from a browser client matches what `@x402/core` emits, (c) the settled
  tx carries the challenge tag (it rides on the asset `extra`,
  `x402-facilitator.md:206-241` — nothing the client does can drop it).
  Use `GET /api/v1/x402/ping` ($0.001) as the first real browser payment,
  exactly what it exists for (`x402-client/README.md`, "Start with ping").
- The payer must hold USDC (or EURQ/USDQ) and be opted in; the flow should
  read the wallet's asset holdings via the algod proxy the frontend already
  has (`config.ts:8-12`, `/api/v1/algod`) and say so *before* showing the QR,
  not after a failed simulation.
- Same `LatestOnly`/AbortController discipline the form already uses
  (`X402RegisterForm.svelte:227-249`; CLAUDE.md §5).
- This flow is one component; it should be the *only* place in the
  frontend that builds an x402 payment, exported as a `payWithWallet(offer,
  body)` helper so board placement and grading can reuse it later — the
  "no new copies of existing logic" rule (CLAUDE.md §3), applied ahead of time.

**Why not a plain payment QR (ARC-26 `algorand://` URI)?** Because a bare
ASA transfer to `payTo` is *not an x402 settlement*: no facilitator
verify/settle, no `PAYMENT-SIGNATURE`, no ledger row, no way to bind the
transfer to a listing body, and it does not count toward the challenge's
USDC volume (`x402-facilitator.md:415-420`: plain transfers almost certainly
do not count). Making it work would mean a backend that watches the chain
for incoming transfers and matches them to pending listings — a second
payment path, which the brief and CLAUDE.md §9 both forbid. The
WalletConnect QR gives the owner the "scan and pay" experience *through*
x402, which is the only version worth building.

### 2.3 Flow C — a human browsing / managing the marketplace

**Current state [fact]** (audit §1.4, §2.3, confirmed by reading):
`/x402` is one 751-line component (`X402.svelte`) stacking a page switcher
(`:182`), hero (`:184-189`), CTA box (`:191-201`), pricing accordion +
developer aside (`:203-234`), tab strip (`:236-243`) and filters
(`:245-272`) above a list of two live listings; `register` is a tab
(`:29`, `:47`); the primary CTA (`:194`) ends on a disabled button. A
second page (`X402Endpoints.svelte`) holds "our" products, split by author
not intent (`X402PageNav.svelte:2-9`). Social's 38 routes and storage's 9
have no human surface beyond an accordion line. Nav entry is one "hub" item
in the newspaper shell (`AppShell.svelte:96-101`, `:123-129`).

**What is broken**: the page answers "what is PXke's x402 catalog" when
the visitor asked "is there anything here, and what do I do". The
"Marketplace vs Our Endpoints" split is the wrong axis (§1.2). And every
row is a dead end (no detail view) even though detail, probe history and
grade summary exist free.

**Proposed flow**: §4 (page structure) — the human page is built around
two verbs, *find* and *list*, with proof (live settlements, measured
uptime) visible on the first screen, and everything for agents moved to
one Developers page.

---

## 3. Subdomain rollout plan — `x402.pxke.me`

**Confirmed host setup [fact].** One box, `5.135.131.229`
(`deploy/deploy.conf:1`, `docs/ops-hosts.md:9-11`). One nginx instance,
shared with unrelated vhosts (`ops-hosts.md:15-18`). One site file rendered
by `deploy.sh` from `deploy/nginx/algorand-platform.conf` with two
placeholders `@SITE_DOMAIN@` / `@API_DOMAIN@` (`deploy.sh:98-102`,
`install_nginx_site` `:108-141` with `nginx -t` rollback). Three server
blocks: 80→443 redirect for both names (`conf:93-96`), the site
(`:113-121`, static `root …/frontend_web` `:171`, SSR paths proxied,
`/x402` proxied for SSR `:557-562`, `/api/` proxied same-origin `:403`,
`.well-known/x402` and `/sdk/` mirrored `:366-386`), and the API host
(`:576+`, `/api/` → upstream, `/` = static merchant landing `:688-692`,
everything else 404 `:693-695`). One Let's Encrypt cert named after
`SITE_DOMAIN` covering both names (`deploy.sh:186-195`; API block reuses
it, `conf` API-block `ssl_certificate`). One backend process; CORS is an
exact-match origin list (`core/cors.py:35-38`) set from
`CORS_ALLOWED_ORIGINS=https://@SITE_DOMAIN@` (`deploy/env/backend.env.example:29`),
and an unknown `Origin` is a hard 403 (`falcon_main.py:53-56`).

**The assumption in the brief is correct**: the subdomain is a DNS record
plus a nginx server block on the *same* host pointing at the *same*
upstream, plus a frontend build target. No new host, no new API
deployment, no new backend process.

### 3.1 What changes, in order

| # | Layer | Change | Notes |
|---|---|---|---|
| 1 | DNS | `x402.pxke.me` → `5.135.131.229` | Same record type as `algorand.pxke.me` (README: "DNS must point at `TARGET_HOST`", `deploy/README.md:112`). I could not see the DNS provider from the repo — A vs CNAME is unverified. |
| 2 | TLS | Expand the existing cert: `certbot certonly --nginx --cert-name algorand.pxke.me -d algorand.pxke.me -d algorand-api.pxke.me -d x402.pxke.me --expand` | `cmd_provision` skips certbot when `/etc/letsencrypt/live/$SITE_DOMAIN` exists (`deploy.sh:189`), so this is a one-off root step *or* a small script change adding a third `-d` and dropping the skip when the SAN list grew. Do it before the nginx block ships, or `nginx -t` fails on the cert reference and the rollback in `install_nginx_site` restores the old file (`:131-141`). |
| 3 | deploy config | `X402_DOMAIN=x402.pxke.me` in `deploy/deploy.conf`; `@X402_DOMAIN@` placeholder in `render()` (`deploy.sh:98-102`); required-var check next to `:83-84` | One owner for the name, same as the other two. |
| 4 | nginx | (a) add `@X402_DOMAIN@` to the port-80 redirect `server_name` (`conf:95`); (b) new `server` block: `server_name @X402_DOMAIN@`, same cert, `root …/releases/current/frontend_web_x402` (see 6), `location ^~ /api/ { proxy_pass http://algorand_api; }` (same-origin API like the site host, `conf:403`), `location = /.well-known/x402` and `location ^~ /sdk/` mirrored verbatim from `:366-386`, SSR locations for the marketplace document routes (see 7), `try_files $uri =404` + `error_page 404 /index.html` fallback like `:571-575`; (c) on the *site* host replace the `location ^~ /x402` SSR block (`:557-562`) with `return 301 https://@X402_DOMAIN@$x402_path` where the map strips the `/x402` prefix (`/x402` → `/`, `/x402/board` → `/board`, `/x402/register` → `/list`, `/x402/endpoints` → `/directory`) | Same upstream `algorand_api` (`conf:6`). The security headers and cache zone are already namespaced for this shared nginx (`conf:20-35`) — reuse, don't redeclare. |
| 5 | backend settings | New `x402_public_site_url: str = "https://x402.pxke.me"` in `backend/app/core/config.py` next to `x402_public_api_base` (`:193`); `PUBLIC_X402_SITE_URL` in `deploy/env/backend.env.example` | Read by: `catalog.py:1299` (`website`), `x402_uptime/services/checker.py:46` (UA), marketplace SSR canonicals (7), `shared/merchant-landing/index.html:22-26` (redirect target; static file, hand-edit). `x402_public_api_base` **does not change** — it is what the 402 offers advertise as `resource.url` (`guard.py:95-105`) and what the Bazaar has catalogued; touching it would orphan every existing Bazaar entry. |
| 6 | CORS | `CORS_ALLOWED_ORIGINS=https://algorand.pxke.me,https://x402.pxke.me` | Exact-match list (`cors.py:35-38`); without it, every cross-origin fetch from the new domain to `algorand-api.pxke.me` is a 403. The same-origin `/api/` proxy in (4b) means the marketplace SPA can also just call `/api/…` relatively — recommend the SPA build for this host sets `VITE_API_BASE_URL` empty so browser fetches stay same-origin (`config.ts:23`, `write_vite_env.sh`), while the *displayed* agent URLs keep the API host (`x402.ts:139-145`). Add the origin anyway for wallets/extensions that fetch cross-origin. |
| 7 | frontend build | One codebase, **two build outputs** from one `frontend/` tree: `VITE_PRODUCT=news` (today's) and `VITE_PRODUCT=marketplace` → `frontend_web_x402/`. The router picks its route table by product (`App.svelte:118-129` today hardcodes `/x402…`); the marketplace root is `/`. `deploy/package.sh` + `detect_changes.sh` build/ship both dirs; `write_vite_env.sh` gets `VITE_PRODUCT`, `VITE_AUTH_DOMAIN`, `VITE_API_BASE_URL` per build. | Why two builds rather than runtime host sniffing: the PWA manifest/`start_url`/`<title>`/precache list (`vite.config.ts:9-60`) and the `<meta description>` in `index.html:8-12` are per-product, and nginx serves a different `root` per host anyway. Components, tokens, i18n and `lib/` are shared — that is the "one design system" constraint made structural. Fallback if the double build is too heavy for `deploy.sh`: single build, `window.location.host` switch in `App.svelte`; loses per-product PWA identity. |
| 8 | backend SSR | Keep the existing `/x402`, `/x402/:tab`, `/x402/endpoints` Falcon handlers (`seo/api/routes.py:648-668`) as the *internal* paths; the new vhost proxies `x402.pxke.me/<page>` to `http://algorand_api/x402/<page>` (nginx `proxy_pass` with a path prefix) and sends `X-Forwarded-Host`. `render.render_x402` builds canonical/OG from `x402_public_site_url` for these routes instead of `site_url()` (`render.py:43-45` reads `public_site_url` = the newspaper). The SPA shell loader (`seo/shell.py:60-90`) must resolve `frontend_web_x402/index.html` for marketplace routes — today it looks for `frontend_web` only (`:66-73`). | Falcon has no host routing, and CLAUDE.md wants one backend — the prefix mapping keeps the backend host-agnostic. The analytics path predicate (`seo/api/routes.py:195-204`) keeps matching `/x402/…` unchanged. |
| 9 | discovery pointers | `catalog.py:1299` `website` → new site URL; `x402.ts:139-145` display constants unchanged (API host); `shared/merchant-landing/index.html` refresh/canonical → `https://x402.pxke.me`; `docs/x402-quickstart.md:22-23` "human-readable mirror" line | The facilitator's OG enrichment reads the **API host root** (`x402-facilitator.md:153-160`) — unaffected. |
| 10 | SEO plumbing | New host needs its own `robots.txt` and a tiny sitemap (the newspaper's `sitemap.py` has no x402 entries to remove — verified by grep). Newspaper `sitemap-pages.xml` should drop any `/x402` entries if `_x402_document`'s tracked paths feed it (they feed analytics, not the sitemap — no change found needed). | Small; can be a static file under the new `root` in v1. |
| 11 | shell nav | `AppShell.svelte:96-101` (nav) and `:123-129` (product switcher): the x402 entry becomes an **external** link to `https://x402.pxke.me`; the marketplace build's shell has the mirror link back to News. | This is the "one company, several products" switch — same component, different targets. |

**Not touched**: `algorand-api.pxke.me` paths, `x402_public_api_base`,
ledger `resource` ids, Bazaar entries, `payTo`, the Celery probe
(`workers/app/modules/x402_probe`), Cassandra schema. The API block's
`location = /` merchant landing stays.

### 3.2 Wallet sign-in on the new domain — a trap to name now

`AUTH_DOMAIN=@SITE_DOMAIN@` is one value (`backend.env.example:27`,
`settings.auth_domain` → the CAIP-122 `domain` in every sign-in message,
`auth/utils/signing_message.py:33`; ARC-60 verification compares it,
`auth/utils/arc0060_verify.py:53`). A wallet *login* from `x402.pxke.me`
would carry the wrong domain. **Proposal: the marketplace has no wallet
login in v1.** It does not need one — payment is identity for every write
(directory `routes.py:411`, social `:538-549`), and reads are free. Admin
stays at `algorand.pxke.me/admin`. If a "my listings" dashboard is ever
wanted, `auth_domain` must become per-host first; flag, don't improvise.

### 3.3 Deploy sequencing (each step alone, each reversible)

1. Backend: `x402_public_site_url` setting + marketplace SSR canonical +
   shell resolver (no behaviour change until the env var is set). Tests.
2. Frontend: `VITE_PRODUCT` route tables, marketplace pages (§4), shared
   shell with cross-domain switcher. `npm run check`/`test`/`build` for
   both products.
3. Deploy scripts: second build dir, `X402_DOMAIN`, nginx render, CORS env.
4. DNS + cert expand (root, one-off).
5. Ship nginx block with the site-host `/x402` → 301 redirect *last*, after
   the new host answers (a redirect to a host that 404s is the worst
   outcome; the rollback in `install_nginx_site` covers a broken render but
   not a bad target).
6. Flip `website` in the catalog and the merchant landing redirect.

---

## 4. `x402.pxke.me` — page and nav structure (human-browse use case)

Design goal: a stranger understands in ten seconds *what this is*, *whether
anything is here*, and *the one thing they can do*. The shared design
system (Archivo headlines, Plex Mono for machine-stamped values, Source
Serif for prose — `frontend/index.html:24-35`; tokens like `--font-mono`,
`--border`, `--on-surface` used in `X402RegisterForm.svelte:515-560`) stays;
only the masthead word changes ("PXke x402"), and the shell's product
switcher points across domains.

### 4.1 Nav

`Directory · List an endpoint · Board · Requests · Trust · Developers`
— one strip, no second-level page switcher, no tabs-as-forms. Social is
*not* in the nav (agent-only); it appears only as one line on Developers.

### 4.2 Pages

| Path | Page | First screen | Below the fold |
|---|---|---|---|
| `/` | **Home** | One sentence: "x402 endpoints on Algorand: list yours, find others, pay per call in USDC." Three live numbers from free reads: *listings* (`/search`), *real settlements this week* (`/settlements/recent`, probe-excluded), *endpoints probed healthy in the last hour*. A search box (category chips + free text → `/directory?…`). One primary button **List an endpoint** → `/list`. | Newest 5 listings (rows link to detail), latest 5 settlements (proof it is used), "For agents: `GET https://algorand-api.pxke.me/.well-known/x402`" in one mono line. Nothing else. |
| `/directory` | **Directory** | Category chips (the closed enum, `x402.ts:150-160`), tag filter, sort (newest / boosted / measured uptime). Each row: name/host, price, assets, category, operator badge (PXke) or verified badge, newest probe (✓ 210 ms, 30 min ago), grade count. Rows open detail. | Empty-category state that says so and links to `/requests` ("ask for one"). |
| `/directory/:id` (or `?url=` until listings carry an id — audit §3.3) | **Listing** | Full listing + `payTo`, probe history sparkline (free `probe/history`), grade summary (free), "unlock weighted score" (paid, with `?preview=true` result shown), boost status, owner (short payer), settlement tx link (explorer). | "Try it": the endpoint's own URL and a copyable `curl` of the 402 challenge. If it is a PXke service: `input_example` and a **Preview** button where `supports_preview`. |
| `/list` | **List an endpoint** | The §2.2 flow: one URL field → inspect → prefilled card → wallet QR → done. No steps, no curl (curl moves to Developers). | "Already listed?" resolves inline from the inspect call. Boost offer appears *after* success, not before. |
| `/board` | **Board** | The placements grid (free feed) + "Place a tile" (same `payWithWallet` helper once §2.2 ships; until then the curl block). | — |
| `/requests` | **Requests** | The request list (free) + inline "File a request" (free, works today — make it the visible success path, audit §3.6). | Paid demand ranking as a preview. |
| `/trust` | **Trust** | "Check an endpoint" URL box → free probe latest + grade summary in one card; the measured reliability leaderboard (paid; show preview). | One paragraph on what is measured vs paid opinion (`_PROBE_LEADERBOARD_NOTE`, `routes.py:101-107`, verbatim). |
| `/developers` | **Developers** | The three discovery URLs; the catalog accordion grouped by `sections` (audit §3.4) — this is where today's "What it costs" + "For agents" aside and `X402ProductCatalog` go; quickstart rendered from `docs/x402-quickstart.md`; SDK download (`/sdk/`, mirrored). | "How payment works" (from `docs/x402-marketplace-api.md:43`), "Agent network: `/api/v1/x402/social` — API only" one line, rate limits. |

Deleted: `X402PageNav`, the `register` tab, `X402Endpoints.svelte` (its
news list becomes the News Engine's directory row + detail page), the
`MARKETPLACE_MECHANIC_PRODUCT_KEYS` split (`catalog.ts:22-28`), the
hand-maintained `X402_PATHS` (`x402.ts:119-133`) once catalog v2 carries
`products[].entry`.

### 4.3 The ten-second test, written out

A visitor lands on `/`: masthead "PXke x402", the sentence, three numbers
that are obviously live (mono, stamped "as of 14:02 UTC"), a search box,
one button. They know it is a marketplace of paid API endpoints on
Algorand, that N things are listed and M people paid this week, and that
they can either search or list. If they are an agent operator with a
wallet, `/list` is one field and one scan. If they are an agent, the mono
line at the bottom is the only URL they need.

---

## 5. Sequencing across §2-4

1. Catalog v2 + operator-sourced directory rows (§2.1) — backend only,
   unblocks every page below.
2. Subdomain plumbing §3.1 steps 1-3, 5-8 (no user-visible change yet).
3. Marketplace build: Home, Directory, Listing, Developers (§4) — all
   free reads that exist today.
4. `/list` wallet flow (§2.2), proven first on `ping` with a real wallet,
   supervised.
5. Cut over: nginx block + 301s, catalog `website`, merchant landing.
6. Board/Requests/Trust pages; `payWithWallet` reused for board and grades.

---

## 6. Assumptions and open points

- **DNS record type / provider** not visible in the repo; assumed an A
  record like the existing names.
- **Partial-group signing by Pera/Defly/Lute** is the packages' documented
  contract, not something this repo has exercised; it is the first thing
  to verify with a real wallet (§2.2).
- **`@x402/core` browser client vs hand-built payload**: the TS skill's
  `wrapFetchWithPayment` + wallet `ClientAvmSigner` would remove the
  hand-built group entirely; whether `@x402/core`/`@x402/avm` are
  installable and small enough for the SPA bundle was not checked
  (`frontend/package.json` has neither). Either route ends in the same
  `PAYMENT-SIGNATURE` header.
- **Two builds vs one**: recommended two (§3.1 #7); the runtime host
  switch is the cheaper fallback.
- **Operator directory rows** need a `source="operator"` value and a seed
  path; whether the probe fleet should probe our own routes (it does not
  pay them — a bare 402 read is not a settlement) is a small owner call.
- The catalog's `products[]` and the directory's listings remain two
  stores by design (merchant manifest vs marketplace); this document does
  not propose merging them, only deriving operator rows from the roster.
- Nothing here changes prices, `payTo`, the network, or any paid route's
  behaviour; nothing is custodial; no wash volume is introduced (operator
  rows are free and labelled).
