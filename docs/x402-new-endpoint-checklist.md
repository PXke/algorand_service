# Checklist: after adding a new x402 endpoint

Every product this session that shipped without following this exact list went
through the same failure once already: fully built, fully working, and
**invisible** — `x402_social` and `x402_scan` were both live and functional
for a real stretch of time before anyone noticed they were missing from the
catalog/`.well-known`/`openapi.json` roster. This checklist exists so that
doesn't happen a third time.

Do these in order. Skipping a step is exactly how the previous two incidents
happened.

## 1. Register the route in the catalog

File: `backend/app/modules/x402_catalog/services/catalog.py`

Add a `CatalogRoute` entry to the relevant `Product` in the `PRODUCTS` tuple
(or a new `Product` if this is a genuinely new product family, not an
addition to an existing one). This one file is what generates:

- `GET /api/v1/x402` (our own machine-readable catalog)
- `GET /.well-known/x402` (the standard discovery manifest)
- `openapi.json`

— so registering it here is what makes it show up in **all three** at once.
Forgetting this step is exactly what happened to `x402_social` and
`x402_scan`: the route worked, took real payments, and was completely absent
from every discovery surface simultaneously.

Required fields: `method`, `path`, `description` (a real, specific
description — see any existing entry for the expected voice), `price_setting`
(the name of the setting in `config.py`, never a literal price string —
config owns prices, see step 2), `resource` (the short id used in the
settlement ledger), `input_example`. Optional: `supports_promo`,
`supports_preview`, `supports_receipts` — set these to match what the route
actually implements, not by default/copy-paste (CLAUDE.md's own test suite,
`test_every_paid_route_supports_promo_except_the_unwired_ones`, enforces this
is deliberate, not accidental).

## 2. Own the price/settings in config.py, never inline

File: `backend/app/core/config.py`

Every price and every tunable (max results, rate limits, TTLs) gets a named
setting here, read from there by both the route and the catalog entry.
CLAUDE.md §3: "Config has one owner." Never a raw `os.getenv` in the route
module itself.

## 3. Wire the route's own Bazaar discovery extension

File: the route handler itself (`api/routes.py` in the new module), pattern
lives in `backend/app/modules/x402_news/api/routes.py` if you need a live
reference.

Call `describe_json_endpoint(...)` (from `app.modules.x402.discovery`) inside
the route's `require_paid_request(..., extensions=describe_json_endpoint(...))`
call, with a real `input_example` and, where relevant, an `output_example`.
This is what actually gets the route **cataloged in GoPlausible's Bazaar** —
but only conditionally, see step 4, which is the newest-discovered gap in
this exact mechanism.

## 4. Make sure the 402 offer's `resource.url` is an absolute public URL

File: `backend/app/modules/x402/guard.py` (`_resource_url`, and
`resource_path=` for any route with a path parameter).

This is the step most likely to silently half-work: **the Bazaar only
catalogs a route from the 402 offer's `resource.url`, and only if that URL is
an absolute public URL** (confirmed live, see `docs/x402-facilitator.md`). A
route whose offer only carries a bare short id (e.g. `x402-directory-list`)
still settles fine and still counts on the leaderboard — it just never gets a
Bazaar catalog entry, with no error or warning anywhere to tell you that
happened. If the new route has a path parameter, pass `resource_path=` with
the *template* (e.g. `/api/v1/x402/features/{request_id}/vote`), not one URL
per possible parameter value.

**Bazaar cataloging also only happens after a real settlement** on that
resource — a correctly-formed offer with zero payments yet will correctly
show nothing in `GET https://facilitator.goplausible.xyz/discovery/resources`
until someone actually pays it once. Don't mistake "no settlement yet" for "still
broken" — check the settlements ledger
(`GET /api/v1/x402/settlements/recent`) before concluding the wiring is wrong.

## 5. Multi-asset `accepts`, even if only USDC is enabled

CLAUDE.md §9: "Multi-asset `accepts` in the 402 offer from day one, even if
only USDC is enabled initially." Confirm the new route's payment gate builds
its `accepts` array the same way every existing route does — don't hand-roll
a USDC-only offer for a new module just because that's what launches first.

## 6. Auto-refund + circuit breaker

Every paid route in this codebase went through a retrofit
(`Auto-refund + circuit breaker for x402 own-endpoint failures`, then
`Retrofit ... into all remaining paid routes`) to guarantee: a payment that
was taken but whose product work then failed gets refunded automatically, and
a route with an elevated failure rate trips a circuit breaker rather than
silently eating payments for a broken product. Wire the new route through the
same `run_with_refund`/`circuit_breaker.is_tripped` pattern every other route
uses — don't ship a paid route without this.

## 7. Frontend: does the new route need to appear on the marketplace page?

If the new product has a human-facing surface (most don't need one — agents
are the primary consumer — but some do, e.g. the directory/board), check
`frontend/src/routes/X402.svelte` and `frontend/src/lib/api/x402.ts` for
whether the new endpoint family needs a UI entry point. Don't assume it's
automatically picked up — `x402.ts`'s endpoint map is a hand-maintained list,
not derived from the live catalog.

## 8. Tests, then verify per CLAUDE.md §6

```
cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check <changed files> && .venv/bin/pytest -q
```

At minimum, extend `tests/test_x402_catalog.py` — it already has the tests
that would have caught both prior incidents
(`test_every_registered_x402_route_is_listed`,
`test_every_paid_route_supports_promo_except_the_unwired_ones`) — make sure
the new route is covered by both, not just added to `PRODUCTS` and left
implicitly untested.

## 9. Live verification after deploy — do not skip this even if tests pass

Tests catch "is this route registered in our own code." They cannot catch
"does the live facilitator actually see it." After deploying:

```bash
# Confirm it's in our own catalog/well-known/openapi
curl -s https://algorand-api.pxke.me/api/v1/x402 | grep -c '"path".*new-route-path'
curl -s https://algorand-api.pxke.me/.well-known/x402 | grep -c 'new-route-path'

# Confirm the 402 offer itself carries an absolute resource.url
curl -s https://algorand-api.pxke.me/api/v1/x402/new-route-path | python3 -m json.tool
# -> check the "accepts"/"resource" field for a real https://algorand-api.pxke.me/... URL,
#    not a bare short id

# After at least one real settlement on it, confirm the Bazaar picked it up
# (may take a beat after the settlement, not instant):
curl -s "https://facilitator.goplausible.xyz/discovery/resources" | grep -c 'new-route-path'
```

If the last check comes back empty even with a confirmed settlement on a
URL-bearing offer, that's a real bug worth re-opening this checklist for —
don't assume it will just eventually appear.

---

## The two prior incidents this checklist exists because of

- **`x402_social`**: fully built, Bazaar-registered in intent, but absent from
  the catalog/`.well-known`/`openapi.json` roster — fixed in
  `Fix x402_social catalog/.well-known/openapi discoverability gap`.
- **`x402_scan`**: same shape of bug, same fix pattern — see
  `Fix x402_scan catalog/discovery registration gap`.

Both were caught by a human noticing "agents aren't finding this," not by any
automated check that existed at the time. Steps 8-9 above are the automated
and manual checks that now exist specifically to catch this class of bug
before a third recurrence.
