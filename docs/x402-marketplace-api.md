# PXke x402 marketplace — API reference for agents

Base URL: `https://algorand-api.pxke.me`. Every route below is under `/api/v1/`,
plus two top-level manifests: `GET /.well-known/x402` (re-serves the catalog
verbatim — no ratified well-known spec exists to conform to instead, see
`backend/app/modules/x402_wellknown/`) and `GET /openapi.json` (a real OpenAPI
3.1 document generated from the same route roster). Nothing else on that host
is proxied to the API. All bodies and responses are JSON. Written from the
route code in `backend/app/modules/x402_catalog/`, `x402_news/`, `x402_scan/`,
`x402_storage/` and `x402_wellknown/` (`api/routes.py` in each) on
2026-09-10, after the consolidation recorded in
[`adr/ADR-0006-x402-consolidation.md`](adr/ADR-0006-x402-consolidation.md);
the live catalog (`GET /api/v1/x402`) is authoritative for prices and for
which routes are registered right now.

Three products are sold here, all our own: the **News Engine** (the PXke
Algorand newspaper, per call), the **sandboxed file/tarball scan**, and
**agent backup storage**. Every earlier marketplace-mechanics product
(endpoint directory, visibility board, feature-request board, grading,
probe history, uptime check, agent social network, KYA identity, fulfillment
receipts, the `ping` route) was removed on 2026-09-10 and those paths now
`404`. Human-readable storefront: `https://x402.pxke.me` (`/`, `/scan`,
`/storage`, `/news`, `/developers`).

## Start here: the catalog

`GET /api/v1/x402` (free, rate-limited) returns one document with everything
an agent needs before paying:

```json
{
  "name": "PXke x402 marketplace",
  "description": "...",
  "website": "https://x402.pxke.me",
  "url": "https://algorand-api.pxke.me/",
  "merchant": {"id": "<facilitator merchant id>", "name": "PXke x402 marketplace", "payTo": "<receive-only Algorand address>"},
  "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
  "network_name": "mainnet",
  "pay_to": "<receive-only Algorand address>",
  "facilitator_url": "https://facilitator.goplausible.xyz/",
  "challenge_tag": "x402-global-challenge",
  "payment_scheme": "exact",
  "assets": [{"symbol": "USDC", "asa_id": 31566704, "decimals": 6}, ...],
  "categories": ["meta", "services"],
  "sections": [
    {"key": "meta", "title": "Start here", "summary": "..."},
    {"key": "services", "title": "Services", "summary": "..."}
  ],
  "products": [
    {"key": "catalog", "title": "Catalog", "section": "meta", "summary": "...",
     "status": "live", "entry": "GET /api/v1/x402", "auth": "..."},
    {"key": "news", ...}, {"key": "scan", ...}, {"key": "storage", ...}
  ],
  "routes": [
    {"product": "news", "method": "GET", "path": "/api/v1/x402/news/search",
     "paid": true, "price_usd": "$0.001", "price_unit": null,
     "resource": "x402-news-search", "description": "...",
     "input_example": {"q": "tinyman volume", "limit": 10},
     "supports_preview": true, "supports_promo": true},
    ...
  ]
}
```

`products[]` lists the full roster, live or gated, each with a computed
`status`; `routes[]` lists only what the running process actually
registered, so a path absent from `routes` will `404`. Prices are the live
settings values. `price_unit` is `null` for a flat price and `"KB"` for the
storage routes, whose `price_usd` is a per-KB rate rather than the amount
charged (see the storage section).

## How payment works here

The marketplace speaks x402 v2 with the `exact` AVM scheme, settled by the
GoPlausible facilitator on **Algorand mainnet**. There is no API key and no
account: the payer is the wallet that signs the payment.

1. Call a paid route with no payment. You get **`402 Payment Required`**. The
   offer is in the **`PAYMENT-REQUIRED` response header**, base64-encoded JSON
   (`x402Version`, `accepts[]`, `resource`). The JSON body is empty (`{}`).
   `resource.url` is the route's absolute public URL (path-parameter routes
   advertise their template, e.g.
   `/api/v1/x402/storage/backups/{backup_id}/renew`); the catalog's
   `resource` field is the short ledger id, not that URL.
   Each `accepts[]` entry is one asset you may pay in: `scheme: "exact"`,
   `network` (CAIP-2), `asset` (ASA id as a string), `amount` (atomic units,
   6 decimals for every asset offered), `payTo`, and
   `extra: {"decimals": 6, "tag": "x402-global-challenge"}`. USDC is listed
   first and is preferred; EURQ and USDQ (Quantoz) are also offered on mainnet
   at the oracle-converted equivalent of the USD price. An asset whose USD
   rate is momentarily unknown is simply omitted from that response.
2. Build and sign the asset-transfer payment for one of the `accepts` entries
   (the `x402-avm` client does this; see `docs/x402-facilitator.md` for a
   signer implementation that actually works against `x402-avm==2.0.2`). Your
   wallet must be **opted into the ASA** you pay with.
3. Retry the same request with the payment in the **`PAYMENT-SIGNATURE`
   request header**. The server verifies **and settles** through the
   facilitator before running the route; on success the response carries the
   settlement receipt in the **`PAYMENT-RESPONSE` header** and every paid
   response body includes `settlement_tx_id` (the on-chain transaction id).
   The facilitator covers the Algorand fee; your transaction pays 0 fee.

Rules every paid route follows:

- **An unpaid request sees the offer before its body is parsed.** A call
  with no `PAYMENT-SIGNATURE` (and no `?preview=`/`?promo=`) gets the `402`
  challenge first, so a client (or the facilitator's own `verify`) can learn
  the price without composing a valid body. With a payment attached,
  **validation still happens before the gate**: a malformed body, bad URL,
  unknown id, out-of-range size, etc. is a `400`/`404`/`413` with nothing
  charged.
- **A payment header is single-use.** Re-presenting one is `409
  payment_replayed`. If verification or settlement fails you get `402` again
  (`settlement_failed` names the facilitator's reason) and the header is
  released for a retry.
- **Settled-then-refused, payment kept, nothing refunded**: checks that
  cannot run until the payer is known (renewing or adding a version to a
  backup a different wallet created — `403 backup_owned_by_another_payer`)
  and any other business rejection that is the caller's fault, not ours
  (a scan whose caller-supplied URL could not be fetched — `422
  fetch_failed`). The payment is taken, nothing changes, and the response
  still carries the `PAYMENT-RESPONSE` receipt header. Each such route's
  description in the 402 offer says so.
- **Settled-then-our-side-failure, auto-refunded**: if the product write
  itself fails after your payment settled (a bug or outage on our end — the
  search engine down, the scan sandbox failing, the storage connector
  failing), the full settled amount is automatically refunded from a
  dedicated refund wallet — no dispute process, nothing you need to do. You
  get a `503` with one of: `product_failed_refunded` (refund sent — the
  refund transaction id is in the message; treat it as `sent_unconfirmed` if
  you can't find it on-chain yet, in which case it's still your
  reconciliation reference) or, rarely, `product_failed_refund_pending` (the
  refund itself did not go through immediately — the message carries your
  original payment's `tx_id` as the reference to reconcile by hand). A
  resource with repeated refund failures trips a circuit breaker and starts
  returning `503 temporarily_disabled` *before* the payment gate — no charge
  — until an operator resets it. There is no escrow: payment always settles
  first, on-chain, before any product work runs; the refund is a
  same-marketplace remediation step afterward, not a smart-contract
  guarantee.
- Error bodies are always `{"error": {"code": "...", "message": "..."}}`.

Discovery: every paid route declares a Bazaar discovery extension (input
example, JSON Schema, output example) in its 402 offer, which is what the
catalog's `input_example` mirrors.

## Preview and promo codes

Two general bypass mechanisms exist on the shared payment gate. Both are
**opt-in per route**: a route only honors them if the live catalog says so.
Check `GET /api/v1/x402` before assuming either works on a given route —
each entry in `routes[]` carries `supports_preview` and `supports_promo`
booleans. As of this writing both are set on the two paid **reads**
(`GET /news/search`, `POST /scan/url`) and neither on the storage
**writes** (`POST /storage/backups`, `.../renew`, `.../versions`): there is
nothing to preview on a route whose whole point is performing the paid
action, only on a route that sells a result back.

**`?preview=true`** (also `1`/`yes`, case-insensitive) bypasses payment
entirely and returns a **redacted** version of the same response shape, with
no facilitator call and nothing settled — this is still a real request the
server serves for free, so it is rate-limited per IP (60/h default, fail-open
on a Redis blip) independently of the catalog's other free-route budgets.
Preview never computes the real result at all (not even to redact it after
the fact) — it returns a fixed exemplar with the same keys the real response
would carry, using an impossible sentinel value (`score: -1.0`, `risk.score:
-1.0`, `malicious: null`, or the literal string `"<preview>"`) precisely
because a real response can never legitimately carry that value. So no hit
count, no verdict, no fetched byte ever leaks for free.

**`?promo=CODE&promo_wallet=ADDRESS`** attempts to redeem an admin-issued
promo code for a bounded number of uses, scoped to one route. **There is no
public way to create a promo code** — codes are issued only through the
admin UI, never self-service. On success the response is the **real,
non-redacted** response (a promo bypasses payment, not product quality) with
an added `via: "promo"` field and an empty `settlement_tx_id` (nothing
settled). Any redemption failure — unknown code, wrong resource, expired,
exhausted, already redeemed by that wallet, a malformed wallet, a
rate-limited IP (30 attempts/h), or a storage blip — is silent: the request
just falls through to the normal payment gate. A promo attempt is never
itself a 402/error the caller can't route around; if you see a `402`, either
no promo was attempted, or it failed and payment is still required as
normal. Because a promo redemption settles nothing, there is nothing to
refund on it: a product failure on a promo call is a plain `503`
(`search_unavailable` / `scan_unavailable`), not `product_failed_refunded`.

Preview is checked before promo, so a `?preview=true` call never spends a
promo redemption even if both query params are present.

**Neither preview nor a promo redemption is ever a settlement.** Redeeming
one does not write to the settlement ledger and never appears in
`GET /api/v1/x402/settlements` — there is simply no settlement row for
either to be counted from.

## Rate limits (free routes)

Counted per client IP, per rolling hour, on a fail-open Redis counter; over
budget is `429 rate_limited`. Paid routes are gated by payment instead and
are not counted (the scan route is the one exception — see its section).
Defaults:

| Budget | Routes |
|---|---|
| 120/h | catalog, `/.well-known/x402` and `/openapi.json` (one shared counter); settlements feed (own counter); news headlines, news tags, news article read (three separate counters); every free storage route (per IP before signature verification, then per wallet after it) |
| 60/h | `?preview=true` on any route that supports it (own counter) |
| 30/h | `POST /scan/url` per IP, paid or not; promo redemption attempts (own counter) |

## Products and routes

Prices below are the defaults in `backend/app/core/config.py`; trust the
catalog. `:param` segments are path parameters. Every list route accepts
`?limit=` (integer, clamped server-side: 50 for news headlines and search,
200 for the news tag list, 100 for storage lists).

### Catalog and settlements (`catalog`, section `meta`)

| | Route | Price |
|---|---|---|
| free | `GET /api/v1/x402` | |
| free | `GET /api/v1/x402/settlements` (alias `GET /api/v1/x402/settlements/recent`) | |
| free | `GET /.well-known/x402` | |
| free | `GET /openapi.json` | |

**`GET /settlements`** — the proof-of-volume feed: the 25 most recent
**real** settlements across every product, newest first, `{"items":
[{tx_id, asset, amount, payer, resource, settled_at_epoch, eur_value,
fulfilled}]}`. `amount` is in human units for the settled asset (`asset`
is the symbol), `eur_value` is the EUR value recorded at settlement time
(`null` if the rate was unavailable), `fulfilled` says whether the product
write completed. Payments from operator-declared wallets
(`X402_PROBE_PAYERS`) are excluded in code, never by convention. When the
window is empty the response adds an honest `note` and a `verify_at` link
to our facilitator merchant page rather than a bare `[]` — an empty feed is
a real, expected state, not an error, and nothing is ever fabricated to
fill it.

### News Engine (`news`, the PXke Algorand newspaper, per call)

| | Route | Price |
|---|---|---|
| free | `GET /api/v1/x402/news` | |
| free | `GET /api/v1/x402/news/tags` | |
| free | `GET /api/v1/x402/news/articles/:article_id` | |
| paid | `GET /api/v1/x402/news/search` | $0.001 |

The article content is already free on the public website, so the article
route carries no payment gate — only the paid search over the archive does.
The three free reads are counted on three separate per-IP counters (same
120/h budget each), so exhausting one never locks you out of the others.

**`lang`** (optional, on every free read here) — a lowercase two-letter
language code, optionally with a region suffix (`fr`, `es`, `fr-ca`);
anything else is `400 invalid_request`. The newspaper's translation pipeline
stores full per-language translations of each article, and this serves them
without scraping the human site. Only the *shape* is validated: a well-formed
code with no stored translation is not an error — that item comes back in
English. Languages stored today: `ar`, `es`, `fa`, `fr`, `hi`, `ps`, `ru`,
`zh`; an article's own `translations_available` is the authoritative list.

**`GET /news?tag=&limit=&lang=`** — `{"items": [{article_id, slug, title,
summary, tags, published_at_epoch, url}]}`, newest first (max 50). `tag` is
one of the tags `GET /news/tags` lists. With `lang`, `title`/`summary` are
overlaid per item where that translation exists, English otherwise; `slug`,
`url` and the ids are language-independent.

**`GET /news/tags?limit=`** — the newspaper's live tag taxonomy — the
writers' own labels, so richer than the fixed sections. `{article_count,
tags: [{tag, count, views, last_epoch}], tag_count_total}`, sorted by
coverage (article count, then readership), `limit` max 200 (default: the
maximum). `article_count` is the size of the published feed;
`tag_count_total` is how many tags exist in total, so you can tell when
`limit` cut the tail off. The aggregate is cached for 30 minutes server-side
(shared with the public site's own tags endpoint), so `count`/`views` can
lag a fresh publish by up to that long. This is the discovery surface for
the headline list's `?tag=` filter.

**`GET /news/articles/:article_id?lang=`** — `article_id` is the uuid or
the slug. `{lang, article_id, slug, title, summary, body_markdown, tags,
service_id, trigger_kind, sources, image_url, published_at_epoch,
updated_at_epoch, url, translations_available}`. `lang` in the payload is
the language actually **served**, not the one requested: `"fr"` when the
French translation of title/summary/body was served, `"en"` otherwise
(including when `lang` was omitted, when no such translation is stored, and
when the translations lookup itself failed — in which case
`translations_available` is also `[]` and the body is the English one). 404
for anything not published.

**`GET /news/search?q=&limit=`** — paid; `q` is 1-200 characters, `limit`
clamped to 50. `{query, engine, items: [{article_id, slug, title, summary,
snippet, score, published_at_epoch, url}], settlement_tx_id}` — Typesense-
ranked, typo-tolerant, synonym-aware, `snippet` carries `<mark>` highlights.
Supports `?preview=true` (one exemplar row with every string `"<preview>"`,
`score` `-1.0`, `published_at_epoch` `0` — the query is never run) and
`?promo=`. If the search engine fails after a real payment settled, the
payment is auto-refunded (see the refund rule above); a `?promo=` call gets
a plain `503 search_unavailable`. Search results are English-only (`lang` is
not accepted here); follow the returned `article_id`/`slug` into the free
article route with `?lang=` for a translation.

### Sandboxed file/tarball scan (`scan`)

| | Route | Price |
|---|---|---|
| paid | `POST /api/v1/x402/scan/url` | $0.01 |

**`POST /scan/url`** — body `{"url": "https://..."}` (http or https only;
anything else is `400 invalid_request`). We fetch the URL server-side
(streamed to disk, SSRF-pinned — private/loopback/link-local targets are
refused, max 1 GiB, 60 s download timeout) and run it through a
network-isolated container that **never executes the input**: ClamAV
signature match, file-type verification, Shannon entropy, embedded URL/IPv4
extraction, YARA rule count, an ssdeep fuzzy hash (informational only — no
known-bad corpus is bundled), and for zip/tar archives a zip/tar-bomb-safe
member listing with the same checks per member (extraction is skipped, with
the reason reported, when declared sizes exceed the guard). 90 s sandbox
timeout. Response:

```json
{
  "source_url": "...", "download_bytes": 18422, "schema_version": 1, "status": "ok",
  "one_line_summary": "No concerns found -- no indicators -- file type: Zip archive data",
  "target": {"label": "input", "size_bytes": 18422, "type": "Zip archive data",
             "entropy_bits_per_byte": 7.91, "indicators": {"urls": [], "ipv4_addresses": []}},
  "clamav": {"engine": "clamdscan", "exit_code": 0, "infected_files": [], "clean": true},
  "archive": {"archive_kind": "zip", "member_count": 3, "declared_uncompressed_bytes": 40200,
              "extraction_skipped_reason": null, "clamav": {...}, "members": [...]},
  "yara": {"matched_rules": 0},
  "fuzzy_hash": {"ssdeep_hash": "...", "comparison_corpus": null, "note": "..."},
  "risk": {"score": 0.0, "verdict": "no concerns found", "malicious": false, "caution_notes": []},
  "settlement_tx_id": "..."
}
```

`archive` is `null` for a non-archive. `risk.malicious` is the boolean to
act on; `risk.score` (0-1) and `caution_notes` carry the reasoning. Static
analysis only: a clean verdict means no *known* indicator was found, not
that the file is safe to run.

Order of checks, and what each costs you: the circuit breaker (`503
temporarily_disabled`, free) → the per-IP rate limit (30/h, `429`, free,
counted whether or not you pay) → the 402 challenge if unpaid → body/URL
validation (`400`, free) → a concurrency slot (`503 scan_unavailable`, free
— capacity is checked *before* the gate so an over-capacity caller is never
charged) → payment → fetch → sandbox. A fetch failure (connection refused,
timeout, non-200, over the byte cap) is **`422 fetch_failed` with the
payment kept** — the URL is yours, so a fetch failure is caller-controlled
and refunding it would make refunds free to trigger on demand. A sandbox
failure is ours and is auto-refunded (`503 product_failed_refunded`).
Supports `?preview=true` (a fixed, clearly-fake report: every string
`"<preview>"`, `clean`/`malicious` `null`, `risk.score` `-1.0`; no target is
ever fetched or scanned for a preview) and `?promo=` (a real scan; on
failure `422 fetch_failed` / `503 scan_unavailable`, nothing to refund).

### Agent backup storage (`storage`)

| | Route | Price |
|---|---|---|
| free | `POST /api/v1/x402/storage/auth/challenge` (alias `POST .../auth/nonce`) | |
| paid | `POST /api/v1/x402/storage/backups` | $0.002/MB per 90 days, KB-billed, $0.001 floor |
| free | `GET /api/v1/x402/storage/backups` | |
| free | `GET /api/v1/x402/storage/backups/:backup_id` | |
| paid | `POST /api/v1/x402/storage/backups/:backup_id/renew` | same rate, from stored size |
| paid | `POST /api/v1/x402/storage/backups/:backup_id/versions` | same rate, from the new upload |
| free | `GET /api/v1/x402/storage/backups/:backup_id/versions` | |
| free | `GET /api/v1/x402/storage/backups/:backup_id/versions/:version` | |
| free | `DELETE /api/v1/x402/storage/backups/:backup_id` | |

The catalog lists the paid routes with `price_usd: "$0.000001953125"` and
`price_unit: "KB"` — that is the per-KB rate for a full 90-day term, not
the amount charged. The live 402 offer carries the actual amount for your
`declared_size_bytes` and `retention_days`.

No sessions anywhere in this product: every free route re-proves control of
a wallet, fresh, on every call. `POST /storage/auth/challenge` (body:
`{"wallet"}`, rate-limited per IP only since the wallet is an unproven
claim at that point) returns a single-use `{nonce, signing_message,
expires_at_epoch, proof_methods}` valid for 5 minutes; sign
`signing_message` with one of the `proof_methods` (`legacy_message` or
`signed_bytes`) and present `?wallet=&nonce=&proof_method=&signature_b64=`
on the very next call to any free route below — the signature is consumed
on first use (`401 unauthorized` on reuse or expiry), so mint a fresh
challenge per call. `503 auth_store_unavailable` means the nonce store
could not be reached; retry.

**`POST /storage/backups`** — query params `declared_size_bytes=<N>` (sets
the size the price is computed from before the body is even read; over
10 MB is `413`) and optional `retention_days=<1-90>` (default 90 — how long
THIS term lasts, validated and rejected with a `400` before any payment if
out of range); body: `{"data": "<base64>", "label": "<=256 chars,
optional"}` (`data` must decode to at most `declared_size_bytes`). Price is
`max($0.001, ceil(N/1KB) * $0.000001953125 * retention_days/90)` — billed by
the KB actually used (never rounded up to a whole MB), scaled linearly by
the chosen retention, and floored so a tiny short-retention request never
prices out to an unsettleable fraction of a cent. Concretely: 10 MB for the
full 90-day term is $0.02. Stored content is **opaque** — never scanned,
indexed, or acted on by us — but not confidential from us: an operator can
inspect or remove a specific backup for abuse/legal response. Encrypt
client-side first if that matters to you — see
[`x402-storage-encryption-guide.md`](x402-storage-encryption-guide.md) for a
real, copy-paste `age`/`openssl` recipe. Response is the backup's metadata
(`backup_id, size_bytes, content_hash, label, created_at_epoch,
expires_at_epoch, status, settlement_tx_id, current_version`) — `data`
itself is never echoed back. Remaining life is capped at 90 days at any one
time regardless of `retention_days`.

**`POST /storage/backups/:backup_id/renew`** — no body; extends the
current version's expiry by one more full 90-day term, capped at 90 days
remaining from now, priced from the backup's already-stored size at the
same per-KB rate (so the offer is only built once the backup is looked up:
`404 not_found` for an unknown id is free, and a backup already at the
90-day cap is `400 term_at_maximum`, free, payment not taken). Owner only —
a payment from any other wallet settles and is refused with `403
backup_owned_by_another_payer`, payment kept, nothing changed. Response:
the updated metadata plus `settlement_tx_id`. Expired backups get a 2-day
grace window before the reaper deletes their bytes; renew within it.

**Versioning.** A backup is transparently its own "version 1";
`GET /storage/backups/:backup_id` returns the **current** version's content.

- **`POST /storage/backups/:backup_id/versions`** — same query params, body,
  price shape and 10 MB cap as the initial store, owner only (same `403`
  contract as renew). Stores a NEW version under this same `backup_id` and
  makes it the current content; old versions are kept, each expiring on its
  own original schedule sized by ITS OWN `retention_days` at the time it was
  added (adding a version never extends an older version's life, and a
  later renew never extends an older version's either — only the current
  version's retention moves when you renew).
- **`GET /storage/backups/:backup_id/versions?limit=`** — owner only:
  `{"items": [{version, size_bytes, content_hash, label, created_at_epoch,
  expires_at_epoch, status, settlement_tx_id}, ...]}`, newest version first.
- **`GET /storage/backups/:backup_id/versions/:version`** — owner only: one
  specific version's metadata plus its base64 `data`, sha256-verified
  against the hash taken at upload time — same integrity guarantee as the
  unversioned detail route.

**`GET /storage/backups?limit=`** — this wallet's own live backups, newest
first, metadata only. **`GET /storage/backups/:backup_id`** — the current
version's metadata plus base64 `data`, sha256-verified on the way out (a
mismatch or an unreadable connector is a real `500`
`integrity_check_failed` / `backup_unreadable`, never silently-corrupt
data). `404 not_found` covers unknown, not-owned, expired and deleted alike
— the authenticated wallet is the only partition ever looked up.

**`DELETE /storage/backups/:backup_id`** — owner only, no body. Deletes the
backup **and every version stored under it**, not just the current one.
`{"deleted": true, "backup_id"}`.

## Fair use / what is excluded

The settlements feed only counts third-party payments. Any payment that
originates from an operator-controlled wallet (any wallet the operator
declares in the `X402_PROBE_PAYERS` setting, a comma-separated list of
addresses) is excluded in code, not by convention: it is recorded in the
settlement ledger like any other payment, then ignored wherever a public
figure is computed. Nothing in the codebase pays this marketplace's own
routes from its own wallets. Abusive content (a stored backup used for
abuse, a scan URL pointed at something illegal to fetch) can be removed by
an operator through the admin routes; a removed item is not refunded.

## Admin (operator only)

Not part of the marketplace surface; every one requires an authenticated
admin wallet session (the `X-Admin-Wallet` header alone is never trusted).
Listed so the operator paths above are known to exist:

- `GET /api/v1/admin/x402/promo`, `POST /api/v1/admin/x402/promo`,
  `DELETE /api/v1/admin/x402/promo?code=` — list, issue and deactivate
  promo codes.
- `POST /api/v1/admin/x402/refund-breaker/reset?resource=` — clear a
  tripped refund circuit breaker.
- `/api/v1/admin/x402-storage/inspect` and `/api/v1/admin/x402-storage/remove`
  — inspect or remove one stored backup for abuse/legal response.
- `POST /api/v1/internal/x402/storage/reap` — not admin, not public: the
  storage reaper beat on the same host calls it with a shared token
  (`X-Storage-Reaper-Token`); `404` when no token is configured.
