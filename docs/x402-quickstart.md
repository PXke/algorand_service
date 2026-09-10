# PXke x402 marketplace — quickstart for agents

This is the "first five minutes" walkthrough: discover what's for sale, try
it for free, make one real paid call, know what happens if it goes wrong,
then use each of the three products once. Every example below is a real
request shape against the current code, not a paraphrase.

**This is not a tutorial on how x402 payments work.** For "how do I build
and sign an Algorand x402 payment in Python," use
[`skills/algorand-x402-python/`](../skills/algorand-x402-python/SKILL.md) —
client/server/facilitator patterns, `x402-avm` usage, the whole protocol
mechanics. This doc assumes you already have (or will get) a signer and
picks up from there: this marketplace's actual endpoints, prices, and
conventions. For the full field-by-field route reference (every request/
response shape, every error code, rate limits), see
[`x402-marketplace-api.md`](x402-marketplace-api.md) — this doc is the
narrative walk-through, that one is what you keep open while integrating.
For the facilitator/CAIP-2/challenge-tag mechanics specific to this build,
see [`x402-facilitator.md`](x402-facilitator.md).

Base URL: `https://algorand-api.pxke.me`. Human-readable storefront for the
same three products: `https://x402.pxke.me` (free to browse, no wallet
needed; `/developers` there mirrors this page).

## 0. What's for sale right now

Snapshot of the live catalog on 2026-09-10 — 3 products plus the catalog
itself, 19 routes, all on Algorand **mainnet**, all paying to one
receive-only address, all accepting USDC (preferred), EURQ and USDQ. The
catalog (§1) is the source of truth; this table exists so you can see the
shape of the whole marketplace in one screen before fetching anything.
"Free" routes are rate-limited per IP (and per wallet where a wallet is
proven); "paid" routes answer `402` until paid.

| Product (`product` key) | Routes | Paid from | What it is |
|---|---|---|---|
| Catalog (`catalog`) | 2 (+ `/.well-known/x402`, `/openapi.json`) | free | This document, and a free proof-of-volume settlement feed |
| News Engine (`news`) | 4 | $0.001 | Free headlines, free tag taxonomy and free full article reads (`?lang=` serves the stored translations) from the PXke Algorand newspaper; paid ranked full-text search (previewable) |
| Sandboxed file/tarball scan (`scan`) | 1 | $0.01 | Static malware/archive scan of a file fetched from a URL you'd rather not open yourself, in a container that never executes it (previewable) |
| Agent backup storage (`storage`) | 9 | $0.001 floor | Store an opaque backup blob (versioned — add new versions under the same id, fetch any specific one) for a caller-chosen retention (1-90 days), billed by the KB at $0.002/MB per 90 days; wallet-signature-authenticated list/get/delete for free, paid store/renew/add-version |

Every route carries a plain-English `description` in the catalog, and every
paid route also carries an `input_example`, so the catalog entry itself plus
`GET /openapi.json` is a complete per-route reference — read those rather
than guessing at a body shape.

If you integrated before 2026-09-10: the endpoint directory
(`/x402/list`, `/x402/search`, `/x402/listings`, `/x402/directory/*`), the
visibility board (`/x402/board`), the feature-request board
(`/x402/features`), grading (`/x402/grades`), the uptime check, the agent
social network (`/x402/social`), fulfillment receipts, KYA (`/kyc/*`) and
the `/x402/ping` route were all removed
([ADR-0006](adr/ADR-0006-x402-consolidation.md)) and now `404`. Nothing
about payment mechanics changed.

## 1. Discovery: find what's for sale without knowing anything in advance

Don't hardcode endpoint knowledge. Call the catalog first:

```bash
curl https://algorand-api.pxke.me/api/v1/x402
```

The same document is also served at the conventional well-known URI several
x402 directories already crawl (`GET /.well-known/x402`), and as a real
OpenAPI 3.1 document (`GET /openapi.json`) if your tooling prefers that
shape. All three are free, rate-limited, and generated from the same live
route roster — a route only appears if it's actually registered right now,
so this is the source of truth over anything cached, including this doc.

The document you get back has everything you need to act without prior
knowledge:

```json
{
  "name": "PXke x402 marketplace",
  "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
  "network_name": "mainnet",
  "pay_to": "<receive-only Algorand address>",
  "facilitator_url": "https://facilitator.goplausible.xyz/",
  "assets": [{"symbol": "USDC", "asa_id": 31566704, "decimals": 6}, "..."],
  "sections": [{"key": "meta", "title": "Start here"}, {"key": "services", "title": "Services"}],
  "products": [{"key": "news", "section": "services", "entry": "GET /api/v1/x402/news", "status": "live", "..."}],
  "routes": [
    {
      "product": "news", "method": "GET", "path": "/api/v1/x402/news/search",
      "paid": true, "price_usd": "$0.001", "resource": "x402-news-search",
      "supports_preview": true, "supports_promo": true,
      "input_example": {"q": "tinyman volume", "limit": 10}
    },
    "..."
  ]
}
```

Iterate `routes[]` and you know, for every currently-live route: its method
and path, whether it's paid and at what price, its ledger `resource` id
(what shows up in settlement records), and — the two things this quickstart
cares about next — whether it honors `?preview=true` or `?promo=` (see
§2). Don't assume either works on a route the catalog doesn't say so for.

## 2. Testing for free

Two independent bypass mechanisms exist. Both are opt-in per route — check
`supports_preview`/`supports_promo` in the catalog entry before assuming
either works on a given path.

### Preview — self-serve, no code needed

`?preview=true` (also `1`/`yes`) skips payment entirely and returns a
**redacted** version of the real response shape — same keys, sentinel
values (`"<preview>"`, `null` where a real verdict would be, or a negative
score that a real response could never legitimately carry). Nothing is
settled, no facilitator call happens, and the real work is never run (the
search engine is never queried, no file is ever fetched), but it's still a
real served request so it's rate-limited per IP. This is wired on the two
paid *reads* — `GET /api/v1/x402/news/search` and `POST /api/v1/x402/scan/url`
— not on the storage write routes, since there's nothing to preview on a
route whose entire point is performing the paid action.

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/news/search?q=algorand&preview=true"
# {"query": "algorand", "engine": "<preview>",
#  "items": [{"article_id": "<preview>", "slug": "<preview>", "title": "<preview>",
#             "summary": "<preview>", "snippet": "<preview>", "score": -1.0,
#             "published_at_epoch": 0, "url": "<preview>"}],
#  "settlement_tx_id": "<preview>"}

curl -X POST "https://algorand-api.pxke.me/api/v1/x402/scan/url?preview=true" \
  -H "Content-Type: application/json" -d '{"url": "https://example.com/file.zip"}'
# {"source_url": "<preview>", "status": "preview",
#  "one_line_summary": "Preview only -- no file was fetched or scanned. Pay to run a real scan.",
#  "clamav": {"engine": "<preview>", "clean": null, ...},
#  "risk": {"score": -1.0, "verdict": "<preview>", "malicious": null, "caution_notes": []},
#  "settlement_tx_id": "<preview>", ...}
```

Compare that to a real paid call (`settlement_tx_id` is a real on-chain tx
id, `score`/`malicious` are real) and you can see exactly what preview does
and doesn't give you: the shape, never the substance.

### Promo codes — admin-issued, not self-service

`?promo=CODE&promo_wallet=<your Algorand address>` attempts to redeem an
admin-issued code for a bounded number of uses on one specific resource.
**There is no public way to mint your own code** — codes exist only through
the operator's admin UI, typically handed to a specific agent or tester for
a specific route. If you've been given one:

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/news/search?q=algorand&promo=YOUR-CODE&promo_wallet=YOURWALLETADDRESSHERE"
# {"query": "algorand", "engine": "typesense", "items": [...], "settlement_tx_id": "", "via": "promo"}
```

A successful redemption gets you the **real, non-redacted** response (a
promo bypasses payment, not product quality), tagged `"via": "promo"`, with
an empty `settlement_tx_id` since nothing settled. Any failure — unknown
code, wrong resource, expired, exhausted, already used by your wallet, a
malformed wallet address, or a storage blip — is silent: the request just
falls through to the normal payment gate, so you'll see a `402`, not a promo
error. `supports_promo` is set on the two paid reads and not on the storage
writes.

If you don't have a code, preview is your free-testing path for the routes
that support it; for a storage write with no preview support, the way to
see the real shape without paying is the `input_example`/`output_example`
already in the catalog and in
[`x402-marketplace-api.md`](x402-marketplace-api.md).

## 3. Making a real paid call

Full round trip against `GET /api/v1/x402/news/search` ($0.001 — the
cheapest paid route, good for a first live test: a mistake costs a tenth
of a cent and no product state changes):

**Step 1 — call with no payment.** You get `402 Payment Required` with an
empty JSON body; the actual offer is in the `PAYMENT-REQUIRED` response
header, base64-encoded:

```bash
curl -si "https://algorand-api.pxke.me/api/v1/x402/news/search?q=algorand"
```

```
HTTP/1.1 402 Payment Required
PAYMENT-REQUIRED: eyJ4NDAyVmVyc2lvbiI6MSwiYWNjZXB0cyI6WyJz...
Content-Type: application/json

{}
```

Base64-decode that header and you get:

```json
{
  "x402Version": 1,
  "accepts": [
    {
      "scheme": "exact",
      "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=",
      "asset": "31566704",
      "amount": "1000",
      "payTo": "<the marketplace's receive-only address>",
      "extra": {"decimals": 6, "tag": "x402-global-challenge"}
    }
  ],
  "resource": {"url": "https://algorand-api.pxke.me/api/v1/x402/news/search"}
}
```

USDC (`asa_id 31566704` on mainnet) is listed first and preferred; EURQ/USDQ
may also appear at the oracle-converted equivalent. Your wallet needs to be
opted into whichever ASA you pay with.

**Step 2 — build and sign the payment.** This is exactly the mechanics
covered in
[`skills/algorand-x402-python/references/create-python-x402-client.md`](../skills/algorand-x402-python/references/create-python-x402-client.md)
— construct the `accepts[]` entry into a signed asset-transfer transaction.
If you're hand-rolling a signer rather than using the package's client
wrapper, see
[`x402-facilitator.md`](x402-facilitator.md#the-packages-own-clientavmsigner-docstring-example-is-wrong-in-three-places)
first — the package's own docstring example for `ClientAvmSigner` doesn't
work as written against the installed `x402-avm==2.0.2`, and that page has
a working implementation. The Python SDK in this repo (`x402-client/`,
`pxke_x402`) does all of this for you: `client.news_search("algorand")`.

**Step 3 — retry with the payment attached** in the `PAYMENT-SIGNATURE`
request header:

```bash
curl -si "https://algorand-api.pxke.me/api/v1/x402/news/search?q=algorand" \
  -H "PAYMENT-SIGNATURE: <base64-encoded signed payment>"
```

```
HTTP/1.1 200 OK
PAYMENT-RESPONSE: eyJzZXR0bGVtZW50X3R4X2lkIjoi...
Content-Type: application/json

{
  "query": "algorand",
  "engine": "typesense",
  "items": [{"article_id": "...", "slug": "...", "title": "...", "summary": "...",
             "snippet": "... <mark>Algorand</mark> ...", "score": 1.23,
             "published_at_epoch": 1756377600, "url": "https://algorand.pxke.me/news/articles/..."}],
  "settlement_tx_id": "ABCDEF...52-CHAR-TXID"
}
```

The server verifies and settles through the GoPlausible facilitator before
running the route — you don't separately call the facilitator. The
facilitator covers the Algorand network fee; your transaction pays 0 fee.
Every paid response body carries `settlement_tx_id`, and the receipt is
also echoed in the `PAYMENT-RESPONSE` header. A payment header is
single-use — resubmitting it is `409 payment_replayed`, not a second
charge.

## 4. What happens if something goes wrong

This is the honest trust-signal question every agent asks before paying an
unfamiliar service: *what if I pay and get nothing?*

The marketplace makes a real, code-backed guarantee here, but it's narrower
than "any complaint gets your money back" — read the distinction carefully:

- **You don't like the response, the search came back empty, the scan found
  nothing, you changed your mind** — none of this is refunded. Payment
  settles on-chain before any product work runs; there is no escrow and no
  dispute process. This is the same as paying any other x402 resource.
- **Your own input was the problem after payment** — the URL you asked us
  to scan refused the connection, timed out or was over the size cap (`422
  fetch_failed`); you tried to renew or add a version to a backup another
  wallet created (`403 backup_owned_by_another_payer`) — the payment is
  **taken and kept**, the write is refused, and you still get a settlement
  receipt. Each such route's 402 offer description says so, precisely
  because it's the caller's own action, not a platform failure: refunding a
  fetch failure would make refunds free to trigger on demand.
- **The product write itself fails after your payment settled** — the
  search engine is down, the scan sandbox crashes, the storage connector
  fails: a bug or outage on the marketplace's side, not anything about your
  request — is where the real guarantee kicks in. `run_with_refund` (in
  `backend/app/modules/x402/paid_request.py`) wraps every product write on
  every paid route: on an unexpected failure it automatically sends the
  **full settled amount back** from a dedicated refund wallet, records the
  refund on the settlement ledger, and returns a `503` — not manual, not a
  promise you have to chase:
  - `product_failed_refunded` — the refund transaction id is in the message;
    if you can't find it on-chain immediately, treat it as
    `sent_unconfirmed` and use it as your reconciliation reference.
  - `product_failed_refund_pending` (rare) — the refund broadcast itself
    didn't confirm; the message carries your **original** payment's
    `tx_id` to reconcile by hand.
  - A resource with repeated refund failures trips a circuit breaker and
    starts returning `503 temporarily_disabled` **before** the payment gate
    — no charge at all — until an operator resets it.

There is no escrow anywhere in this design: every payment settles on-chain
first, and a refund (when one happens) is a same-marketplace remediation
step afterward, never a smart-contract guarantee.

## 5. Scanning a file you'd rather not open yourself

`POST /api/v1/x402/scan/url` ($0.01) fetches a URL server-side and runs it
through a network-isolated container that never executes it: ClamAV, file
type, entropy, embedded URLs/IPs, YARA, fuzzy hash, and a bomb-safe member
listing for zip/tar archives. Same 402/retry dance as §3, with a JSON body:

```bash
curl -si -X POST "https://algorand-api.pxke.me/api/v1/x402/scan/url" \
  -H "PAYMENT-SIGNATURE: <signed payment for $0.01>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/suspicious-download.zip"}'
```

```json
{
  "source_url": "https://example.com/suspicious-download.zip",
  "download_bytes": 18422, "status": "ok",
  "one_line_summary": "No concerns found -- no indicators -- file type: Zip archive data",
  "target": {"type": "Zip archive data", "entropy_bits_per_byte": 7.91, "indicators": {"urls": [], "ipv4_addresses": []}, "...": "..."},
  "clamav": {"engine": "clamdscan", "clean": true, "infected_files": []},
  "archive": {"archive_kind": "zip", "member_count": 3, "members": [], "...": "..."},
  "risk": {"score": 0.0, "verdict": "no concerns found", "malicious": false, "caution_notes": []},
  "settlement_tx_id": "..."
}
```

Act on `risk.malicious`; read `caution_notes` for why. Limits: 1 GiB
download, 60 s fetch, 90 s sandbox, 30 calls/hour per IP (paid or not),
and a small concurrency cap — if capacity is full you get `503
scan_unavailable` *before* paying, never a charge. A URL we cannot fetch
(refused, timed out, non-200, private/loopback address) is `422
fetch_failed` with the payment kept (§4). Static analysis only: "no
concerns found" means no known indicator, not "safe to run".

## 6. Backing up your own state

Agent backup storage is the one product with a free *authenticated*
surface, and it has no sessions: every free call proves wallet control
fresh.

**Store** (paid, price from `declared_size_bytes` and `retention_days`,
computed before the body is read):

```bash
BODY=$(base64 -w0 my-agent-state.json.age)   # encrypt first — see x402-storage-encryption-guide.md
curl -si -X POST "https://algorand-api.pxke.me/api/v1/x402/storage/backups?declared_size_bytes=$(stat -c%s my-agent-state.json.age)&retention_days=30" \
  -H "PAYMENT-SIGNATURE: <signed payment>" \
  -H "Content-Type: application/json" \
  -d "{\"data\": \"$BODY\", \"label\": \"state-2026-09-10\"}"
# {"backup_id": "...", "size_bytes": 4096, "content_hash": "<sha256>", "label": "state-2026-09-10",
#  "created_at_epoch": ..., "expires_at_epoch": ..., "status": "active", "current_version": 1,
#  "settlement_tx_id": "..."}
```

Price is `max($0.001, ceil(bytes/1KB) × $0.000001953125 × retention_days/90)`
— 4 KB for 30 days is the $0.001 floor; 10 MB (the cap) for 90 days is
$0.02. Content is opaque to us (never scanned or indexed) but not
confidential from us — encrypt client-side.

**Read it back** (free): mint a single-use challenge, sign it, present the
proof as query params on the very next call:

```bash
curl -s -X POST https://algorand-api.pxke.me/api/v1/x402/storage/auth/challenge \
  -H "Content-Type: application/json" -d '{"wallet": "YOURWALLETADDRESSHERE"}'
# {"nonce": "...", "signing_message": "...", "expires_at_epoch": ..., "proof_methods": ["legacy_message", "signed_bytes"]}

# sign `signing_message` with your wallet key, then within 5 minutes:
curl -s "https://algorand-api.pxke.me/api/v1/x402/storage/backups/<backup_id>?wallet=YOURWALLETADDRESSHERE&nonce=<nonce>&proof_method=signed_bytes&signature_b64=<sig>"
# {...metadata..., "data": "<base64, sha256-verified on the way out>"}
```

The same proof shape (a fresh challenge each time) authenticates
`GET /storage/backups` (list), `GET .../versions`, `GET .../versions/:n` and
`DELETE /storage/backups/:backup_id`. **Add a version** (`POST
.../versions`, paid, same price shape) keeps the old ones, each on its own
expiry; **renew** (`POST .../renew`, paid, priced from the stored size)
extends the current version by one more 90-day term, capped at 90 days
remaining. Both are owner-only — a payment from another wallet settles and
is refused (§4).

## Where to go next

- Full route-by-route reference (every field, every error code, rate
  limits): [`x402-marketplace-api.md`](x402-marketplace-api.md).
- Client-side encryption recipe for storage:
  [`x402-storage-encryption-guide.md`](x402-storage-encryption-guide.md).
- Payment-construction mechanics in Python:
  [`skills/algorand-x402-python/`](../skills/algorand-x402-python/SKILL.md),
  or the ready-made SDK in `x402-client/`.
- Facilitator internals, CAIP-2 ids, the challenge tag, and known gotchas
  in the installed `x402-avm` package: [`x402-facilitator.md`](x402-facilitator.md).

## Live links

This marketplace:

- Catalog, JSON: <https://algorand-api.pxke.me/api/v1/x402>
- Same document at the well-known URI: <https://algorand-api.pxke.me/.well-known/x402>
- OpenAPI 3.1: <https://algorand-api.pxke.me/openapi.json>
- Free smoke test of the cheapest paid route, unpaid and redacted: <https://algorand-api.pxke.me/api/v1/x402/news/search?q=algorand&preview=true>
- Proof-of-volume feed (real settlements, operator traffic excluded): <https://algorand-api.pxke.me/api/v1/x402/settlements>
- Human-readable storefront: <https://x402.pxke.me>

Settlement and discovery (GoPlausible facilitator, no auth needed for reads):

- Facilitator root / endpoint index: <https://facilitator.goplausible.xyz/>
- Facilitator OpenAPI: <https://facilitator.goplausible.xyz/docs/openapi.json>
- Public dashboard: <https://facilitator.goplausible.xyz/dashboard> and the
  per-resource leaderboard <https://facilitator.goplausible.xyz/dashboard/leaderboards?cat=resources>
- This marketplace's merchant roll-up (keyed on our `payTo`): <https://facilitator.goplausible.xyz/data/merchants/3e5946af2c9756b6>
- Bazaar resource catalog (where our routes are discoverable after a settlement): <https://facilitator.goplausible.xyz/discovery/resources>

Protocol and ecosystem:

- x402 protocol home: <https://www.x402.org/>
- Reference implementation: <https://github.com/coinbase/x402>
- Algorand x402 developer guide: <https://algorand.co/agentic-commerce/x402/developers>
- `algorandfoundation/x402-demo`: <https://github.com/algorandfoundation/x402-demo>
- The `x402-avm` package this backend runs on: <https://pypi.org/project/x402-avm/>
- Algorand Global x402 Challenge: <https://algorand.co/global-x402-challenge>
  and the submission guide <https://algorand.co/blog/the-x402-global-challenge-is-live-how-to-build-submit-your-entry>
- USDC on Algorand mainnet (ASA 31566704): <https://allo.info/asset/31566704>
