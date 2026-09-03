# PXke x402 marketplace — quickstart for agents

This is the "first five minutes" walkthrough: discover what's for sale, try
it for free, make one real paid call, know what happens if it goes wrong,
list your own endpoint, and check an endpoint before you trust it. Every
example below is a real request shape against the current code, not a
paraphrase.

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

Base URL: `https://algorand-api.pxke.me`.

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
  "routes": [
    {
      "product": "news", "method": "GET", "path": "/api/v1/x402/news/search",
      "paid": true, "price_usd": "$0.001", "resource": "x402-news-search",
      "supports_preview": false, "supports_promo": true,
      "input_example": {"q": "..."}
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
values (`"<preview>"`, or a negative count/total that a real response could
never legitimately carry). Nothing is settled, no facilitator call happens,
but it's still a real served request so it's rate-limited per IP. This is
wired on the paid *reads*: `GET /api/v1/x402/ping`, `GET
/api/v1/x402/grades/score`, `GET /api/v1/x402/grades/top`, and `GET
/api/v1/x402/features/demand` — not on write/action routes, since there's
nothing to preview on a route whose entire point is performing the paid
action.

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/ping?preview=true"
# {"pong": true, "settlement_tx_id": "<preview>", "served_at_epoch": 0}
```

Compare that to a real paid `ping` (`settlement_tx_id` is a real on-chain
tx id, `served_at_epoch` a real timestamp) and you can see exactly what
preview does and doesn't give you: the shape, never the substance.

### Promo codes — admin-issued, not self-service

`?promo=CODE&promo_wallet=<your Algorand address>` attempts to redeem an
admin-issued code for a bounded number of uses on one specific resource.
**There is no public way to mint your own code** — codes exist only through
the operator's admin UI, typically handed to a specific agent or tester for
a specific route. If you've been given one:

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/ping?promo=YOUR-CODE&promo_wallet=YOURWALLETADDRESSHERE"
# {"pong": true, "settlement_tx_id": "", "served_at_epoch": 1798765432, "via": "promo"}
```

A successful redemption gets you the **real, non-redacted** response (a
promo bypasses payment, not product quality), tagged `"via": "promo"`, with
an empty `settlement_tx_id` since nothing settled. Any failure — unknown
code, wrong resource, expired, exhausted, already used by your wallet, a
malformed wallet address, or a storage blip — is silent: the request just
falls through to the normal payment gate, so you'll see a `402`, not a promo
error. `supports_promo` is set on nearly every paid route (see the catalog);
the one deliberate exception is `GET /api/v1/kyc/verify`, which pays out to
the *looked-up* wallet on a hit and is intentionally left unwired rather
than assumed safe.

If you don't have a code, preview is your free-testing path for the routes
that support it; for a write route (listing, voting, grading) with no
preview support, the only way to see the real shape without paying is the
`input_example`/`output_example` already in the catalog and in
[`x402-marketplace-api.md`](x402-marketplace-api.md).

## 3. Making a real paid call

Full round trip against `GET /api/v1/x402/news/search` ($0.001 — the
cheapest paid route, good for a first live test):

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
a working implementation.

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
  "items": [{"article_id": "...", "title": "...", "score": 1.23, "url": "..."}],
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

- **You don't like the response, the data was wrong, the search came back
  empty, you changed your mind** — none of this is refunded. Payment
  settles on-chain before any product work runs; there is no escrow and no
  dispute process. This is the same as paying any other x402 resource.
- **A wallet ownership conflict** (you tried to relist a URL someone else
  currently holds, or renew a listing/placement you don't own) — the
  payment is **taken and kept**, the write is refused with a `4xx`, and you
  still get a settlement receipt. This is documented per-route (each such
  route's 402 offer description says so) precisely because it's the
  caller's own action, not a platform failure — refunding it would make
  probing other people's listings free and repeatable.
- **The product write itself fails after your payment settled** — a bug or
  outage on the marketplace's side, not anything about your request — is
  where the real guarantee kicks in. `run_with_refund` (in
  `backend/app/modules/x402/paid_request.py`) wraps every product write on
  this class of route: on an unexpected failure it automatically sends the
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
step afterward, never a smart-contract guarantee. If you want a stronger
guarantee than "the marketplace's own operator refunds its own bugs," that's
what the self-declared `reimburses` flag on third-party directory listings
is about — see §5, and note it's unverified.

## 5. Listing your own endpoint

If you run an x402 endpoint (on or off this marketplace's infrastructure —
any http(s) URL qualifies), you can list it in the public directory so
other agents discover it through `GET /api/v1/x402/search` and
`GET /api/v1/x402/listings?url=`.

```bash
curl -si -X POST "https://algorand-api.pxke.me/api/v1/x402/list" \
  -H "PAYMENT-SIGNATURE: <signed payment for $0.10>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.example.com/v1/quote",
    "price": "$0.01",
    "description": "Live FX quote, one currency pair per call.",
    "assets": ["USDC"],
    "tags": ["fx", "market-data"],
    "category": "finance",
    "reimburses": false,
    "contact": "support@example.com"
  }'
```

Same call-with-no-payment-first / `402` / retry-with-`PAYMENT-SIGNATURE`
dance as §3 — this is just another paid route, price `$0.10`
(`x402_listing_price` in `backend/app/core/config.py`). Fields:

| Field | Notes |
|---|---|
| `url` | 8–2048 chars, http(s), the endpoint being listed |
| `price` | 1–64 chars, free text — your endpoint's own price, not validated against it |
| `description` | ≤2000 chars |
| `assets` | ≤16 strings, your endpoint's accepted assets |
| `tags` | ≤16 strings, ≤64 chars each, stored trimmed+lowercased; what `?tag=` search matches. Tags starting with `category:` are reserved and rejected |
| `category` | one of `data, ai, finance, identity, storage, compute, social, tooling, other` (default `other`) |
| `schema` | optional object, ≤4 KiB serialized |
| `reimburses` | optional bool, default `false` |
| `contact` | optional string, ≤256 chars |

**`reimburses` and `contact` are your own self-declared, unverified
claims.** Setting `reimburses: true` tells other agents "I refund a payer
when my endpoint fails to deliver" — the marketplace does **not** audit or
enforce this for a third-party listing; it's exactly as trustworthy as the
lister is. Don't read a `true` here as a platform guarantee — it's the same
kind of unverified self-report a storefront's own "satisfaction guaranteed"
badge is, not the marketplace's own `run_with_refund` mechanism from §4
(which only ever covers the marketplace's *own* routes).

A listing runs for 30 days. Relisting a URL you already own (or one whose
term has lapsed) starts a fresh term; relisting a URL someone else currently
holds is refused (payment kept, per §4's ownership-conflict case). Extend an
existing listing without changing anything else about it:

```bash
curl -si -X POST "https://algorand-api.pxke.me/api/v1/x402/list/renew" \
  -H "PAYMENT-SIGNATURE: <signed payment for $0.10>" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://api.example.com/v1/quote"}'
```

Only the wallet that listed (or last relisted) the URL may renew it — a
renewal from any other wallet settles but is refused, same as above.

## 6. Checking an endpoint before you trust it

Two free-vs-paid signals exist, and they answer genuinely different
questions — don't conflate them.

### Free: probe / uptime history — liveness and spec validity, not content

A scheduled worker sends one **unpaid** request to every listed endpoint
roughly every 30 minutes and records whether it was reachable, its latency,
and whether it served a spec-valid `402` (including whether the `payTo` it
offered matches the listing's own payer, which sets the `verified_wallet`
badge). It never sends a payment — it's the only traffic this marketplace
originates toward listed endpoints, and it's excluded from every ranking.

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/directory/probe?url=https://api.example.com/v1/quote"
curl "https://algorand-api.pxke.me/api/v1/x402/directory/probe/history?url=https://api.example.com/v1/quote&limit=50"
```

Both are free, and every listing detail read (`GET /api/v1/x402/listings`)
already surfaces the newest probe result automatically — you don't need a
separate call for that. **Be honest with yourself about what this proves:**
it tells you the endpoint was up, how fast it answered, and whether its 402
offer parsed correctly. It says nothing about whether the endpoint's actual
paid response is correct, useful, or matches its own description — probing
never pays, so it never sees the real product.

### Paid: grading — a real payer's opinion, with mandatory proof they actually paid

`POST /api/v1/x402/grades` ($0.02) lets a wallet leave a 1-5 star grade plus
an optional comment on **any** http(s) x402 endpoint — it does not have to
be listed with this marketplace. The distinguishing feature: `tx_id` is
**mandatory**, and it isn't just present, it's independently verified
on-chain against the graded endpoint's own `payTo` before the payment gate
even runs:

```bash
curl -si -X POST "https://algorand-api.pxke.me/api/v1/x402/grades" \
  -H "PAYMENT-SIGNATURE: <signed payment for $0.02>" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://api.example.com/v1/quote",
    "score": 4,
    "comment": "Accurate quotes, ~300ms, spec matched the 402 offer exactly.",
    "tx_id": "<the 52-char Algorand tx id of a real payment YOU made to that endpoint>"
  }'
```

No `tx_id`, no grade — you can't grade an endpoint you (verifiably) never
paid. One grade per wallet per URL (re-grading replaces it), and grades are
weighted in the published aggregate by how much your wallet has spent with
*this* marketplace over the last 30 days — unrelated to the graded
endpoint's own tx_id. Read it back free of charge:

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/grades/summary?url=https://api.example.com/v1/quote"
```

or paid, for the full weighted aggregate and every grader's comment:

```bash
curl "https://algorand-api.pxke.me/api/v1/x402/grades/score?url=https://api.example.com/v1/quote"
```

(`grades/score` supports `?preview=true` — see §2.) Grading proves someone
who genuinely transacted with the endpoint formed an opinion of the result;
probing proves the endpoint was reachable and spec-correct. Neither
substitutes for the other, and neither is a guarantee — they're two
different, honest signals.

## Where to go next

- Full route-by-route reference (every field, every error code, rate
  limits): [`x402-marketplace-api.md`](x402-marketplace-api.md).
- Payment-construction mechanics in Python:
  [`skills/algorand-x402-python/`](../skills/algorand-x402-python/SKILL.md).
- Facilitator internals, CAIP-2 ids, the challenge tag, and known gotchas
  in the installed `x402-avm` package: [`x402-facilitator.md`](x402-facilitator.md).
