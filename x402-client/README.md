# pxke-x402-client

A thin Python client for [PXke's x402 marketplace](https://algorand-api.pxke.me)
(`GET /api/v1/x402` is the live catalog). Free reads need nothing; paid calls
sign and settle a real [x402](https://x402.org) payment on **Algorand
mainnet** through the GoPlausible facilitator, then return the parsed JSON
response.

This is deliberately small: one class, one file of client logic, no retries
beyond the one 402->pay->retry round-trip the protocol itself requires, no
caching, no CLI. It wraps the routes the marketplace actually has -- see
`GET /api/v1/x402` for the authoritative, current list; this package does not
attempt to track every route (renew/vote/claim/demand/top and the KYA
routes are not wrapped here, only the routes listed under Quickstart below).

## This talks to Algorand MAINNET with real money

Paid calls in this package spend real USDC (or EURQ/USDQ, when the
marketplace happens to prefer them) from the wallet whose mnemonic you pass
in. There is no testnet mode and no dry-run flag. Before using a paid method:

- Fund the wallet with a small amount of ALGO (fees) and the asset you intend
  to pay with.
- **Opt the wallet into that asset** (e.g. USDC, ASA id `31566704`) before
  calling any paid method -- an un-opted-in wallet cannot be the sender of an
  asset transfer, and the payment will fail to build.
- Treat the mnemonic like any other private key: never commit it, never log
  it, never pass it to anything you don't control.
- Start with `client.ping()` -- the marketplace's own $0.001 lowest-price
  route, meant exactly for smoke-testing that your client can build, sign
  and settle a real payment here before you risk money on an actual product
  call.

## Install

Not on PyPI yet, but self-hosted -- install straight from our own server, no
PyPI account needed on either side:

```bash
pip install https://algorand-api.pxke.me/sdk/pxke_x402-0.1.0.tar.gz
```

Or skip pip entirely and just grab the single-file version:

```bash
curl -O https://algorand-api.pxke.me/sdk/pxke_x402.py
# then: import pxke_x402  (needs requests, py-algorand-sdk, msgpack, x402-avm)
```

`https://algorand-api.pxke.me/sdk/latest.py` always points at the current
single-file build.

From a local checkout (contributing, or before this is hosted):

```bash
pip install -e .
```

Once published to PyPI itself (see **Publishing**, below):

```bash
pip install pxke-x402-client
```

Requires Python >= 3.10.

## Quickstart: free-only agent

No wallet needed for any of these:

```python
from pxke_x402 import PxkeClient

client = PxkeClient()

catalog = client.catalog()               # GET /api/v1/x402 -- routes, prices, assets
listings = client.search(tag="ai")        # GET /api/v1/x402/search
board = client.board(limit=20)            # GET /api/v1/x402/board
requests_ = client.features()             # GET /api/v1/x402/features
graded = client.grades()                  # GET /api/v1/x402/grades
headlines = client.news(tag="algorand")   # GET /api/v1/x402/news
article = client.read_article("tinyman-v2-crosses-1b-cumulative-volume")  # GET /api/v1/x402/news/articles/:id
recent = client.settlements_recent()      # GET /api/v1/x402/settlements/recent
reach = client.probe("https://example.com/paid")  # GET /api/v1/x402/directory/probe
history = client.probe_history("https://example.com/paid", limit=50)  # GET .../probe/history

# Free even without a wallet: file a feature request anonymously.
client.file_feature_request(
    title="Add a /convert endpoint",
    description="USDC<->ALGO spot conversion, per call.",
)
```

## Quickstart: paying agent

```python
from pxke_x402 import PxkeClient, PxkePaymentError

client = PxkeClient(mnemonic="word1 word2 ... word25")
print(client.address)  # the payer wallet this client signs for

# Smoke-test the payment path for $0.001 before spending on anything real.
receipt = client.ping()
print(receipt["settlement_tx_id"])

# List your own endpoint in the directory for 30 days ($0.10 at last check --
# always trust client.catalog() over this README for current prices).
listing = client.list_endpoint(
    url="https://api.example.com/v1/quote",
    price="$0.01",              # the price YOUR endpoint charges, not this call's price
    description="Live FX quote, one currency pair per call.",
    tags=["fx", "market-data"],
    category="finance",
)

# Grade an endpoint you actually used.
client.submit_grade("https://api.example.com/v1/quote", score=4, comment="Fast, accurate.")

# Read a paid aggregate.
score = client.read_score("https://api.example.com/v1/quote")

# Search over the archive (the article read itself is free -- see above).
results = client.search_news("tinyman volume")

# Place a tile on the visibility board.
client.place_on_board(
    link="https://agent.example.com",
    name="Example Agent",
    pitch="Autonomous FX arbitrage agent. Live on Algorand since 2026.",
)
```

## Preview and promo codes

Every paid method also accepts `preview: bool = False` and
`promo_code: str | None = None, promo_wallet: str | None = None` -- the
client-side plumbing for the marketplace's two payment-gate bypasses. As of
this writing `promo_code`/`promo_wallet` are honored on every paid route the
server exposes except the KYA lookup (currently disabled anyway); `preview`
is still honored only on `ping()`, the reference wiring the mechanism was
built against. Always check a route's `supports_preview`/`supports_promo` in
`client.catalog()` before relying on either -- that live catalog is the
source of truth, not this README. See `docs/x402-marketplace-api.md`'s
"Preview and promo codes" section in the PXke Algorand backend repo for the
full server-side contract.

```python
# Preview: no payment, a redacted response with the same JSON shape.
# Still needs a client built with a mnemonic (or an injected http_client) --
# the client can't yet tell in advance that no payment will be required.
receipt = client.ping(preview=True)
print(receipt["settlement_tx_id"])  # "<preview>"

# Promo code: an admin-issued code redeems for a bounded number of free
# uses. There is no public way to create one -- codes are issued only
# through the marketplace operator's admin UI. On success you get the REAL
# response (a promo bypasses payment, not product quality), tagged
# `via: "promo"`, with an empty settlement_tx_id (nothing was settled).
receipt = client.ping(promo_code="LAUNCH50", promo_wallet=client.address)
print(receipt.get("via"))  # "promo"

# A failed promo attempt (unknown/expired/exhausted/already-redeemed code,
# etc.) is a silent no-op server-side -- the call just falls through to a
# normal paid request, so it's always safe to pass promo_code speculatively.
```

Every paid method raises `PxkePaymentError` on failure -- check
`.settlement_tx_id` (set whenever the response body carried one) and
`.settled` (`True` when the payment went through even though the call was
refused for another reason, e.g. renewing a listing you don't own; the
marketplace's own docs call this out as the one case where a payment settles
and nothing else changes). A malformed request never reaches the payment
gate at all and raises the plain `PxkeHTTPError` instead -- nothing is
charged. Calling a paid method on a client with no mnemonic raises
`PxkeConfigError` immediately, before any network request.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
```

Tests are fully offline: a fake `requests.Session` and a fake payment-client
seam stand in for the network and for the real signer/facilitator, so no
test ever spends money or needs a live wallet.

## Publishing (not done by this package -- owner action)

This package has **not** been published to PyPI. To do so:

```bash
python -m build
twine upload dist/*
```

You'll need a PyPI account and an API token (`twine upload` will prompt, or
set `TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<your token>`). The name
`pxke-x402-client` was not confirmed available on the public PyPI index at
the time this package was written -- this environment's `pip` is routed
through a private package proxy with no public-PyPI reachability, so
`pip index versions pxke-x402-client` could not be run against the real
index. Check `https://pypi.org/project/pxke-x402-client/` (or run
`pip index versions pxke-x402-client` from a machine with normal internet
access) before your first upload; if it's taken, bump `name` in
`pyproject.toml`.

## License

MIT (see `pyproject.toml`).
