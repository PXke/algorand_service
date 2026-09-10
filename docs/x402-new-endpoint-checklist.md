# Checklist: after adding a new x402 endpoint

Two products in this repo's history shipped fully built, fully working, and
**invisible** — live and taking payments for a real stretch of time before
anyone noticed they were missing from the catalog/`.well-known`/
`openapi.json` roster. This checklist exists so that doesn't happen again.

Scope note (2026-09-10): the marketplace now sells three products of our
own — News Engine (`x402_news`), sandboxed scan (`x402_scan`), backup
storage (`x402_storage`) — and nothing else; the directory, board,
feature-request, grading, probe, uptime, social, KYA and receipt modules
were removed ([ADR-0006](adr/ADR-0006-x402-consolidation.md)). A "new
endpoint" here means a new route on one of those three, or a genuinely new
product of our own behind the shared gate — not a listing of somebody else's
endpoint, which is no longer a thing this marketplace does.

Do these in order. Skipping a step is exactly how the previous incidents
happened.

## 1. Register the route in the catalog

File: `backend/app/modules/x402_catalog/services/catalog.py`

Add a `CatalogRoute` entry to the relevant `Product` in the `PRODUCTS` tuple
(or a new `Product` if this is a genuinely new product family, not an
addition to an existing one — a new `Product` needs `section` (`meta` or
`services`), `summary`, `entry` and `auth` too). This one file is what
generates:

- `GET /api/v1/x402` (our own machine-readable catalog)
- `GET /.well-known/x402` (the discovery manifest peer directories crawl)
- `GET /openapi.json`

— so registering it here is what makes it show up in **all three** at once.
Forgetting this step is exactly what happened before: the route worked, took
real payments, and was completely absent from every discovery surface
simultaneously.

Required fields: `method`, `path`, `description` (a real, specific
description — see any existing entry for the expected voice), `price_setting`
(the name of the setting in `config.py`, never a literal price string —
config owns prices, see step 2), `resource` (the short id used in the
settlement ledger), `input_example`. Optional: `supports_promo`,
`supports_preview`, `price_unit` — set these to match what the route
actually implements, not by default/copy-paste (`tests/test_x402_catalog.py`
enforces that the promo/preview flags are deliberate).

## 2. Own the price/settings in config.py, never inline

File: `backend/app/core/config.py`

Every price and every tunable (max results, rate limits, TTLs) gets a named
setting here, read from there by both the route and the catalog entry.
CLAUDE.md §3: "Config has one owner." Never a raw `os.getenv` in the route
module itself. If the product needs a durable store, its registration is
gated on that store setting not being `"memory"`, and the settlement ledger
itself is gated the same way (`X402_SETTLEMENT_STORE=cassandra` in prod —
the process refuses to start paid routes against an in-memory ledger
outside `APP_ENV=dev`).

## 3. Challenge before parsing, validate before charging

Pattern: `backend/app/modules/x402_news/api/routes.py` (query-param input)
or `x402_scan/api/routes.py` (JSON body).

Call `challenge_if_unpaid(request, **offer)` **before** decoding the body
or query string, so an unpaid request — including the facilitator's own
`verify` and any x402 client's first probe — sees the `402` offer without
having to compose a valid body (found live 2026-09-05: routes that
validated first answered an unpaid probe with a `400` instead of the offer,
and were never catalogued). Then, with a payment header present, validate
everything checkable without knowing the payer **before**
`require_paid_request`, so a doomed request is never charged. Anything that
can only be checked after the payer is known (ownership) settles and is
refused with the payment kept — say so in the offer `description`.

## 4. Wire the route's own Bazaar discovery extension

File: the route handler itself (`api/routes.py` in the module).

Pass `extensions=describe_json_endpoint(...)` (from
`app.modules.x402.discovery`) in the offer, with a real `input`,
`input_schema` and `output_example`. **A POST/PUT/PATCH route must set
`body_type="json"`** even if it takes no body — the query-params extension
only admits GET/HEAD/DELETE, and a body-method route declared without it
fails the facilitator's own validator and is silently never catalogued
(see `docs/x402-facilitator.md`). This is what actually gets the route
**cataloged in GoPlausible's Bazaar** — but only conditionally, see step 5.

## 5. Make sure the 402 offer's `resource.url` is an absolute public URL

File: `backend/app/modules/x402/guard.py` (`_resource_url`, and
`resource_path=` for any route with a path parameter).

This is the step most likely to silently half-work: **the Bazaar only
catalogs a route from the 402 offer's `resource.url`, and only if that URL is
an absolute public URL** (confirmed live, see `docs/x402-facilitator.md`). A
route whose offer only carries a bare short id still settles fine and still
counts on the leaderboard — it just never gets a Bazaar catalog entry, with
no error or warning anywhere to tell you that happened. If the new route has
a path parameter, pass `resource_path=` with the *template* (e.g.
`/api/v1/x402/storage/backups/{backup_id}/renew`), not one URL per possible
parameter value.

**Bazaar cataloging also only happens after the facilitator has run
`verify` on that resource** — in practice, after a real x402 client has
paid it once. A correctly-formed offer with zero payments yet will correctly
show nothing in `GET https://facilitator.goplausible.xyz/discovery/resources`.
Don't mistake "no settlement yet" for "still broken" — check the settlements
feed (`GET /api/v1/x402/settlements`) before concluding the wiring is wrong.
And note the reverse: the facilitator exposes **no unregister endpoint**, so
a route you later remove stays in the Bazaar as a stale entry that 404s.
Don't advertise a URL you are not sure you will keep.

## 6. Multi-asset `accepts`, even if only USDC is enabled

CLAUDE.md §9: "Multi-asset `accepts` in the 402 offer from day one, even if
only USDC is enabled initially." Confirm the new route's payment gate builds
its `accepts` array the same way every existing route does (it does, if you
go through `require_paid_request`) — don't hand-roll a USDC-only offer for
a new module just because that's what launches first.

## 7. Auto-refund + circuit breaker

Every paid route wraps its product write in `run_with_refund` and checks
`circuit_breaker.is_tripped(resource)` **before** the payment gate, to
guarantee: a payment that was taken but whose product work then failed gets
refunded automatically, and a route with an elevated failure rate trips a
circuit breaker rather than silently eating payments for a broken product.
Wire the new route the same way — don't ship a paid route without this.
Decide explicitly which failures are the *caller's* (convert them to a
`PlatformError` inside the product write so they are kept, not refunded —
see `x402_scan`'s `FetchError` handling) and which are ours (let them
propagate, they refund). Anything that can be checked before the gate
(capacity, rate limit, input shape) must be, so the refund path is never
reachable for free.

## 8. Preview and promo — decide, don't inherit

`?preview=true` belongs on a paid **read** (a fixed redacted exemplar, the
real work never run); it does not belong on a write. `?promo=` is safe on
any route whose product write is idempotent per wallet or harmless to give
away in bounded quantity. Set `supports_preview`/`supports_promo` in the
catalog to match what the route actually does, and make the preview
response impossible to mistake for a real one (sentinel values, never
plausible fakes).

## 9. Frontend: does the new route need to appear on the storefront?

The x402 storefront (`frontend/`, `VITE_PRODUCT=marketplace`,
`x402.pxke.me`) has one page per product (`/scan`, `/storage`, `/news`) that
reads prices live from the catalog, plus `/developers`. A new route on an
existing product usually needs a paragraph on that product's page (what it
does, how to call it, what comes back); a new product needs a new page and
a nav entry. Prices are never hand-written in the frontend — read them from
`routes[]`.

## 10. Tests, then verify per CLAUDE.md §6

```
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check <changed files> && .venv/bin/pytest -q
```

At minimum, extend `tests/test_x402_catalog.py` — it already has the tests
that would have caught the prior incidents
(`test_every_registered_x402_route_is_listed`,
`test_every_paid_route_supports_promo_except_the_unwired_ones`) — and
`tests/test_x402_bazaar_extension_sweep.py`, which drives every registrar
unpaid against a stub facilitator and validates the emitted discovery
extension survives the facilitator's own parser. Make sure the new route is
covered by all of them, not just added to `PRODUCTS` and left implicitly
untested.

## 11. Live verification after deploy — do not skip this even if tests pass

Tests catch "is this route registered in our own code." They cannot catch
"does the live facilitator actually see it." After deploying:

```bash
# Confirm it's in our own catalog/well-known/openapi
curl -s https://algorand-api.pxke.me/api/v1/x402 | grep -c '"path".*new-route-path'
curl -s https://algorand-api.pxke.me/.well-known/x402 | grep -c 'new-route-path'

# Confirm the unpaid 402 offer carries an absolute resource.url
curl -si https://algorand-api.pxke.me/api/v1/x402/new-route-path | grep -i payment-required
# -> base64-decode the header; check "resource.url" is a real https://algorand-api.pxke.me/... URL,
#    not a bare short id, and "accepts" lists USDC first with extra.tag set

# After at least one real payment on it, confirm the Bazaar picked it up
# (may take a beat, not instant). Use the BAZAAR merchant id, not the analytics one:
curl -s "https://facilitator.goplausible.xyz/discovery/resources?merchantId=S1NBVk9ZVFZOQjdBNk5LQ000VzJXQk9P" | grep -c 'new-route-path'
```

If the last check comes back empty even with a confirmed settlement on a
URL-bearing offer, that's a real bug worth re-opening this checklist for —
don't assume it will just eventually appear.

---

## The prior incidents this checklist exists because of

- Two products (the since-removed agent social network, and `x402_scan`)
  were each fully built and live but absent from the catalog/`.well-known`/
  `openapi.json` roster, because `PRODUCTS` in `catalog.py` was never
  updated. Both were caught by a human noticing "agents aren't finding
  this," not by any automated check that existed at the time. Steps 10-11
  are the checks that now exist specifically to catch this class of bug.
- Most paid routes validated their request body **before** the payment
  gate, so an unpaid probe got a `400` instead of the `402` offer and was
  never catalogued by the facilitator (found 2026-09-05/06; fixed by the
  shared `challenge_if_unpaid` primitive — step 3).
- A body-method route declared without `body_type="json"` failed the
  facilitator's own extension validator and was silently never catalogued,
  even after real settlements (found 2026-09-05 — step 4).
