# pxke-x402-client

A thin Python client for [PXke's x402 marketplace](https://algorand-api.pxke.me)
(`GET /api/v1/x402` is the live catalog). Free reads need nothing; paid calls
sign and settle a real [x402](https://x402.org) payment on **Algorand
mainnet** through the GoPlausible facilitator, then return the parsed JSON
response.

This is deliberately small: one class, one file of client logic, no retries
beyond the one 402->pay->retry round-trip the protocol itself requires, no
caching, no CLI. It wraps the marketplace's three products plus its catalog:

| Product | Method | Route | Paid? |
| --- | --- | --- | --- |
| Catalog | `catalog()` | `GET /api/v1/x402` | free |
| Catalog | `settlements_recent(limit=None)` | `GET /api/v1/x402/settlements/recent` | free |
| News Engine | `news(tag=None, limit=None)` | `GET /api/v1/x402/news` | free |
| News Engine | `read_article(article_id)` | `GET /api/v1/x402/news/articles/:id` | free |
| News Engine | `search_news(q, limit=None, ...)` | `GET /api/v1/x402/news/search` | paid |
| URL scan | `scan_url(url, ...)` | `POST /api/v1/x402/scan/url` | paid |
| Storage | `storage_create_backup(data, label="", ...)` | `POST /api/v1/x402/storage/backups` | paid |
| Storage | `storage_renew_backup(backup_id, wallet, ...)` | `POST /api/v1/x402/storage/backups/:id/renew` | paid |

`GET /api/v1/x402` is the authoritative, current list of routes and prices --
always trust it over this README.

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
- Start with `preview=True` (see **Preview and promo codes**) -- a free,
  unpaid call that returns the same JSON shape with every value redacted,
  so you can check your integration end-to-end before spending anything.
  Then make your first real call against the cheapest route
  (`search_news`, $0.001 at last check) before an actual scan or backup.

Every paid call is also protected client-side before anything is signed: the
402 offer's recipient must match PXke's known `payTo` address
(`expected_pay_to=`) and its amount must stay under `max_payment_atomic=`
($1.00-equivalent by default) -- see `PxkeClient`'s docstring.

## Install

Not on PyPI yet, but self-hosted -- install straight from our own server, no
PyPI account needed on either side:

```bash
pip install https://algorand-api.pxke.me/sdk/latest.tar.gz
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
for route in catalog["routes"]:
    print(route["method"], route["path"], route["paid"], route["price_display"])

headlines = client.news(tag="defi", limit=10)   # GET /api/v1/x402/news
for item in headlines["items"]:
    print(item["published_at_epoch"], item["title"])

# The full article is free too -- the same content as the public website.
article = client.read_article(headlines["items"][0]["slug"])  # GET /api/v1/x402/news/articles/:id
print(article["title"])

recent = client.settlements_recent(limit=20)   # GET /api/v1/x402/settlements/recent
```

## Quickstart: paying agent

```python
from pxke_x402 import PxkeClient, PxkePaymentError

client = PxkeClient(mnemonic="word1 word2 ... word25")
print(client.address)  # the payer wallet this client signs for

# Sandboxed URL scan ($0.01 at last check): the file is fetched server-side,
# never executed, and checked with ClamAV + file-type + entropy + embedded
# URL/IP extraction (+ a bomb-safe member listing for zip/tar).
report = client.scan_url("https://example.com/suspicious-download.zip")
print(report["risk"]["verdict"], report["risk"]["malicious"], report["risk"]["score"])
print(report["settlement_tx_id"])

# Agent backup storage: store an opaque blob, priced per KB from len(data).
# Encrypt it yourself first if it is sensitive -- the server stores it as-is.
backup = client.storage_create_backup(b"...your agent state...", label="nightly")
backup_id = backup["backup_id"]

# Later: extend its retention window (only the wallet that created it may).
client.storage_renew_backup(backup_id, wallet=client.address)

# Ranked full-text search over the news archive ($0.001 at last check; the
# article read itself is free -- see above).
results = client.search_news("tinyman volume", limit=5)
```

Reading, listing, deleting a backup and its per-version routes are free but
wallet-signature-authenticated (`POST /api/v1/x402/storage/auth/challenge`,
then sign the nonce); this thin client does not wrap that flow -- see
**Development**.

## Preview and promo codes

Every paid method also accepts `preview: bool = False` and
`promo_code: str | None = None, promo_wallet: str | None = None` -- the
client-side plumbing for the marketplace's two payment-gate bypasses.
`search_news` and `scan_url` honor `preview` as of this writing; always
check a route's `supports_preview`/`supports_promo` in `client.catalog()`
before relying on either -- that live catalog is the source of truth, not
this README. See `docs/x402-marketplace-api.md`'s "Preview and promo codes"
section in the PXke Algorand backend repo for the full server-side contract.

```python
# Preview: no payment, a redacted response with the same JSON shape. For the
# scan this is a FIXED fake report -- no target is ever fetched or scanned,
# and risk.malicious is None, never a verdict.
# Still needs a client built with a mnemonic (or an injected http_client) --
# the client can't yet tell in advance that no payment will be required.
report = client.scan_url("https://example.com/file.zip", preview=True)
print(report["settlement_tx_id"])  # "<preview>"

# Promo code: an admin-issued code redeems for a bounded number of free
# uses. There is no public way to create one -- codes are issued only
# through the marketplace operator's admin UI. On success you get the REAL
# response (a promo bypasses payment, not product quality), tagged
# `via: "promo"`, with an empty settlement_tx_id (nothing was settled).
results = client.search_news("tinyman", promo_code="LAUNCH50", promo_wallet=client.address)
print(results.get("via"))  # "promo"

# A failed promo attempt (unknown/expired/exhausted/already-redeemed code,
# etc.) is a silent no-op server-side -- the call just falls through to a
# normal paid request, so it's always safe to pass promo_code speculatively.
```

Every paid method raises `PxkePaymentError` on failure -- check
`.settlement_tx_id` (set whenever the response body carried one) and
`.settled` (`True` when the payment went through even though the call was
refused for another reason, e.g. renewing a backup you don't own, or a scan
whose target URL refused the connection -- the caller supplied the URL, so
that fetch failure is charged, not refunded; a sandbox failure on PXke's side
is auto-refunded). A malformed request never reaches the payment gate at all
and raises the plain `PxkeHTTPError` instead -- nothing is charged. Calling a
paid method on a client with no mnemonic raises `PxkeConfigError`
immediately, before any network request.

## Changes

### 0.7.0 -- consolidation to the three products (breaking)

The marketplace was cut down to the products that do real work for a paying
agent (owner decision 2026-09-10). Removed from this client, with their
server routes: `search`, `board`, `features`, `grades`, `probe`,
`probe_history`, `file_feature_request`, `list_endpoint`, `place_on_board`,
`submit_grade`, `read_score`, `ping`, `uptime_check`, and every `social_*`
method. Added: `scan_url`. Fixed: `storage_renew_backup` now sends `wallet`
as the `?wallet=` query param the server actually reads (it was sent in the
JSON body, which the server ignored -- every renewal via 0.6.0 was a 404).

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
python scripts/build_single_file.py > pxke_x402.py   # regenerate the single-file build
```

Tests are fully offline: a fake `requests.Session` and a fake payment-client
seam stand in for the network and for the real signer/facilitator, so no
test ever spends money or needs a live wallet.

Not wrapped yet: the storage module's free `GET`/`DELETE`
`/api/v1/x402/storage/backups[/:id[/versions[/:version]]]` routes need the
wallet-signature challenge flow (`POST .../storage/auth/challenge`, sign the
nonce, present `wallet`/`nonce`/`proof_method`/`signature_b64` as query
params) -- a signing helper this thin client does not implement, so they are
left for a follow-up rather than half-mirrored.

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
