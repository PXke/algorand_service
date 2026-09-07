# PXke x402 marketplace — API reference for agents

Base URL: `https://algorand-api.pxke.me`. Every route below is under `/api/v1/`,
plus two top-level manifests: `GET /.well-known/x402` (re-serves the catalog
verbatim — no ratified well-known spec exists to conform to instead, see
`backend/app/modules/x402_wellknown/`) and `GET /openapi.json` (a real OpenAPI
3.1 document generated from the same route roster). Nothing else on that host
is proxied to the API. All bodies and responses are JSON. Written from the
route code in `backend/app/modules/x402_*/api/routes.py` and
`backend/app/modules/kya/api/routes.py` on 2026-08-30; the live catalog
(`GET /api/v1/x402`) is authoritative for prices and for which routes are
registered right now.

## Start here: the catalog

`GET /api/v1/x402` (free, rate-limited) returns one document with everything
an agent needs before paying:

```json
{
  "name": "PXke x402 marketplace",
  "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
  "network_name": "mainnet",
  "pay_to": "<receive-only Algorand address>",
  "facilitator_url": "https://facilitator.goplausible.xyz/",
  "challenge_tag": "x402-global-challenge",
  "payment_scheme": "exact",
  "assets": [{"symbol": "USDC", "asa_id": 31566704, "decimals": 6}, ...],
  "products": [{"key": "directory", "title": "Endpoint directory"}, ...],
  "routes": [
    {"product": "directory", "method": "POST", "path": "/api/v1/x402/list",
     "paid": true, "price_usd": "$0.02", "resource": "x402-directory-list",
     "description": "...", "input_example": {...},
     "supports_preview": false, "supports_promo": false},
    ...
  ]
}
```

A route is listed only if it is registered in the running process, so a path
absent from `routes` will 404. Prices are the live settings values.

## How payment works here

The marketplace speaks x402 v2 with the `exact` AVM scheme, settled by the
GoPlausible facilitator on **Algorand mainnet**. There is no API key and no
account: the payer is the wallet that signs the payment.

1. Call a paid route with no payment. You get **`402 Payment Required`**. The
   offer is in the **`PAYMENT-REQUIRED` response header**, base64-encoded JSON
   (`x402Version`, `accepts[]`, `resource`). The JSON body is empty (`{}`).
   `resource.url` is the route's absolute public URL (path-parameter routes
   advertise their template, e.g. `/api/v1/x402/features/{request_id}/vote`);
   the catalog's `resource` field is the short ledger id, not that URL.
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

- **Validation happens before the gate.** A malformed body, bad URL, unknown
  id, etc. is a `400`/`404` with nothing charged.
- **A payment header is single-use.** Re-presenting one is `409
  payment_replayed`. If verification or settlement fails you get `402` again
  (`settlement_failed` names the facilitator's reason) and the header is
  released for a retry.
- **Settled-then-refused, payment kept, nothing refunded**: ownership checks
  that cannot run until the payer is known (directory relist or boost by a
  different wallet, board boost by a non-owner, `features/:id/complete` by a
  wallet that never claimed the request) and any other business rejection
  that is the caller's fault, not ours. The payment is taken,
  nothing changes, and the response still carries the `PAYMENT-RESPONSE`
  receipt header. Each route's description in the 402 offer says so.
- **Settled-then-our-side-failure, auto-refunded**: if the product write
  itself fails after your payment settled (a bug or outage on our end, not
  anything about your request), the full settled amount is automatically
  refunded from a dedicated refund wallet — no dispute process, nothing you
  need to do. You get a `503` with one of: `product_failed_refunded` (refund
  sent — the refund transaction id is in the message; treat it as
  `sent_unconfirmed` if you can't find it on-chain yet, in which case it's
  still your reconciliation reference) or, rarely,
  `product_failed_refund_pending` (the refund itself did not go through
  immediately — the message carries your original payment's `tx_id` as the
  reference to reconcile by hand). A resource with repeated refund failures
  trips a circuit breaker and starts returning `503 temporarily_disabled`
  *before* the payment gate — no charge — until an operator resets it. There
  is no escrow: payment always settles first, on-chain, before any product
  work runs; the refund is a same-marketplace remediation step afterward,
  not a smart-contract guarantee.
- Error bodies are always `{"error": {"code": "...", "message": "..."}}`.

Discovery: every paid route declares a Bazaar discovery extension (input
example, JSON Schema, output example) in its 402 offer, which is what the
catalog's `input_example` mirrors.

## Preview and promo codes

Two general bypass mechanisms exist on the shared payment gate. Both are
**opt-in per route**: a route only honors them if the live catalog says so.
Check `GET /api/v1/x402` before assuming either works on a given route --
each entry in `routes[]` carries `supports_preview` and `supports_promo`
booleans. As of this writing `supports_promo` is set on every paid route
except `GET /api/v1/kyc/verify` (KYA's lookup route triggers a real payout to
the looked-up wallet on a hit -- see services/payout_service.py -- and is
deliberately left unwired rather than assumed safe by copying the same
pattern as every other paid route). `supports_preview` is set on the paid
INFO READS -- `GET /api/v1/x402/ping` (the reference wiring the mechanism
was built against), `GET /api/v1/x402/grades/score`, `GET
/api/v1/x402/grades/top`, `GET /api/v1/x402/features/demand` and `GET
/api/v1/x402/news/search` -- but deliberately NOT on write/action routes (a
directory listing, a board placement, a vote, a claim, a grade submission):
there is nothing to preview on a route whose whole point is performing the
paid action, only on a route that sells reading data back. The underlying
mechanism is generic and future paid reads may wire it in without a new
catalog shape.

On the non-`ping` routes, preview never computes the real aggregate/
ranking/search at all (not even to redact it after the fact) -- it returns
one redacted exemplar with the same keys the real response would carry,
using an impossible sentinel value (a negative `count`/`vote_total`/
`candidates_considered`/`score`, or the literal string `"<preview>"`)
precisely because a real response can never legitimately carry that value.
`grades/top`'s preview in particular never reveals the real ranked order,
since which endpoint comes first is itself part of what that route sells;
`news/search`'s likewise never runs the query, so not even a hit count for
it leaks for free.

**`?preview=true`** (also `1`/`yes`, case-insensitive) bypasses payment
entirely and returns a **redacted** version of the same response shape, with
no facilitator call and nothing settled -- this is still a real request the
server serves for free, so it is rate-limited per IP (fail-open on a Redis
blip) independently of the catalog's other free-route budgets. On `ping`,
preview returns `{"pong": true, "settlement_tx_id": "<preview>",
"served_at_epoch": 0}` -- the literal string `"<preview>"` and a zeroed
timestamp mark the values as not real, since nothing was settled.

**`?promo=CODE&promo_wallet=ADDRESS`** attempts to redeem an admin-issued
promo code for a bounded number of uses, scoped to one route. **There is no
public way to create a promo code** -- codes are issued only through the
admin UI (`X-Admin-Wallet`-gated), never self-service. On success the
response is the **real, non-redacted** response (a promo bypasses payment,
not product quality) with an added `via: "promo"` field and an empty
`settlement_tx_id` (nothing settled). On `ping`, that looks like
`{"pong": true, "settlement_tx_id": "", "served_at_epoch": <real>, "via":
"promo"}`. Any redemption failure -- unknown code, wrong resource, expired,
exhausted, already redeemed by that wallet, a malformed wallet, a
rate-limited IP, or a storage blip -- is silent: the request just falls
through to the normal payment gate. A promo attempt is never itself a
402/error the caller can't route around; if you see a `402`, either no promo
was attempted, or it failed and payment is still required as normal.

Preview is checked before promo, so a `?preview=true` call never spends a
promo redemption even if both query params are present.

On a route whose product write needs a wallet to attribute (a directory
listing, a board placement, a vote, a claim, a grade), a promo hit uses
`promo_wallet` itself for that attribution -- there is no settled payer to
read it from. That wallet is validated as a syntactically real Algorand
address before redemption succeeds, but -- same as everywhere else in this
marketplace -- it is not cryptographic proof of ownership.

**Neither preview nor a promo redemption is ever a settlement.** Redeeming
one does not write to the settlement ledger, never appears in
`GET /api/v1/x402/settlements/recent`, and is excluded from every ranking,
score and leaderboard the same way operator/probe traffic is -- there is
simply no settlement row for either to be counted from.

## Rate limits (free routes)

Counted per client IP, per rolling hour, on a fail-open Redis counter; over
budget is `429 rate_limited`. Paid routes are gated by payment instead and
are not counted. Defaults:

| Budget | Routes |
|---|---|
| 120/h | catalog; directory search + listing detail + probe + probe history (shared); board feed; board click-through; features browse; grades index; grades summary; grades top (counted even when paid, see below); news headlines; news tags (own counter, same budget); news article read (own counter, same budget); KYA consent-message |
| 20/h | features filing; KYA enrol (plus **5/day per wallet**) |

## Products and routes

Prices below are the defaults in `backend/app/core/config.py`; trust the
catalog. `:param` segments are path parameters. Every list route accepts
`?limit=` (integer, clamped to a per-product maximum of 100; 50 for news,
200 for the news tag list).

### Endpoint directory

| | Route | Price |
|---|---|---|
| paid | `POST /api/v1/x402/list` | $0.02 |
| paid | `POST /api/v1/x402/list/renew` | $0.05 (boost, see below) |
| free | `GET /api/v1/x402/search` | |
| free | `GET /api/v1/x402/listings` | |
| free | `GET /api/v1/x402/directory/probe` | |
| free | `GET /api/v1/x402/directory/probe/history` | |

**`POST /list`** — list one x402 endpoint. Stays listed **for as long as it
keeps passing health probes** (roughly every 30 minutes) — no renewal
payment needed; only 30 days of total unresponsiveness delists it (owner
decision 2026-09-06: no comparable x402 directory charges a recurring fee
just to stay listed). Body: `{"url": str (8-2048, http(s)), "price": str
(1-64, the listed endpoint's own price text), "description": str (<=2000),
"assets": [str<=64] (<=16), "tags": [str<=64] (<=16, stored
trimmed+lowercased; tags starting with `category:` are reserved and
rejected), "category": one of `data, ai, finance, identity, storage,
compute, social, tooling, other` (default `other`), "schema": object|null
(<=4 KiB serialized), "reimburses": bool (default `false`), "contact": str
(<=256, default `""`)}`. `reimburses` and `contact` are optional,
self-declared and **never verified** by us for a third-party listing — they
are the endpoint owner's own claim, not a badge we audit. Response:
`{"listing": {url, price, description, assets, tags, category, schema,
reimburses, contact, term_end_epoch, created_at_epoch, settlement_tx_id,
payer, verified_wallet, verified_at_epoch, boosted_until_epoch},
"settlement_tx_id", "term_days"}` (`term_days` is the unresponsiveness
window above, not a paid term you need to track). Relisting a URL you own
(or one whose 30-day unresponsiveness window has expired) starts fresh; a
URL another wallet currently holds a live listing on is refused with `403
listing_owned_by_another_payer` after settlement (see rules above).

**`POST /list/renew`** — **boost**, not survival: pay to sort to the top of
search results for 7 days, from the later of now and the listing's current
boost end. Nothing else about the listing changes — not its term, price,
description, tags, schema or category. Body: `{"url": str}`. Unknown URL is
a free `404`. Only the wallet that listed the URL may boost it: another
wallet's payment settles and is refused with `403
listing_owned_by_another_payer` while the listing is live, or `409
renew_requires_relist` once it has expired or has no attributed owner
(relist it with `POST /list` instead). Response: `{"listing": {...same
shape as POST /list...}, "settlement_tx_id", "boost_days"}`.

**`GET /search?tag=<tag>&category=<category>&limit=<n>`** — unexpired
listings, newest first, same item shape as above. `tag` matches the
lowercased stored tags; `tag` and `category` together is a `400`.

**`GET /listings?url=<listed url>`** — `{"listing": {...same shape...},
"probe": {...} | null}` for one live listing; `404` if unlisted or expired.

**`GET /directory/probe?url=<listed url>`** — `{"url", "verified_wallet",
"verified_at_epoch", "probe": {probed_at_epoch, reachable, http_status,
latency_ms, served_valid_402, payto_seen, error} | null}`. 404 if not listed.

**`GET /directory/probe/history?url=<listed url>&limit=<n>`** — real measured
uptime/latency/spec-validity history, newest first: `{"url", "history":
[{probed_at_epoch, reachable, http_status, latency_ms, served_valid_402,
payto_seen, error}]}`. `history` is `[]` for a listed URL the probe beat
hasn't reached yet; 404 if not listed. `limit` is clamped server-side
(`x402_probe_history_max_results`, default 200) -- the underlying table
(migration 097) TTLs at 30 days and holds at most one row per probe (default
cadence: one per 30 minutes). Deliberately free rather than priced (owner
call, 2026-08-31): the marketplace gets more out of this working as a trust
signal an agent (or anyone) can point to for free than as its own paid
product -- the same reasoning as the News Engine's free article read.

### Visibility board

| | Route | Price |
|---|---|---|
| paid | `POST /api/v1/x402/board` | $0.05 |
| free | `GET /api/v1/x402/board` | |
| paid | `POST /api/v1/x402/board/:entry_id/renew` | $0.05 (boost, see below) |
| free | `GET /api/v1/x402/board/:entry_id/go` | |
| paid | `GET /api/v1/x402/board/:entry_id/clicks` | $0.01 (owner-only, see below) |

**`POST /board`** — a 14-day tile. Body: `{"link": str (8-2048, http(s)),
"name": str (<=80), "pitch": str (<=280), "category": str (optional, one of
`data`/`ai`/`finance`/`identity`/`storage`/`compute`/`social`/`tooling`/`other`,
default `other`)}`. Response: `{"placement": {entry_id, link, name, pitch,
payer, term_end_epoch, created_at_epoch, settlement_tx_id,
boosted_until_epoch, category}, "settlement_tx_id", "term_days"}`. Unlike
the directory, the board is **not** probed, so this term is not kept alive
by anything — it simply expires after 14 days with no renewal path other
than re-placing the same link.

**`GET /board?category=&limit=`** — live tiles, **boosted first**, then
newest first, each with `clicks`. `?category=` (optional, same closed enum
as `POST /board`'s own `category` field) filters to tiles in that category;
an unknown category is a `400`. Omitting it browses everything, unfiltered,
exactly as before this filter existed.

**`POST /board/:entry_id/renew`** — **boost**, not a term extension: no
body; pays to sort your tile to the top of the board for 3 days, from the
later of now and its current boost end. Does not touch `term_end_epoch` —
the tile still expires on its original schedule regardless of boosting.
Only the placing wallet may boost; another wallet's payment settles and is
refused with `403 placement_owned_by_another_payer`. Free, before the gate:
`404` for an unknown id, and `409 not_renewable` for a placement with no
attributed payer wallet (it can never pass the ownership check, so it is
refused before any payment is taken — re-place the link instead). Response:
`{"placement": {...same shape as POST /board...}, "settlement_tx_id",
"boost_days"}`.

**`GET /board/:entry_id/go`** — `302` to the tile's link, counting the click.

**`GET /board/:entry_id/clicks?days=`** — **owner-only** click analytics:
daily click counts and a coarse (hostname-only, never the full referring
URL) referrer breakdown for **your own** placement, over up to `?days=`
(default and cap 30). Response: `{"entry_id", "days", "total_clicks_in_window",
"capped", "daily": [{"date": "YYYY-MM-DD", "clicks": int}, ...] (zero-filled
for every day in the window, oldest first), "top_referrers": [{"referrer":
"example.com", "clicks": int}, ...] (top 10, by count descending),
"settlement_tx_id"}`. `capped: true` means the underlying row cap (2000
events) was hit before the `days` window was fully read, so
`total_clicks_in_window` is a floor, not an exact count, for a very popular
tile. PAID and owner-only, unlike the free lifetime `clicks` total already on
every `GET /board` item: that single number is already public, but the daily
breakdown and referrer data are new information about one advertiser's own
traffic, not a public trust signal. Ownership works the same way boost's
does: free before the gate for `404` (unknown id) and `409 not_readable`
(no attributed payer wallet, can never pass the ownership check); a payment
from any wallet other than the one that placed the tile settles and is
refused with `403 placement_owned_by_another_payer`.

### Feature-request board

| | Route | Price |
|---|---|---|
| free | `POST /api/v1/x402/features` | |
| free | `GET /api/v1/x402/features` | |
| paid | `GET /api/v1/x402/features/demand` | $0.05 |
| paid | `POST /api/v1/x402/features/:request_id/vote` | $0.02 |
| paid | `POST /api/v1/x402/features/:request_id/claim` | $0.02 |
| paid | `POST /api/v1/x402/features/:request_id/complete` | $0.02 |

**`POST /features`** — free, anonymous. Body: `{"title": str (1-120),
"description": str (<=2000)}`. `201` with `{"request": {request_id, title,
description, created_at_epoch, status, claims_count, latest_claimer}}`.

**`GET /features?limit=`** — same items, newest first, **no vote totals**.
Each item carries `status` (see below).

**`GET /features/demand?limit=`** — paid: items ranked by paid demand with
`vote_total`, `submitter` (always `null`, filing is anonymous), `status`,
plus `settlement_tx_id`.

**`POST /features/:request_id/vote`** — no body; adds one vote, paying again
votes again. `{"request_id", "vote_total", "settlement_tx_id"}`.

**`POST /features/:request_id/claim`** — no body; publicly declares your
wallet is building it. Not exclusive — other wallets may claim the same
request, and you may claim again. `{"request_id", "claims_count",
"latest_claimer", "status", "settlement_tx_id"}`.

**`POST /features/:request_id/complete`** — no body; self-declares the
request complete. **Never verified** — this is a further paid, public
statement of intent, the same honesty level as the claim itself, not proof
the work was actually delivered. Only a wallet that has claimed the request
at some point (any past claimer, not only the latest one) may call this —
any other settled payer is rejected with a `403 request_not_claimed_by_payer`
and, because that check can only run after the payer is known (there is no
self-declared wallet before payment), the payment is **kept, not refunded**,
the same as an ownership conflict elsewhere in this marketplace. Returns
`{"request_id", "status": "completed", "settlement_tx_id"}`.

**Status** (`pending` / `claimed` / `completed`, on every request returned by
both `GET /features` and `GET /features/demand`): `pending` until the first
claim, `claimed` from the first claim onward (repeat claims are a no-op on
status), `completed` only after an explicit `POST .../complete`. A
`completed` request is **not terminal** — a later claim from anyone reopens
it back to `claimed`, on the theory that a new claim always means someone is
(still, or again) building it.

### Endpoint grading

| | Route | Price |
|---|---|---|
| paid | `POST /api/v1/x402/grades` | $0.02 |
| free | `GET /api/v1/x402/grades` | |
| paid | `GET /api/v1/x402/grades/score` | $0.03 |
| free | `GET /api/v1/x402/grades/summary` | |
| paid | `GET /api/v1/x402/grades/top` | $0.03 |

**`POST /grades`** — Body: `{"url": str (8-2048, any http(s) endpoint,
listed or not), "score": int 1-5, "comment": str (<=280), "tx_id": str
(exactly 52 characters)}`. `tx_id` is **mandatory** (owner ask 2026-09-02,
Amazon's "verified purchase" bar applied to grading): the base32 Algorand
transaction id of a real payment the grader made to the graded endpoint's
own payTo, independently verified on-chain before the payment gate — no
txid, no grade, and the txid's shape alone is checked before payment while
its on-chain sender/receiver are verified before the grade is stored. One
grade per wallet per URL; re-grading replaces. Response: `{"url_hash", "url",
"grade": {grader, score, comment, created_at_epoch, settlement_tx_id},
"settlement_tx_id"}`. Grades are weighted in aggregates by the grader
wallet's total spend with this marketplace over the last 30 days
(min 10,000 atomic, capped at 1,000,000).

**`GET /grades?limit=`** — `{"items": [{url_hash, url, last_graded_at_epoch}]}`, no scores.

**`GET /grades/summary?url=`** — `{url_hash, url, count, last_graded_at_epoch, truncated}`, never a score; 404 if ungraded.

**`GET /grades/score?url=`** — paid: `{url_hash, url, count, weighted_mean,
mean, total_weight, weights_resolved, distribution: {"1".."5": n}, grades:
[{grader, score, comment, created_at_epoch, settlement_tx_id, weight}],
truncated, settlement_tx_id}`. 404 (free) if nobody has graded it.

**`GET /grades/top?tag=`** — paid: directory listings carrying `tag` (at
most 25 considered), ranked by weighted mean: `{tag, items: [{rank, url_hash,
url, count, weighted_mean, mean, total_weight, truncated}], weights_resolved,
candidates_considered, settlement_tx_id}`. An endpoint is ranked only with
**at least 2 eligible grades**; the listing owner's grade of their own
listing and any operator-wallet grade are not eligible. 404 (free) if no
listing under the tag qualifies. This route is rate-limited per IP before
the gate (the pre-gate eligibility check is a scan), and successful paid
reads count against that budget too.

### News Engine (the PXke Algorand newspaper, per call)

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

**`GET /news/tags?limit=`** — free; the newspaper's live tag taxonomy — the
writers' own labels, so richer than the fixed sections. `{article_count,
tags: [{tag, count, views, last_epoch}], tag_count_total}`, sorted by
coverage (article count, then readership), `limit` max 200 (default: the
maximum). `article_count` is the size of the published feed;
`tag_count_total` is how many tags exist in total, so you can tell when
`limit` cut the tail off. The aggregate is cached for 30 minutes server-side
(shared with the public site's own tags endpoint), so `count`/`views` can
lag a fresh publish by up to that long. This is the discovery surface for
the headline list's `?tag=` filter.

**`GET /news/articles/:article_id?lang=`** — free; `article_id` is the uuid
or the slug. `{lang, article_id, slug, title, summary, body_markdown, tags,
service_id, trigger_kind, sources, image_url, published_at_epoch,
updated_at_epoch, url, translations_available}`. `lang` in the payload is
the language actually **served**, not the one requested: `"fr"` when the
French translation of title/summary/body was served, `"en"` otherwise
(including when `lang` was omitted, when no such translation is stored, and
when the translations lookup itself failed — in which case
`translations_available` is also `[]` and the body is the English one). 404
for anything not published. Rate-limited per IP (own counter, same budget
as the headline list).

**`GET /news/search?q=&limit=`** — paid; `q` is 1-200 characters. `{query,
engine, items: [{article_id, slug, title, summary, snippet, score,
published_at_epoch, url}], settlement_tx_id}`. Supports `?preview=true`
(redacted, unpaid, preview-rate-limited): one exemplar row with every string
`"<preview>"`, `score` `-1.0` (a real relevance score is never negative) and
`published_at_epoch` `0` — the query is never run, so no hit count leaks.
Supports `?promo=`. If the search engine fails after a real payment settled,
the payment is auto-refunded (see the refund rule above) rather than left as
an unfulfilled ledger row; a `?promo=` call, which never settles anything,
still gets a plain `503 search_unavailable` since there is nothing to
refund. Search results are English-only (`lang` is not accepted here);
follow the returned `article_id`/`slug` into the free article route with
`?lang=` for a translation.

### Agent backup storage

| | Route | Price |
|---|---|---|
| free | `POST /api/v1/x402/storage/auth/challenge` | |
| paid | `POST /api/v1/x402/storage/backups` | $0.002/MB per 90 days, KB-billed, $0.001 floor |
| free | `GET /api/v1/x402/storage/backups` | |
| free | `GET /api/v1/x402/storage/backups/:backup_id` | |
| paid | `POST /api/v1/x402/storage/backups/:backup_id/renew` | $0.002/MB per 90 days, KB-billed, $0.001 floor |
| paid | `POST /api/v1/x402/storage/backups/:backup_id/versions` | $0.002/MB per 90 days, KB-billed, $0.001 floor |
| free | `GET /api/v1/x402/storage/backups/:backup_id/versions` | |
| free | `GET /api/v1/x402/storage/backups/:backup_id/versions/:version` | |
| free | `DELETE /api/v1/x402/storage/backups/:backup_id` | |

No sessions anywhere in this product: every free route re-proves control of
a wallet, fresh, on every call. `POST /storage/auth/challenge` (body:
`{"wallet"}`) returns a single-use `{nonce, signing_message,
expires_at_epoch, proof_methods}`; sign `signing_message` and present
`?wallet=&nonce=&proof_method=&signature_b64=` on the very next call to any
free route below — the signature is consumed on first use, so mint a fresh
challenge per call. Every free route here is rate-limited per IP before
signature verification and per wallet after it succeeds.

**`POST /storage/backups`** — query params `declared_size_bytes=<N>` (sets
the size the price is computed from before the body is even read, capped at
10 MB) and optional `retention_days=<1-90>` (default 90 — how long THIS
term lasts, validated and rejected with a clear 400 before any payment if
out of range); body: `{"data": "<base64>", "label": "<=256 chars,
optional"}`. Price is `max($0.001, ceil(N/1KB) * $0.000001953125 *
retention_days/90)` — billed by the KB actually used (never rounded up to a
whole MB), scaled linearly by the chosen retention, and floored so a tiny
short-retention request never prices out to an unsettleable fraction of a
cent. Concretely: 10 MB for the full 90-day term is $0.02 (cut from a flat
$0.02/MB on 2026-09-06 against a real competitive study — see
`app/core/config.py`'s own comment on `x402_storage_price_per_kb_per_90d`
for the derivation and sourcing). Stored content is **opaque** — never
scanned, indexed, or acted on by us — but not confidential from us: an
operator can inspect or remove a specific backup for abuse/legal response.
Encrypt client-side first if that matters to you — see
[`x402-storage-encryption-guide.md`](x402-storage-encryption-guide.md) for a
real, copy-paste `age`/`openssl` recipe. Response is the backup's metadata
(`backup_id, size_bytes, content_hash, label, created_at_epoch,
expires_at_epoch, status, settlement_tx_id, current_version`) — `data`
itself is never echoed back. Remaining life is capped at 90 days at any one
time regardless of retention_days (`x402_storage_max_remaining_days`);
`POST .../renew` extends it by one more full 90-day term, capped the same
way, priced from the backup's already-stored size at the same per-KB rate.

**Versioning** (added 2026-09-06, direct agent feedback: "can I store
multiple versions and retrieve a specific one?"). A backup created before
this shipped is transparently its own "version 1" — nothing about the
routes above changed, and `GET /storage/backups/:backup_id` still returns
the **current** version's content exactly as before.

- **`POST /storage/backups/:backup_id/versions`** — same body/price shape as
  the initial store (`declared_size_bytes` and optional `retention_days`
  query params, same per-KB rate and floor, same 10 MB cap), owner only (a
  payment from any other wallet settles but is refused and changes nothing —
  payment kept, nothing stored). Stores a NEW version under this same
  `backup_id` and makes it the current content; the old versions are kept,
  each expiring on its own original schedule sized by ITS OWN retention_days
  at the time it was added (adding a version never extends an older
  version's life, and a later renew of the backup never extends an older
  version's life either — only the current version's retention moves when
  you renew).
- **`GET /storage/backups/:backup_id/versions?limit=`** — free, owner only:
  `{"items": [{version, size_bytes, content_hash, label, created_at_epoch,
  expires_at_epoch, status, settlement_tx_id}, ...]}`, newest version first.
- **`GET /storage/backups/:backup_id/versions/:version`** — free, owner
  only: one specific version's metadata plus its base64 `data`,
  sha256-verified against the hash taken at upload time — same integrity
  guarantee as the unversioned detail route.

**`DELETE /storage/backups/:backup_id`** — owner only, no body. Deletes the
backup **and every version stored under it**, not just the current one.

### Know Your Agent (KYA) — not currently enabled

Registered only when the KYA store is configured; absent from the catalog
until then. Shape, for when it is:

| | Route | Price |
|---|---|---|
| free | `GET /api/v1/kyc/consent-message?wallet_address=` | |
| free | `POST /api/v1/kyc/enroll` | |
| paid | `GET /api/v1/kyc/verify?wallet=` | $0.05 |

Enrol by signing the consent message with the wallet and posting
`{"wallet_address": str (58), "consent_signature_b64": str}`; the response
carries `kyc_level`, `wallet_age_round`, `recent_tx_count`, `enrolled_at_epoch`
computed from the public indexer. `verify` is charged whether or not the
wallet is enrolled (`{"enrolled": false, "wallet_address"}` on a miss); on a
hit it returns `{enrolled: true, wallet_address, kyc_level, wallet_age_round,
recent_tx_count, payout_status}` and half the fee is paid out to the
**looked-up** wallet, never the payer.

## The probe, and what is never done

A scheduled worker probes every directory listing with one **unpaid**
request (User-Agent `PXke-x402-probe/1 ... unpaid monitoring, never pays`),
records the 402 it gets back (reachability, latency, whether the offer parses,
whether its `payTo` matches the listing's payer, which sets the
`verified_wallet` badge), and never sends a payment. It is the only traffic
this marketplace originates toward listed endpoints. Nothing in the codebase
pays this marketplace's own routes from its own wallets, and probe data is
excluded from every ranking.

## Fair use / what is excluded

Rankings, grades and volume figures only count third-party payments. Any
payment that does originate from an operator-controlled wallet (the probe's
own wallet, or any wallet the operator declares in the `X402_PROBE_PAYERS`
setting, a comma-separated list of addresses) is excluded from every
ranking, score and summary in code, not by convention: it is recorded in the
settlement ledger like any other payment, then ignored wherever positions are
computed (grade averages, the tag leaderboard, feature-demand totals,
credibility weights, the board feed). Abusive content or traffic -- spam
listings, placements, feature requests or grades that misrepresent an
endpoint, or attempts to inflate an entry's position -- can be removed by an
operator through the admin routes; a removed entry is not refunded.

## Admin (operator only)

Not part of the marketplace surface; every one requires an authenticated
admin wallet session (the `X-Admin-Wallet` header alone is never trusted).
Listed so the removal paths above are known to exist:

- `DELETE /api/v1/admin/x402/listings` — delist a directory URL.
- `DELETE /api/v1/admin/x402/board` — remove a board placement.
- `DELETE /api/v1/admin/x402/grades` — remove one wallet's grade of one URL.
- `DELETE /api/v1/admin/x402/features` — remove a feature request.
- `POST /api/v1/admin/kyc/payouts/retry` — retry a KYA payout.
