# x402 uptime/reachability check — design

Date: 2026-09-04. Roadmap item not numbered in CLAUDE.md §9.1 — new product,
owner's own framing: *"A simple endpoint that sends a URL/IP and we return if
the site is down from the point of view of our servers. 0.001 cts. We can
give things like time to answer and maybe the curl trace or extra
information. A rate limiting for a given IP/domain is important, we can
cache. Idea is to not be used for DDoS."*

Module: `backend/app/modules/x402_uptime/`. Resource id `x402-uptime-check`,
route `POST /api/v1/x402/uptime/check`.

## Why the DDoS/SSRF framing drives every decision below

This is a paid endpoint that makes **our** server fetch a URL **the caller
chose**, on demand. That shape is a textbook SSRF-as-a-service /
DDoS-reflector primitive if built carelessly — a caller could pay a fraction
of a cent to make our infra hammer a third party, probe our own internal
network, or amplify traffic against a victim who never opted into any of
this. The owner named this risk explicitly; it is the binding constraint,
not a nice-to-have. Every design choice below traces back to one of:

1. Never let the target control what we do (no forwarded headers, no
   non-GET, no body relay, bounded time/bytes).
2. Never let a caller reach where we can reach but the public can't (SSRF
   guard, reused, not reinvented).
3. Never let unlimited callers turn into unlimited traffic against one
   target, regardless of how many different IPs/wallets they use (the
   per-target limiter + cache is the actual DDoS defense, not just a cost
   optimization).

## What already exists and is reused, not duplicated

- **SSRF-safe resolve-then-connect**: `backend/app/modules/media/api/routes.py`'s
  `_resolve_public_ip` (already reused once, by
  `x402_scan/services/scan_service.py`, with a comment flagging "promoting
  this to a shared SSRF-fetch module is the flagged follow-up once a second
  real consumer exists" — this module is that second, real consumer). This
  build imports `_resolve_public_ip` directly from `media.api.routes`,
  exactly the same shortcut `x402_scan` already took, rather than doing the
  promotion now: CLAUDE.md section 1 rules out drive-by refactors outside
  the files a task actually requires, and this task's scope is a new,
  self-contained module, not a `media`/`x402_scan` refactor. The promotion
  is now flagged by a *third* consumer wanting it (see Observed, not fixed
  in the final report) rather than done unilaterally here. Rejects any host
  that resolves to a private/loopback/link-local/reserved/multicast/
  non-global address, and connects to the IP it already validated (never a
  second, unvalidated DNS lookup at connect time — closes the DNS-rebinding
  TOCTOU window the same way `workers/app/core/net_guard.py` does).
- **URL normalization**: `x402_directory/services/listing_service.normalize_url`
  (lowercases scheme/host, drops the fragment, validates scheme ∈
  {http,https} and a non-empty host, raises `DirectoryError` — a
  `PlatformError` subclass — on anything else). Reused directly rather than
  re-implementing the same 10 lines with a second, possibly-diverging
  definition of "the same URL" — CLAUDE.md forbids a new copy of existing
  logic, and a diverging normalizer here would be a real bug class (two
  different cache/rate-limit keys for what a human considers "the same
  site").
- **Redis counter primitive**: `app/core/rate_limit.incr_with_expiry`, the
  same one `x402_scan`, `img_proxy`, `algod_proxy` use.
- **Payment gate / replay / settlement / refund / circuit breaker**:
  `app/modules/x402/paid_request.require_paid_request` +
  `run_with_refund` + `app/modules/x402/circuit_breaker`, same as every
  other paid route.
- **Catalog registration pattern**: `Product`/`CatalogRoute` in
  `x402_catalog/services/catalog.py`, `bool_setting`-gated exactly like
  `x402_scan` (`x402_uptime_enabled`, default `False` — see "what ships
  disabled" below). CLAUDE.md §9 explicitly calls out two prior incidents
  where a product was registered in `falcon_main.py` and reachable but
  never added to `PRODUCTS`, so it never appeared in the catalog or
  `/.well-known/x402`; this build adds the `PRODUCTS` entry in the same
  commit as the route registration, not as a follow-up.
- **What is deliberately NOT reused**: `x402_scan`'s own fetch
  (`_fetch_bounded_to_disk`) streams the full body to disk — wrong shape
  here, this product never needs the body (see "no body download" below).
  `x402_directory`'s probe (`workers/app/modules/x402_probe/probe.py`) is
  close in spirit but is scheduled, workers-side, and only ever probes
  **listed directory entries** on a fixed schedule — never an arbitrary
  caller-supplied URL on demand. Its "never raises for a per-URL failure,
  always returns a result" contract is the one piece of *philosophy* this
  design explicitly copies (see "result shape" below), not its code — it's
  a different process (workers, not backend) with a different trigger model
  (beat, not HTTP request) and reuse would mean the backend importing from
  `workers/`, which nothing else in this codebase does.

## Request / response shape

```
POST /api/v1/x402/uptime/check
{"url": "https://example.com"}
```

A caller-supplied literal IP is expressed as the URL's host component
(`https://93.184.216.34/`) — same convention `x402_scan` already uses for
"URL or IP" in its own input; there is no separate schemeless-bare-host
input mode. This is a deliberate simplification, not an open question: it
keeps exactly one parsing path (reuse `normalize_url`, which already
requires a scheme) instead of a second bespoke "is this a bare IP" branch.

```json
{
  "target_url": "https://example.com/",
  "final_url": "https://example.com/",
  "checked_at": "2026-09-04T12:00:00Z",
  "cache": "hit",
  "cached_at": "2026-09-04T11:58:30Z",
  "reachable": true,
  "http_status": 200,
  "response_time_ms": 143,
  "redirect_chain": ["https://example.com/"],
  "resolved_ip": "93.184.216.34",
  "error": ""
}
```

Fields:

- `cache`: `"hit"` (fresh, served from Redis, no outbound request made this
  call), `"miss"` (a real fetch just ran), or `"stale"` (the per-target
  budget was exhausted this hour so a real fetch was skipped and an
  older-than-TTL cached result was served instead — see "per-target rate
  limit" below for why this exists and is preferable to a 429).
- `reachable`: `true` once we got *any* HTTP response (including a 4xx/5xx
  from the target — that's still "up", just erroring); `false` for
  connection-level failure (DNS, refused, timeout, TLS, non-public target,
  too-many-redirects).
- `http_status`: the target's status code, or `0` if we never got one.
- `response_time_ms`: wall-clock time for the whole check (connect through
  first byte of the final hop's status line — see "no body download"), a
  single number. This is "time to answer," the owner's own phrase.
- `redirect_chain`: the sequence of **target-supplied** URLs the check
  followed (each hop's `Location`), capped at `x402_uptime_max_redirects`
  (3). Never includes anything about *our* resolution of any hop (see next
  section for what's deliberately excluded).
- `resolved_ip`: the IP the *final* hop actually connected to. This is
  information about the target, not about us — safe to return, and useful
  (confirms the check reached the address the caller expects, catches a
  surprise CDN/DNS result).
- `error`: `""` on a normal reachable check, else one of
  `dns_failure` / `connection_refused` / `timeout` / `tls_error` /
  `non_public_target` / `too_many_redirects` / `http_error`
  (a non-exception transport error `httpx` can't otherwise classify).

### What "extra information" is deliberately NOT returned, and why

The owner's own ask — "maybe the curl trace" — is not built as asked,
because a literal trace is a fingerprinting and self-exposure vector, not
because tracing itself is bad:

- **No raw request/response headers are echoed** (ours or the target's).
  Ours would tell every payer exactly what our outbound fetcher looks like
  (User-Agent, header order, TLS client fingerprint) — free reconnaissance
  for building something that specifically evades this checker. The
  target's headers are technically "safe" (already public — anyone can curl
  the same URL) but are left out of v1 anyway to keep the response surface
  minimal and reviewable; this is the one place in this design that is a
  clean, low-risk v2 addition rather than a security decision, so it isn't
  gated behind an open question.
- **No internal timing breakdown** (DNS-resolve-ms vs connect-ms vs
  TLS-handshake-ms vs TTFB-ms individually) — a multi-stage timing profile
  is exactly the kind of thing a `curl -w` trace gives you, and while it's
  arguably about the target's latency, splitting it finely enough to be
  useful also reveals our own network path's shape (which hop is slow: us
  or them). One aggregate `response_time_ms` gives the owner's literal ask
  ("time to answer") without the fingerprinting surface.
- **No proxying of caller-supplied headers to the target, ever.** The
  request body accepts exactly one field (`url`). There is no way for a
  caller to inject a header, a cookie, or a body into the outbound request —
  closes the "use us to relay a crafted request at a third party" shape of
  abuse, independent of the rate limits below.
- **Only GET, never forwarded/caller-chosen.** No PUT/POST/DELETE/PATCH
  option — this product answers "is it up," not "proxy my write."

## No body download

Unlike `x402_scan` (which needs the file's bytes) or the media proxy (which
needs the image's bytes), an uptime check needs only the status line and
timing — the response body is read for **zero bytes** past what the
transport needs to read headers (`httpx.Client.stream()`, close the
response immediately after `response.headers` is available, before calling
`iter_bytes()` at all). Two direct consequences:

- **Materially less DDoS amplification per check than any other module in
  this codebase that fetches a caller-supplied URL** — we never pull a
  multi-MB body off the target, so even the deliberately-worst-case (the
  per-target rate limit's ceiling, see below) moves far less data against
  any one target than `x402_scan`'s up-to-1GB-per-call does.
- No byte cap is needed for the body (there is no body read), only a
  per-attempt wall-clock timeout (`x402_uptime_check_timeout_s`, proposed
  5s) covering connect + TLS + first byte of the status line, mirroring
  `workers/app/modules/x402_probe/probe.py`'s own wall-clock deadline
  reasoning (a slow-drip endpoint must not be able to hold a check open
  indefinitely).

## SSRF guard

Every hop (the original URL and every redirect target, up to
`x402_uptime_max_redirects` = 3) goes through the promoted
`app/core/ssrf_guard.resolve_public_ip` before connecting: DNS resolution
happens first, EVERY resolved address for the hostname must be public (not
just the first — closes the round-robin-between-public-and-private DNS
trick), and the actual TCP connect targets the already-validated IP
literal, with the original hostname preserved as the `Host` header and TLS
SNI (so virtual-hosted/cert-checked targets keep working) — byte-for-byte
the same shape `_resolve_public_ip`/`_stream_fetch` already use.

**Billing decision, matching existing precedent exactly**: a target that
resolves to a private/reserved/loopback/link-local/multicast address is
**not** rejected pre-payment with a 400. It is charged normally and
answered with `{"reachable": false, "error": "non_public_target", ...}` —
this mirrors `x402_scan`'s own existing, shipped behavior (`FetchError`
from `_resolve_public_ip` returning `None` is charged, never refunded,
because the caller controls the URL). No connection is ever attempted to
the private address, so there is no oracle for probing internal
infrastructure beyond "yes, that's a private-range IP" — information the
caller already had by typing it in. Keeping this consistent with `x402_scan`
avoids the two products silently disagreeing on the same question.

## Caching

Owner's own ask: "we can cache." One Redis key per normalized URL
(`sha256(normalize_url(url))`, 16 hex chars is enough entropy for a cache
key — full 64 not needed):

- `algorand:x402:uptime:cache:<hash>` — the last result (JSON plus its
  `cached_at`), used for both the fresh-hit path and the stale-fallback path
  (see "per-target rate limit" below). Stored with its own fixed 24h Redis
  TTL (pure storage lifetime, not a freshness signal) so a stale-but-present
  value stays available for the stale-fallback branch long after either
  freshness window below has closed. Freshness itself is judged separately,
  by comparing `cached_at` against the outcome-specific TTL at read time —
  "does this key still exist" and "is it still fresh enough to serve as a
  hit" are deliberately kept as two different questions, not conflated into
  one Redis-native expiry.
- Freshness rule: an `up` result is fresh for `x402_uptime_cache_ttl_up_seconds`
  (proposed 180s / 3min); a `down` result (`reachable: false` OR
  `http_status >= 500`) is fresh for only
  `x402_uptime_cache_ttl_down_seconds` (proposed 30s) — asymmetric on
  purpose, same direction as the media proxy's own asymmetric TTL
  (`_CACHE_TTL = 86400` for a successful image fetch vs. a 1-hour
  `Cache-Control` on its failure placeholder,
  `backend/app/modules/media/api/routes.py:34,304,317`): a negative result
  is trusted for less time than a positive one, so a real recovery becomes
  visible again quickly.

**Named, not resolved, judgment call**: which direction is actually
*safer* to get wrong is a product-trust question the owner hasn't stated
and this doc does not resolve — see Open Questions below. The 180s/30s
numbers above are a reasoned default (6:1 ratio, same shape as media's
asymmetry, both windows far short of media's 24h/1h because "is it up
right now" has a much shorter honesty window than "what does this image
look like"), not a confirmed one.

## Rate limiting — two independent dimensions, both required

The owner named both explicitly ("a given IP/domain"): per-caller-IP alone
does not stop a determined caller from using many IPs/wallets to still
flood one victim, and per-target alone does not stop one IP from being a
general nuisance across many different low-traffic targets. Both are
implemented with the existing `incr_with_expiry` primitive, fail **open** on
a Redis error for the IP limiter (CLAUDE.md invariant 9 default — matches
`x402_scan_rate_limit_per_hour`'s own fail-open choice for the identical
reason: "one Redis blip must not crash a beat/endpoint").

- **Per-caller-IP** (`x402_uptime_rate_limit_per_hour`, proposed 120/hour):
  covers the free pre-payment surface (URL-syntax validation, the 402 offer
  lookup) the same way `x402_scan_rate_limit_per_hour` covers scan's —
  "CLAUDE.md section 9 requires every free endpoint rate limited per wallet
  and per IP... a caller can trigger a 402 offer lookup and a 400 without
  ever paying." 120/hour matches the majority convention in this codebase
  (`x402_news`/`x402_board`/`x402_features`/`x402_grading`/`x402_catalog`
  all use 120/hour) rather than `x402_scan`'s tighter 30/hour, because this
  endpoint's pre-payment validation work is cheap (URL parsing only, no
  sandbox/container spin-up) — 30/hour was sized for scan's much heavier
  per-attempt cost, not a general "SSRF-adjacent" tax.
- **Per-target** (`x402_uptime_target_rate_limit_per_hour`, proposed 20/hour
  = roughly one real outbound check every 3 minutes), keyed on the
  normalized `host[:port]`, counting only **real fetches** (cache hits never
  increment it) — **this is the actual DDoS defense**, not the IP limiter.
  It bounds worst-case outbound traffic against any single target to a
  small, fixed number **regardless of how many different payer IPs or
  wallets are involved**, which the per-IP limiter alone cannot do. This has
  no existing precedent in the codebase (no other module rate-limits by
  "the caller-chosen destination" rather than "the caller") — the mechanism
  (a second `incr_with_expiry` key) is not novel, but the number is a fresh
  judgment call, flagged below.

### What happens when the per-target budget is exhausted

Not a bare 429 — a caller who paid deserves an answer, and a 429 after
payment would need a refund path anyway. Instead:

1. A fresh cache hit never touches the per-target counter at all (this is
   the main reason caching and the per-target limit compose well: a
   popular, stable target converges to "almost every check is a cache hit,"
   and the limiter only ever bites during genuine instability/flapping,
   which is also exactly when a fresh check is most valuable — see Open
   Questions for the tension this creates).
2. Cache miss, budget available → real fetch, counter incremented, fresh
   result cached and returned (`"cache": "miss"`).
3. Cache miss (expired), budget exhausted, but *some* prior result exists
   (even long expired) → that prior result is returned verbatim with
   `"cache": "stale"` and its true `cached_at`, so the caller can judge
   staleness themselves. Still a normal, charged, non-refunded outcome —
   an honestly-labeled stale answer is still worth something.
4. Cache miss, budget exhausted, **no prior result exists at all** (only
   plausible for a brand-new target hit by a burst of first-time callers
   within the same hour) → this is treated as OUR capacity failure, not the
   caller's fault: it raises a plain (non-`PlatformError`) exception from
   the product write, which `run_with_refund` catches, refunds in full, and
   returns `503 product_failed_refunded` — the same shape `x402_scan` uses
   for its own `ConcurrencyLimitError`. `circuit_breaker.is_tripped(resource)`
   is checked before `require_paid_request`, same as scan, so a resource
   tripping this repeatedly (e.g. a coordinated burst against many
   brand-new targets) gets refused before further money is ever at risk.

Every *target-side* outcome (down, timeout, DNS failure, non-public,
4xx/5xx, too-many-redirects) is a **normal return value**, never an
exception — mirroring `workers/app/modules/x402_probe/probe.py`'s
`probe_url`'s own contract ("never raises for a per-URL failure"). This is
the one deliberate divergence from `x402_scan`'s own precedent: scan treats
"the target couldn't be fetched" as `FetchError` (charged, not refunded,
but still routed through the same failure-shaped code path as a real
platform error). Here "the target is down" **is the product** — most
requests to `x402-scan-url` succeed-with-content, whereas a meaningful
fraction of requests to `x402-uptime-check` are *expected* to report "down"
as their entire, fully-paid-for answer. Routing "down" through any
exception path (even a charged, non-refunded one) would make the happy path
and the sad path indistinguishable in the code, which is the wrong shape for
a product whose two possible verdicts are equally the point.

## Pricing

Owner's anchor: **$0.001**. Sanity-checked against this marketplace's
existing lowest tier: `x402_ping_price = "$0.001"` (the deliberately
near-zero connectivity-test price, "the marketplace's lowest price") and
`x402_news_search_price = "$0.001"` (chosen specifically because the
underlying content is already free elsewhere, so search itself should be
"a trivial call," per that setting's own comment in `config.py`). An uptime
check is closer in shape to news-search than to `x402_scan` ($0.01, priced
for a Docker container + up-to-1GB download + ClamAV/YARA pass) or
`x402_board`/`x402_features_demand` ($0.05) — there is no heavy compute or
storage behind it, and (per the caching design above) a meaningful share of
requests cost us **zero** outbound traffic at all. **$0.001 is not
mispriced** against this marketplace's own conventions; it sits exactly
where the cheapest, lightest-weight products already sit.

## Settings (proposed, config.py — one owner, per CLAUDE.md §3)

```python
x402_uptime_enabled: bool = False          # prototype; see "what ships disabled"
x402_uptime_price: str = "$0.001"
x402_uptime_rate_limit_per_hour: int = 120           # per caller IP
x402_uptime_target_rate_limit_per_hour: int = 20     # per target host[:port]
x402_uptime_cache_ttl_up_seconds: int = 180
x402_uptime_cache_ttl_down_seconds: int = 30
x402_uptime_check_timeout_s: float = 5.0
x402_uptime_max_redirects: int = 3
```

## What ships disabled, and why (mirrors `x402_scan_enabled`'s own convention)

`x402_uptime_enabled` defaults to `False`, same as `x402_scan_enabled` did
at prototype stage ("Disabled by default — this is a design prototype, not
a live product; flip on only after the host-isolation decision... is made
explicitly," `config.py:537-538`). The code is complete, tested, and
catalog-registered, but the numeric knobs above (price aside, which is
directly anchored) are reasoned defaults, not owner-confirmed operating
parameters — flipping this on is a deliberate follow-up action, not a side
effect of this build landing.

## Open questions / judgment calls this document does NOT resolve

1. **Exact per-IP and per-target rate-limit numbers.** 120/hour and
   20/hour above are reasoned from precedent (see their sections) but
   invented for the per-target case specifically, since no other module in
   this codebase rate-limits by caller-chosen destination. Needs an owner
   call before going live, especially the per-target number: it directly
   trades off "how fresh can a flapping target's status be for the 2nd,
   3rd, 4th... caller within the window" against "how much traffic can we
   put against one victim regardless of payer count."
2. **The up/down cache-TTL asymmetry direction and magnitude.** This
   document defaults to "trust a down result for less time than an up
   result" (180s/30s), reasoning from the media proxy's own precedent and
   from "a missed recovery feels worse to a monitoring-style caller than a
   missed brand-new outage." But the *opposite* argument is also
   defensible: caching a false "down" for 30s could itself mislead a
   caller who just watched their own site come back up seconds ago and
   paid us specifically to confirm it — and caching a false "up" for 180s
   could hide a real, currently-in-progress outage from someone deciding
   whether to page an on-call. Which failure mode this product should bias
   toward is a stance on what the product is *for* (an incident-response
   tool vs. a lightweight sanity-check) that the owner hasn't stated.
3. **Whether the `"stale"` fallback (item 3 in "what happens when the
   per-target budget is exhausted") is the right trade at all**, versus
   simply refusing with a refunded 503 the way `x402_scan` refuses on
   capacity exhaustion. This document argues an honestly-labeled stale
   answer beats a refusal, but "the caller paid for a *current* answer and
   got an old one, silently substituted" is a legitimate objection this doc
   doesn't have a clean answer to beyond the `"cache": "stale"` +
   `cached_at` transparency.
4. **Whether response headers (or a curated allowlist of them — e.g.
   `Server`, `Content-Type`) from the target should ever be surfaced.** V1
   omits all of them for review-surface minimalism (see "what's deliberately
   NOT returned"), not because a specific header was judged unsafe — a
   concrete allowlist is a reasonable v2 ask but wasn't designed here.
5. **TLS/certificate detail** (issuer, expiry, chain validity) is out of
   scope for v1 entirely — not designed, not flagged with a default, purely
   omitted. A "is this cert about to expire" check is a genuinely different,
   larger feature (needs its own response shape and probably its own
   price) and conflating it with a basic reachability check risks the same
   scope-creep the file-scan module explicitly avoided by staying
   "static analysis only" for its own v0.

## What was built

See the implementation PR/diff for the file list. In short:
`app/modules/x402_uptime/` (services: `checker.py`, `cache.py`,
`rate_limit.py`; `api/routes.py`, importing `_resolve_public_ip` directly
from `media.api.routes`, same as `x402_scan` does), a `schemas.py` request
struct, a `config.py` settings block, `falcon_main.py` registration behind
`x402_uptime_enabled`, and a `PRODUCTS` catalog entry — landing in the same
change as route registration, per the §9.1 note above about the two prior
catalog-omission incidents. No existing file outside this new module was
modified except those four cross-cutting registration points, matching
exactly how `x402_scan` itself was added.
