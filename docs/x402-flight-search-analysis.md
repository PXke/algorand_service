# x402 flight-search product — research and architecture analysis

Status: **research only, no code written**. Written in response to the owner's
brainstorm for an x402-paid "delegate my flight search to an agent" product
built on SerpApi's Google Flights data. Per instructions this file is left
untracked for review, not committed.

**Bottom line up front: do not build this on SerpApi.** SerpApi's own terms
of service prohibit exactly the reuse this product requires, and — separately
and more seriously — Google is actively suing SerpApi (filed 2025-12-19, N.D.
Cal., case 25-10826) for the same underlying scraping-and-resale business
model, seeking DMCA circumvention damages of $200–$2,500 per act across
"hundreds of millions" of daily queries. This is not a "check with legal
later" flag; it is the same shape of problem that killed the earlier
Brave-Search-resale plan, except with active federal litigation against the
vendor on top of the contractual restriction. See §1.

---

## 1. SerpApi ToS finding (primary finding)

### 1.1 The resale/reproduction clause

SerpApi's Terms of Service (serpapi.com/legal), quoted verbatim:

> "You agree not to reproduce, duplicate, copy, sell, resell or exploit any
> portion of the Service, use of the Service, or access to the Service or any
> contact on the website through which the service is provided, without
> express written permission by us."

This is broader than Brave's clause, not narrower. It prohibits copying,
duplicating, or exploiting **any portion of the Service** without SerpApi's
express written permission — not merely "don't resell our API key" or "don't
white-label our brand." The proposed product's entire mechanism is: pay
SerpApi for a flight search, cache/store the structured result, and sell
processed answers derived from it to paying third parties (other agents).
That is "exploit[ing] a portion of the Service" for resale on its plain
reading. There is no separate commercial-resale tier or resale license
offered in the published pricing plans (Free through Cloud/54M-searches) —
the resale prohibition is not something a higher plan lifts; it requires
individual written permission from SerpApi, which this project does not have
and has not sought.

### 1.2 Caching / retention

SerpApi's own retention policy: **"Search data is retained for 31 days after
the search is completed, after which it expires and is deleted
automatically."** That describes SerpApi's own server-side retention of your
query history for support/billing purposes — it is not a caching *permission*
granted to the customer. Nothing in the published ToS text explicitly says
"you may cache results for N hours/days for your own reuse," and nothing
explicitly forbids transient caching either; the governing constraint is the
broader resale/exploit clause above, which does not carve out an exception
for cached reuse. Given the plain resale prohibition, a "heavy caching, serve
many customers off one paid query" architecture is squarely inside the
prohibited use, independent of whatever the retention-window language means
narrowly.

### 1.3 Downstream-use disclaimer (does not help)

> "SerpApi assumes liability for the lawful collection of public search
> data (scraping, parsing, and related actions), but not for how that data
> is ultimately used."

This is a liability-shifting clause (SerpApi disclaiming responsibility for
what customers do with the data), not a grant of resale rights. It does not
override §1.1's resale prohibition — if anything it confirms SerpApi is
aware customers might misuse results and is deliberately not taking
responsibility for that, which is consistent with resale being against the
rules rather than silently tolerated.

### 1.4 The vendor's own legal exposure (new information beyond ToS text)

Independent of contract terms with SerpApi, Google filed suit against SerpApi
on 2025-12-19 (N.D. Cal., case 25-10826) alleging DMCA § 1201 circumvention
of Google's anti-scraping protections at massive scale (SerpApi's automated
request volume allegedly grew ~25,000% over two years to "hundreds of
millions" of queries/day), specifically because SerpApi "resells" that scraped
data to third parties. Google is seeking statutory damages of $200–$2,500
per circumvention act — a number that at SerpApi's alleged query volume could
theoretically reach far beyond SerpApi's reported annual revenue. SerpApi
has filed a motion to dismiss; the case is unresolved as of this writing.

This matters architecturally even if one were to argue the ToS clause is
negotiable: SerpApi's ability to keep providing this data at all is now
under a live existential legal threat from the data's original source, for
the exact "scrape once, resell to many downstream customers" business model
this product would depend on. Building a paid, revenue-bearing product with
a hard dependency on that vendor means the product's continued existence
would ride on the outcome of someone else's DMCA lawsuit.

### 1.5 Verdict

Do not build this against SerpApi as designed. Two independent reasons stack:
(a) SerpApi's own ToS plainly prohibits reselling/exploiting search results
without express written permission, which this product's whole shape
requires, and (b) SerpApi itself is being sued by Google for enabling
exactly this resale pattern, making it an unstable foundation regardless of
what the contract says. The rest of this document (§2–§5) documents the data
and cost picture *as researched*, and the architecture *as it would look if
built*, so the owner has the technical picture on record — but per the task
instructions, this is not a green light to proceed, and no code should be
written against this assumption.

**Two paths that would avoid the ToS problem, not evaluated in depth here
since they weren't the ask:** (1) an official Google Flights-family API if
one exists with an explicit resale/redistribution license (none is currently
public — Google does not offer a general-purpose commercial flights API to
third parties outside a few program partnerships), or (2) a different data
vendor whose ToS explicitly permits redistribution to the customer's own
paying users (e.g. Kiwi.com/Skyscanner-style travel affiliate/partner APIs,
Amadeus/Duffel/Kiwi Tequila-style GDS-backed flight-search APIs built for
exactly this reseller use case — these were not researched here and would
need the same ToS-first check before any design work).

---

## 2. Data/endpoint findings (SerpApi's Google Flights family, as documented)

Researched from serpapi.com's public documentation pages only; no account
created, no calls made.

### 2.1 `engine=google_flights` (the core search)

- **Parameters**: `departure_id`/`arrival_id` (3-letter IATA or Google's
  location kgmid), `outbound_date`/`return_date` (`YYYY-MM-DD`), `type`
  (1=round-trip, 2=one-way, 3=multi-city, with `multi_city_json` for legs),
  `travel_class`, passenger counts (`adults`/`children`/`infants_in_seat`/
  `infants_on_lap`), `currency`, `deep_search` (boolean — trades latency for
  browser-matching completeness), `sort_by`, `stops`, `include_airlines`/
  `exclude_airlines`, `max_price`, `max_duration`, `outbound_times`/
  `return_times`.
- **Date shape**: takes **one specific outbound date** (and one return date
  for round-trip). It is *not* a native date-range/calendar sweep — one call
  = one departure/return date pair. (Some secondary docs describe a
  comma-separated flexible-window syntax on `outbound_date` for certain
  engines — see google_flights_deals below — but the core `google_flights`
  engine's documented behavior is single-date.)
- **Response**: price, airline, flight number, aircraft, departure/arrival
  times and airports, duration, layovers, travel class, legroom, carbon
  emissions vs. typical, booking token, plus an embedded `price_insights`
  object (below).
- **Pricing**: not stated on the endpoint's own doc page; governed by
  SerpApi's general per-search-credit pricing (§3).

### 2.2 `price_insights` (embedded in the google_flights response — NOT a
separate calendar engine)

```
"price_insights": {
  "lowest_price": <int>,
  "price_level": "<string>",       // e.g. "low"/"typical"/"high"
  "typical_price_range": [<int>, <int>],
  "price_history": [[<timestamp>, <price>], ...]
}
```

This is returned **as part of** a single `google_flights` search — it does
not cost an extra call. Important nuance for the owner's "when could I fly
to minimize price" question: `price_history` reads as a **trend/history**
series for that route (how this route's price has moved over recent time),
not a forward-looking calendar of what every candidate future date currently
costs. It answers "is now a good time relative to history," not "which of
the 31 days in January is cheapest." It does not substitute for a real
date-grid sweep.

### 2.3 `engine=google_flights_deals`

The closest thing SerpApi documents to a genuine flexible-date search:
accepts a **comma-separated date window** on the departure param (e.g.
`2026-09-04,2026-10-03`) or a `travel_duration` preset (weekend/1 week/2
weeks, or a custom `min,max` day-count range), and returns a set of "deals"
with `price`, `average_price`, `discount_percentage` rather than a full
per-day price grid. This is Google's own curated "deals" surface, not an
exhaustive calendar — it will not reliably answer "show me every day in
January and its price," only "here are some good deals in this window."

### 2.4 `engine=google_travel_explore`

The API that maps to Google Flights' "Explore" map (open-destination
search): required `departure_id`, **no destination required**, optional
`arrival_id`/`arrival_area_id` to narrow to a region. Returns a
`destinations` array, each with destination name/location id/airport code,
thumbnail, **starting price**, duration, stops, airline. This is the
mechanism that would answer "where can I travel cheaply" — it is a single
call that fans out server-side (on SerpApi/Google's side) across many
destinations, rather than something the caller has to loop.

### 2.5 Google Hotels

Not researched in depth (out of scope per the owner's example questions,
which are flight-only), but SerpApi documents an analogous `google_hotels`
engine with similar per-search pricing; would need its own ToS-status check
if ever added (same vendor, same resale prohibition applies).

---

## 3. Cost/pricing analysis

### 3.1 SerpApi's published per-search cost

| Plan | $/month | Searches | $/search |
|---|---|---|---|
| Free | $0 | 250 | $0 |
| Starter | $25 | 1,000 | $0.025 |
| Developer | $75 | 5,000 | $0.015 |
| Production | $150 | 15,000 | $0.010 |
| Big Data | $275 | 30,000 | $0.009 |
| Searcher | $725 | 100,000 | $0.007 |
| Volume | $1,475 | 250,000 | $0.006 |
| Infrastructure | $2,750 | 500,000 | $0.006 |
| Cloud (1M+) | from $100,250 | 1M+ | ~$0.002 |

Baseline is **1 search = 1 credit** on the standard plans; SerpApi's own docs
do not clearly state whether `deep_search=true` on `google_flights` consumes
more than one credit (their marketing copy only mentions it costs *latency*,
not credits) — this needs a direct confirmation from SerpApi before any
pricing commitment, since deep_search is the mode that "mirrors your browser"
and is the one likely to be needed for answers a paying agent would trust.
Treat $0.01–$0.025/search as the realistic per-call cost band at any volume
this product would launch at (Developer/Production tier, before proving
enough volume to reach the cheaper tiers) — nowhere near the Cloud tier's
$0.002.

### 3.2 Cost of each example question

- **"I would like to travel in that week, what are the planes"** — one
  ordinary `google_flights` call for a given date (or a handful for a
  ±3-day week). **1 call ≈ $0.01–$0.025.** This is the only one of the three
  that's cheap and native.

- **"When could I fly to minimize the price to go to Egypt in January"** —
  no native single-call answer for a full-month sweep. Options:
  - `google_flights_deals` with a date-window param: **1 call**, but returns
    curated "deals," not a full 31-day grid — may miss the actual minimum if
    the cheapest specific date isn't in Google's "deals" selection.
  - Brute-force `google_flights` once per candidate date: 31 days ×
    (potentially ×2 if also sweeping return date, i.e. a real date-grid) =
    **31–~450 calls** for a genuinely exhaustive January sweep, i.e.
    **$0.31–$11+ in SerpApi cost alone** for one user's one question,
    depending on whether return-date is also swept. Even a coarse
    once-per-week sampling (4–5 candidate dates) is 4-5 calls ≈ $0.05–$0.12.
  - Realistic compromise: use `price_insights.price_history` (free,
    piggybacks on one call) as a *rough* signal plus a small deliberately
    sparse set of candidate dates (e.g. every 3rd day = ~10 calls,
    ≈ $0.10–$0.25) rather than an exhaustive sweep, and be honest in the
    product's own response that it's a sampled estimate, not an exhaustive
    calendar.

- **"Where can I travel cheaply next month"** — the hard one. There is no
  way to ask "cheaply, anywhere" without either (a) `google_travel_explore`
  with no destination filter, which is genuinely **1 call** but returns
  *starting* prices Google's own explore surface picked, for whatever
  candidate destinations Google's explore endpoint decides to return (not
  configurable to "give me 50 destinations," and coverage/completeness is
  entirely dependent on what Google Explore itself shows) — cheapest single
  call, ≈$0.01–$0.025; or (b) a curated candidate-destination list (e.g. 20-
  40 popular destinations from the departure city) each queried individually
  via `google_flights` for a representative date = **20-40 calls per user
  question ≈ $0.20–$1.00**. Real "explore anywhere in the world, next month,
  cheapest" is not affordable per-query without (a).

### 3.3 Does 3 cents/query work?

**No, not uniformly, and this is a real unit-economics problem, not just a
preference.** Compare against this marketplace's existing price points
(`backend/app/modules/x402_catalog/services/catalog.py` `PRODUCTS`, prices
read live from `backend/app/core/config.py`):

| Route | Price |
|---|---|
| `x402_news_search_price` | $0.001 |
| `x402_ping_price` | $0.001 |
| `x402_social_react_price` | $0.002 |
| `x402_social_comment_price` / `follow` | $0.005 |
| `x402_scan_price` (sandboxed ClamAV scan — the closest existing "heavy external job" comparator) | $0.01 |
| `x402_features_vote_price` / `x402_grading_grade_price` | $0.02 |
| `x402_features_demand_price` / `x402_grading_score_price` | $0.03 |
| `x402_board_price` / `kyc_lookup_price` | $0.05 |
| `x402_listing_price` | $0.10 |
| `x402_social_group_create_price` | $0.25 |

3 cents lands at the `grading_score`/`features_demand` price point — already
one of the pricier routes in the whole marketplace, well above the "compute
job" comparator (`x402_scan` at 1 cent).

- For the **cheap-single-search** question (§3.2, "what are the planes this
  week"): 3 cents covers a $0.01–$0.025 SerpApi cost with a thin margin
  (~0.5–2 cents). Workable, barely, before accounting for our own compute,
  the payment-rail overhead every other route already absorbs, or refunds.
- For the **"minimize price in January"** question: even the sparse-sampling
  compromise (~10 calls, ~$0.10–$0.25) already **loses money outright** at 3
  cents — the underlying SerpApi cost is 3-8x the proposed charge.
- For the **"where cheaply next month"** question: the only affordable path
  is `google_travel_explore`'s single call (~$0.01–$0.025, marginally
  profitable at 3 cents) — the curated-candidate-list fallback (20-40 calls,
  up to $1.00) is 15-30x more expensive than the proposed price and cannot
  be offered at a flat 3 cents at all.

**Conclusion on pricing, independent of the ToS blocker**: a single flat 3
cent price cannot cover all three example question types. If this were ever
built (on a ToS-compliant data source), it needs **per-endpoint pricing that
reflects real query fan-out** — a cheap flat price for the single-date
search, and either a materially higher price or a hard-capped/sampled scope
(with the sampling limitation disclosed in the response) for the calendar
and explore-style questions — mirroring how `x402_storage` already prices
per-MB rather than flat, for the same reason (cost scales with the request,
not fixed).

---

## 4. Caching proposal (architecture only — contingent on a ToS-compliant vendor)

Even setting the SerpApi legal blocker aside, "heavy caching" has a real
tension the owner's framing doesn't resolve on its own: flight prices are
genuinely volatile (dynamic pricing, seat-class inventory draining, fare
sales), so an aggressively long TTL directly trades away answer accuracy for
margin.

Proposed shape if this were built on a compliant vendor:

- **Cache key**: `(origin, destination, outbound_date, return_date|null,
  cabin_class, adults)` — a normalized tuple, not the raw query string, so
  equivalent phrasing ("CDG→CAI" vs "Paris→Cairo") collapses to one key
  after airport/city resolution.
- **TTL tiers by lead time**, since price volatility isn't uniform across the
  booking window:
  - Departure < 14 days out: short TTL (e.g. 1-2 hours) — prices move fastest
    close-in.
  - Departure 14-60 days out: medium TTL (e.g. 6-12 hours).
  - Departure > 60 days out: longer TTL (e.g. 24 hours) — far-out prices move
    slower.
- **Explore/deal results** (broader, "starting from" prices rather than a
  bookable quote) can tolerate a longer TTL than a specific bookable
  itinerary, since they're already advertised as indicative, not final.
- **Realistic hit rate**: honest expectation is **low-to-moderate**, not
  "heavy caching solves the cost problem." Flight search query space is
  extremely long-tail — two agents asking about the same origin/destination/
  exact-date/cabin combo within the same TTL window is a real but limited
  overlap (much lower than, say, caching a news headline list that every
  reader shares). The popular trunk routes (major city pairs, common cabin
  class) will cache well; anything with an unusual origin, exact date, or
  multi-city itinerary will be a near-guaranteed cache miss, meaning most of
  the actual cost (§3) is still paid per query, not amortized away. Cache TTL
  controls staleness risk and shaves the popular-route tail, but it is not a
  substitute for pricing every route close to its real fan-out cost (§3.3).
- Any cached response returned as an answer must be labeled with the
  timestamp it was fetched, given flight prices can move meaningfully within
  even a short TTL window — a paying agent should be able to tell "this is a
  live quote" from "this is a cached quote from 40 minutes ago."

---

## 5. Architecture sketch (design only, contingent on resolving §1)

Following the established `x402_catalog` / `require_paid_request` /
`run_with_refund` pattern (matched against `backend/app/modules/x402_news/
api/routes.py` and the `PRODUCTS` roster in `backend/app/modules/x402_catalog/
services/catalog.py`):

A new `x402_flights` module, `Product(key="flights", ...)`, gated the same
way every other product is (`x402_enabled` plus its own store setting), with
three priced routes:

1. **`GET /api/v1/x402/flights/search`** — direct single-date search.
   - Params: `origin`, `destination`, `outbound_date`, `return_date?`,
     `cabin_class?`, `adults?`.
   - Cheapest tier price (e.g. matching `x402_news_search_price`'s order of
     magnitude if margin allows, not 3 cents flat — §3.3).
   - `product_write` wraps a cache-then-SerpApi lookup exactly like
     `_news_search_product_write` wraps Typesense: cache hit returns
     immediately; cache miss calls the vendor, stores the result under §4's
     key/TTL, returns it. Vendor call failure raises (never returns `{}` /
     `[]` — CLAUDE.md §2 invariant 8) so `run_with_refund` can refund
     instead of silently faking a "no flights found" answer.

2. **`GET /api/v1/x402/flights/cheapest-window`** — "when should I fly."
   - Params: `origin`, `destination`, `date_from`, `date_to`, `cabin_class?`.
   - Priced materially higher than `search` (this is the multi-call one,
     §3.2) — needs its own price setting reflecting real fan-out, and should
     cap the window size (e.g. max 31 days) the same way every other route
     in this codebase caps list sizes (CLAUDE.md §4 "no unbounded listings").
   - Response should disclose whether it used a native deals/calendar
     surface or a sampled sweep, and at what sampling density, so the payer
     knows the limits of what they paid for.

3. **`GET /api/v1/x402/flights/explore`** — "where can I travel cheaply."
   - Params: `origin`, `date_from`, `date_to?`, `budget_max?`, `region?`.
   - Backed by the single-call `google_travel_explore`-shaped query where
     available; priced close to the single-search tier since it's one call.
   - If ever extended to a curated-candidate-list fallback for broader
     coverage than the native explore surface offers, that variant needs a
     visibly different (higher) price — same reasoning as
     `cheapest-window`.

All three: rate-limited the same way free routes are, wired through
`require_paid_request`/`run_with_refund`/`mark_fulfilled` for settlement and
refund-on-failure, `describe_json_endpoint` for Bazaar discovery with a
realistic `input_example`, and entries in `PRODUCTS`/`config.py` per the
established convention (price settings owned in `config.py`, never raw env
reads — CLAUDE.md §3).

---

## 6. Recommendation

**Don't build it — not against SerpApi, not as designed.** Two independent,
sufficient reasons:

1. SerpApi's own ToS prohibits reproducing, copying, or exploiting search
   results for resale without express written permission — precisely what
   this product's "query once, cache, sell processed answers to many paying
   third parties" design requires (§1.1–1.3).
2. SerpApi is itself the defendant in an active Google lawsuit over exactly
   this resale business model (filed 2025-12-19, DMCA circumvention claims,
   damages potentially in the billions against a vendor with reported annual
   revenue of a few million dollars) — an existential risk to the vendor this
   product would depend on, independent of what its contract says (§1.4).

Even if the ToS question were resolved via a different, resale-permitting
vendor, the pricing as proposed (flat 3 cents) does not cover the real
per-query cost of two of the owner's three example questions (§3.3) — the
"minimize price across a month" question loses money outright at any
realistic sampling density, and "where can I travel cheaply" is only
affordable through a single-call explore-style endpoint, not a general
curated-list fallback.

**If the owner wants to pursue this product family**, the concrete next
steps, in order, are:

1. Identify a flight-data vendor whose terms explicitly permit
   redistribution/resale to the customer's own paying end users (travel-
   industry GDS-backed APIs built for resellers — Amadeus, Duffel, Kiwi
   Tequila-style APIs — are the natural candidates, not a "Google Search
   results, structured" scraper-reseller like SerpApi). This needs the same
   ToS-first check this document just did for SerpApi, before any design
   work resumes.
2. Once a compliant vendor is chosen, redo §2's data-shape research and
   §3's cost math against that vendor's actual pricing and actual
   calendar/explore endpoint support (these vary a lot vendor to vendor).
3. Price per-endpoint by real fan-out cost (§3.3), not one flat number
   across three questions with wildly different query costs — following
   this codebase's own precedent (`x402_storage` prices per-MB, not flat).
4. Only then build against the `x402_catalog`/`require_paid_request`
   pattern sketched in §5.

Do not start on §5's build under the current SerpApi plan. Flag this to the
owner as a stop, the same way the Brave-resale plan was stopped earlier
today.

---

## Sources consulted

- SerpApi Terms of Service — https://serpapi.com/legal
- SerpApi Pricing — https://serpapi.com/pricing
- SerpApi Google Flights API docs — https://serpapi.com/google-flights-api
- SerpApi Google Flights Price Insights docs — https://serpapi.com/google-flights-price-insights
- SerpApi Google Flights Deals API docs — https://serpapi.com/google-flights-deals-api
- SerpApi Google Travel Explore API docs — https://serpapi.com/google-travel-explore-api
- Google v. SerpApi lawsuit coverage: https://ipwatchdog.com/2025/12/26/google-sues-serpapi-parasitic-scraping-circumvention-protection-measures/ ,
  https://searchengineland.com/google-sues-serpapi-466541 ,
  https://www.vktr.com/ai-platforms/google-sues-serpapi-over-data-scraping/
- This codebase: `backend/app/modules/x402_catalog/services/catalog.py`
  (`PRODUCTS` roster), `backend/app/core/config.py` (price settings),
  `backend/app/modules/x402_news/api/routes.py` (paid-route pattern
  reference).

---

## Vendor comparison: alternatives to SerpApi

Status: **research only, no code written.** Follow-up to §1–§6 above, per the
owner's instruction to identify a GDS-backed vendor whose terms explicitly
permit commercial resale to our own paying end-customers, rather than
another Google-scraper-reseller. Same method as §3 (per-search cost ×
realistic call count per example question), so the comparison is apples-to-
apples with the SerpApi numbers already on record. Web research only — no
account created, no calls made against any of these vendors, no code
touched.

### V.1 Amadeus for Developers

**Resale clause: could not be confirmed either way — and it is now moot for
a new signup regardless of what it says.** The headline finding here isn't
about a clause, it's about access: **Amadeus decommissioned its entire
Self-Service developer portal on 2026-07-17.** New self-service registration
was paused starting spring 2026 and the portal (API keys included) was fully
shut down that date — confirmed independently by PhocusWire's coverage of
Amadeus's own letter to users, plus multiple migration-guide sites written
for stranded self-service customers (Amadeus gave no public reason). The
only remaining path to any Amadeus flight API — Self-Service or Enterprise —
is now the **Enterprise** track, which requires: IATA/ARC accreditation (or
equivalent), a negotiated commercial contract, a technical/PCI-DSS/GDPR
compliance audit, and a sales/onboarding process that third-party
integrators describe as running "weeks to months," with minimum-volume
commitments typical of GDS enterprise deals. Enterprise contract terms are
individually negotiated and not published, so there is no public clause to
quote on resale — I looked for the old Self-Service Terms of Use PDF's
resale language directly (`developers.amadeus.com/.../Terms of Use -
Self-Service...pdf`) and could not extract clean clause text from it via the
available tools even before considering it's now a dead product; I am not
guessing at what it said. **Net: unclear/unverifiable on paper, and
irrelevant in practice — there is no self-serve signup left to evaluate
against.**

Before the shutdown, Amadeus's self-service catalog was, on data shape
alone, the best fit of everything researched for this task:
- **Flight Cheapest Date Search** — a real native calendar query: given
  origin (+ optional destination/date), returns priced options across
  candidate dates, sortable by price/date/duration. This directly answers
  question 2 in one call, unlike every other vendor here.
- **Flight Inspiration Search** — a real native "explore" query: given only
  an origin IATA code, returns a list of destinations ordered by price
  (filterable by max price / departure date), pulled from a daily-refreshed
  cache of trending searches/bookings. This directly answers question 3 in
  one call.

Both still appear in current third-party documentation mirrors (e.g.
apis.guru's schema mirrors of `amadeus-flight-cheapest-date-search` and
`amadeus-flight-inspiration-search`, both v1.0.6), so the products existed
as designed — the gate is access, not whether the endpoints exist.

- **Pricing**: historically free tier ~2,000 req/month (Flight Offers
  Search) / ~3,000 (Flight Offers Price), then a small per-call charge
  (sub-cent to a few cents depending on endpoint per third-party summaries —
  Amadeus itself never published one flat number the way SerpApi does), plus
  a documented 90% discount on search calls tied to a completed booking
  through the platform (irrelevant to a search-only product, since we'd never
  trigger it). This pricing model is dead for new signups; Enterprise pricing
  is negotiated and not public.
- **Cost-per-answer**: not publishable for a real decision — Enterprise
  pricing requires a sales conversation to even get a number, which itself
  answers the "self-serve this week" question (no).
- **Onboarding friction**: worst of the four core vendors — sales process,
  accreditation, audit, likely minimum commitment, no self-serve tier at all
  as of today (2026-09-04).

### V.2 Duffel

**Resale: explicitly permitted for the intended "sell travel" use case, but
with a specific carve-out that is a real risk for exactly the product shape
being proposed.** From Duffel's Services Agreement (`duffel.com/services-
agreement`), clause 2.5(e), quoted verbatim:

> "[You shall not] license, sell, rent, lease, transfer, assign, distribute,
> display, disclose, or otherwise commercially exploit, or otherwise make
> the Services available to any third party (or assist third parties in
> obtaining access to the Services) except for the Authorised Users **and to
> offer the Travel Services to your customers as envisaged by this
> Agreement**."

That carve-out is the resale permission this whole search was looking for —
Duffel's entire business model is customers (OTAs, travel agents, "any app
that sells flights") reselling Duffel-sourced flight content to their own
paying end users, and it says so affirmatively, unlike every scraper-reseller
in §1. **However**, clause 2.5(d), quoted verbatim, is a direct problem for
a *search-only, no-booking* product:

> "[You shall not] access or use the Services for metasearch purposes
> (including to build a metasearch on top of the Duffel Platform and/or to
> redistribute to a metasearch platform)."

The agreement doesn't define "metasearch" beyond that clause, but the
owner's three example questions — pay us, we search, we report back a price
or a list of options, no booking ever happens on our side — is structurally
what "metasearch" ordinarily means in this industry (compare-and-report,
not facilitate-the-purchase). Duffel monetizes on completed orders ($3/order
+ 1% of managed-content order value + $2/ancillary — see pricing below), so
a product that only ever calls the search endpoint and never creates an
Order looks, from Duffel's side, exactly like the thing 2.5(d) exists to
stop. This is a genuine ambiguity, not a clean yes: Duffel is unambiguously
resale-friendly *if the product lets the payer actually book through us*,
and questionable-to-prohibited if it's answer-only with no booking path.
Compounding this, clause 2.3 (Search-to-Order Ratio) puts Duffel on notice
to monitor and potentially cap accounts whose searches vastly outpace their
orders — a search-only product by definition creates zero orders, so 100%
of its call volume would sit in the "Excess Search" / abuse-monitoring
bucket from day one, verbatim: "You shall not use the Services in such a way
that Duffel believes (acting reasonably) has or is likely to have an adverse
impact on the Services, including sending excessive calls to the Duffel
Platform and an excessive Search-to-Order Ratio."

- **Native calendar/explore**: **no.** Confirmed against Duffel's own Offer
  Request API docs (`duffel.com/docs/api/offer-requests/create-offer-
  request`): each slice takes exactly one specific `departure_date`; there
  is no date-range or multi-candidate-date parameter, and no separate
  destination-inspiration/explore endpoint exists in the documented API.
  Third-party wrappers that market "flexible date search" on top of Duffel
  (e.g. an MCP "Find Flights" server) are doing the date-sweeping
  client-side, calling Duffel once per candidate date — same brute-force
  shape as the SerpApi fallback in §3.2, not a native capability.
- **Pricing** (`duffel.com/pricing`, confirmed self-serve/pay-as-you-go, no
  free tier, no minimum commitment, signup at `app.duffel.com/join`):
  - $3.00 per confirmed Order (monthly-billed)
  - 1% of order value for "managed content" orders
  - $2.00 per paid ancillary
  - **$0.005 per Excess Search** — i.e. any search beyond a 1,500-searches-
    per-Order allowance (confirmed via Duffel's own help-center article "What
    is Excess Search?": allowance = orders × 1,500; since a search-only
    product with zero Orders has an allowance of 0, **every single search
    call would bill at $0.005**, not merely the calls "beyond a quota" — the
    quota is zero).
  - 2% FX conversion fee (not applicable if quoting in the vendor's native
    currency).
  - No published free tier for independent developers.
- **Cost-per-answer** (same method as §3.2, all at $0.005/search since our
  allowance is always zero):
  - Q1 (single date/route): 1 call ≈ **$0.005**.
  - Q2 (cheapest date in a month): no native calendar, so a swept sample —
    sparse ~10-date sampling ≈ **$0.05**; a full 31-day one-way sweep ≈
    **$0.155**; a full round-trip date-grid (31×31) ≈ **$4.80** (avoid this
    shape entirely, same conclusion as §3.2).
  - Q3 (open-destination "where cheaply"): no native explore endpoint, so a
    curated 20-40-destination candidate list ≈ **$0.10-$0.20**.
  - All three are 2-5x cheaper per call than SerpApi's $0.01-$0.025/search —
    Duffel's raw unit economics are the best of any vendor checked here, if
    the metasearch/search-to-order-ratio risk above is resolved (e.g. by
    talking to Duffel directly about the intended use before launch, which
    is cheap insurance given how good the underlying pricing is).
- **Onboarding friction**: **lowest of any vendor here** — self-serve API
  key, pay-as-you-go, no sales call needed to get a working key and start
  making real (non-sandbox) calls. The friction is entirely on the ToS-fit
  question above, not access.

### V.3 Kiwi.com Tequila API

**Resale terms: cannot be verified — access itself is gated, so there is no
public ToS text to check.** Tequila has been **invitation-only since 2024**;
self-serve API-key registration is closed, and this is corroborated across
multiple independent sources (a Kiwi-adjacent open-source docs mirror,
travel-tech integrator blogs, and Kiwi's own partner-support-shaped
messaging that new applicants need "a live travel product or an established
distribution use case"). Commercial terms — including any resale
permission — are negotiated per-partner inside the Tequila portal after
approval and are explicitly stated (by third-party integrator guides
summarizing Kiwi's own partner messaging) to vary partner-to-partner, so a
generic public clause doesn't exist to quote. **I am stating this as
genuinely unresolved, not a guess in either direction**, per the task
instructions — this is exactly the "ambiguous, say so" case.

This is a real loss on the data-fit side, because Tequila's documented API
shape is otherwise the strongest match for questions 2 and 3 of any vendor
researched:
- **Native cheapest-month/calendar search** — Tequila's `/search` supports
  querying across a full month or arbitrary date range to find the minimum
  price, purpose-built for "flexible date" UI widgets — directly answers
  question 2.
- **NOMAD API** — given a departure point and a date window (with no
  destination specified, or a candidate set), returns the cheapest
  multi-destination routing — the closest thing to a genuine "where can I
  go cheaply" native query of any vendor checked, arguably a better match
  for question 3 than even Amadeus's Inspiration Search, since it's
  optimizing a real itinerary rather than returning independent per-
  destination starting prices.
- **Pricing**: cannot be stated — not published; Tequila's monetization is
  described (by third-party sources, since Kiwi doesn't publish this
  directly) as commission/affiliate-based (revenue on referred bookings)
  rather than a flat per-call fee, which — like Travelpayouts below — may
  not map cleanly onto a "pay us a flat fee per search, no booking" x402
  product even if a partnership were granted.
- **Cost-per-answer**: cannot be estimated — no public per-call price exists
  to compute from.
- **Onboarding friction**: application/invitation-gated, explicitly
  described as wanting an applicant with an existing live product or
  distribution channel — not a good fit for "start this week," and not
  guaranteed to succeed even if attempted.

### V.4 Skyscanner

**Resale: explicitly and unambiguously prohibited — the cleanest kill of
any vendor checked, arguably worse than SerpApi's clause for this specific
product shape.** Skyscanner's Content API Data Services Terms
(`skyscanner.net/media/content-api-data-services-terms-skyscanner-limited`),
corroborated identically across the equivalent EMEA API terms and the
general Data Products Terms and Conditions pages, state customers must not:

> cache Travel Data for a period exceeding 24 hours; resell or repackage the
> API Services or Travel Data; and — the clause that matters most here —
> **"not charge End-Users (whether directly or indirectly) for access to the
> Travel Data."**

That last clause is a direct, explicit prohibition on the exact mechanism
this x402 product is: an end user (the paying agent) pays us, directly, for
access to flight search data sourced from the vendor. There is no
search-vs-book distinction to fall back on here the way there is with
Duffel — the prohibition is on charging for *access to the data itself*,
full stop. The Data Products Terms add a further catch-all: no reselling,
redistributing, repackaging, sublicensing, or otherwise commercially
exploiting the data to any third party "without prior written consent of
Skyscanner."

- **Access**: **partner-only**, invitation/application-based
  (`partners.skyscanner.net/product/travel-api`), explicitly reserved for
  "an established business with a large audience and strong alignment with
  Skyscanner's brand and values" — approval is case-by-case, ~2 weeks if
  accepted. Third-party summaries note that older "free self-serve API key"
  documentation is stale as of 2026; there is no current self-serve path.
- **Native calendar/explore**: not evaluated further — moot given the
  resale prohibition is dispositive on its own, same logic the SerpApi
  section applied (don't need to price out a vendor that's already
  contractually closed off).
- **Pricing / cost-per-answer**: not applicable — killed at the ToS gate
  before pricing is a relevant question.
- **Onboarding friction**: application-gated with an audience-size bar this
  project likely doesn't clear yet, on top of being legally closed off
  regardless.

### V.5 Other candidates checked, not pursued further

- **Sabre Dev Studio**: same enterprise-sales pattern as Amadeus — no fixed
  published pricing, third-party estimates put commercial access at
  roughly $500-$5,000+/month depending on volume/tier, production access
  requires a commercial agreement (sandbox/test is free). No public clause
  found addressing resale to end customers specifically — would need a
  direct sales conversation to get real terms, same "not self-serve this
  week" conclusion as Amadeus. Not researched to the same depth as V.1-V.4
  since the access-friction conclusion was already clear from the pricing
  research alone.
- **Travelpayouts (Aviasales Data API / Flight Search API)**: structurally
  a poor fit independent of ToS specifics — it's an affiliate network
  (Kiwi.com's own affiliate program runs through it), monetized via
  referral commission on bookings the *end human* completes through an
  embedded affiliate link, not a flat per-call fee a paying agent could be
  charged against. Their richer real-time Flight Search API additionally
  gates on the requesting project having ≥50,000 monthly active users,
  which this product doesn't have. Not evaluated further — the
  commission-on-click monetization model doesn't map onto "an agent pays us
  once for a search answer" regardless of what the ToS says about resale.
- **Travelport**: not reached in this pass — every public source found
  during this research pointed to the same "enterprise sales conversation,
  no published self-serve pricing" pattern as Amadeus/Sabre with no
  additional information likely to change the recommendation below; flagging
  as **not independently verified** rather than asserting a conclusion about
  it.

### V.6 Per-vendor summary table

| Vendor | Resale permitted | Self-serve signup | Cost/simple search | Q2 (cheapest-date-in-month) realistic cost | Q3 (explore-cheap) realistic cost | Native calendar/explore |
|---|---|---|---|---|---|---|
| SerpApi (baseline, §1-3) | **No** — ToS bars exploit/resale; vendor itself under DMCA suit | Yes | $0.01-$0.025 | ~$0.10-$0.25 (sampled) / $0.31-$11+ (exhaustive) | ~$0.01-$0.025 (native explore) / $0.20-$1.00 (curated list) | Partial (explore yes; calendar no, only curated "deals") |
| Amadeus for Developers | Unclear/moot — self-service dead 2026-07-17; Enterprise terms unpublished/negotiated | **No** — Enterprise sales+accreditation only | Not publishable (negotiated) | Not publishable | Not publishable | **Yes, both** (Cheapest Date Search, Inspiration Search) — but inaccessible |
| Duffel | **Yes for booking-path resale; ambiguous/risky for search-only** (2.5(e) permits, 2.5(d) bars "metasearch") | **Yes** | $0.005 | ~$0.05 (sampled) / ~$0.155 (31-day sweep) | ~$0.10-$0.20 (curated list) | No (neither) |
| Kiwi.com Tequila | **Unclear — access-gated, no public terms to check** | No — invitation-only since 2024 | Not publishable | Not publishable | Not publishable | **Yes, both** (calendar search, NOMAD) — inaccessible without approval |
| Skyscanner | **No — explicit "not charge End-Users for access to the Travel Data"** | No — partner application, case-by-case | N/A (moot) | N/A | N/A | Not evaluated (moot) |
| Sabre Dev Studio | Not found publicly | No — sales process | ~$500-$5,000+/mo, not per-call | Not publishable | Not publishable | Not evaluated |
| Travelpayouts/Aviasales | Not the right model (commission-based, not resale-of-data) | Partial (MAU≥50,000 gate on the relevant API) | N/A — commission model | N/A | N/A | Not evaluated (poor structural fit) |

### V.7 Recommendation

**No vendor researched here cleanly clears both bars — legal and unit-
economics — at self-serve speed.** Ranked by how close each comes:

1. **Duffel is the closest fit and the only one worth acting on now**, but
   not by simply flipping the SerpApi build to a new vendor name. Its
   resale clause is affirmatively permission-granting for "offer the Travel
   Services to your customers as envisaged by this Agreement" — the
   opposite posture of SerpApi/Skyscanner — and its raw per-call economics
   ($0.005/search) are 2-5x better than SerpApi's, comfortably clearing a
   3-cent price on the single-search question (Q1: $0.005 cost vs $0.03
   price, ~6x margin) and getting close on the sampled versions of Q2/Q3
   (~$0.05-$0.20 cost vs $0.03 price — **still underwater at a flat 3
   cents**, same conclusion §3.3 already reached for SerpApi, just a
   smaller loss). The real blocker isn't price, it's clause 2.5(d)
   ("metasearch") plus the Search-to-Order Ratio abuse-monitoring language
   in 2.3: a product that is *purely* pay-to-search-and-report, with no
   path for the payer to actually complete a booking through us, is
   structurally what those clauses exist to stop, and 100% of its volume
   would read as "Excess Search" from day one. **This needs a direct
   question to Duffel before launch** (their sales/support team, not a
   guess) — either (a) confirm a search-only informational product is
   acceptable use under their agreement, or (b) restructure the product so
   the endpoint surfaces a real, time-limited Duffel offer/booking link the
   payer *could* complete (even if most won't), which would put it
   unambiguously inside the 2.5(e) carve-out rather than the 2.5(d)
   prohibition.
2. **Amadeus's Cheapest Date Search + Flight Inspiration Search are the best
   data-shape match of anything found** (native one-call answers to
   questions 2 and 3, which nothing else here fully offers), but the
   2026-07-17 self-service shutdown moved the entire vendor behind an
   Enterprise sales/accreditation process — not a "this week" option
   regardless of what its resale terms turn out to say once negotiated.
   Worth a sales inquiry in parallel with the Duffel conversation if the
   owner wants the better data shape and is willing to accept a longer
   onboarding timeline, but should not block Duffel or gate the immediate
   next step.
3. **Kiwi.com Tequila has the single best native "explore" query (NOMAD)**
   of any vendor researched, but is invitation-gated with no public terms —
   worth an application in parallel, not worth waiting on.
4. **Skyscanner and (on current evidence) Travelpayouts are not viable at
   all** for this product shape — the former on an explicit contractual
   prohibition, the latter on a monetization-model mismatch.

**On the 3-cent price specifically**: it holds for Duffel's single-search
question with real margin (~6x), and it's the closest any vendor here comes
to making the owner's original flat price work — but it still does not
cover the calendar/explore questions at a flat rate, for the same structural
reason §3.3 already identified (query fan-out cost varies 10-40x across the
three question types, so a flat price cannot be right for all three no
matter which vendor sources the data). The fix is the same one §3.3 already
recommended: **per-endpoint pricing**, not a new flat number. Concretely, on
Duffel's economics: keep something close to 3 cents (or even lower, e.g. 1-2
cents, given the ~6x margin) for the single-search endpoint, and price the
calendar/explore endpoints separately in the 5-10 cent range to keep a
similar margin over their $0.05-$0.20 realistic cost.

### V.8 Onboarding reality check

**Duffel: yes, self-serve signup could start this week.** Signup is at
`app.duffel.com/join`, pay-as-you-go with no minimum commitment and no
sales call required to get a working production API key — confirmed
directly from Duffel's own pricing page. The blocker to actually building
against it is not access, it's resolving the metasearch/search-to-order-
ratio ambiguity in V.2 first (a support/sales email to Duffel asking
directly about the intended use case, which costs nothing and should happen
before any code is written against it, not after).

**Every other vendor researched requires a sales process, an invitation/
application, or is outright closed to new resale relationships** — Amadeus
(Enterprise sales + accreditation), Kiwi Tequila (invitation, existing-
product bar), Skyscanner (partner application, and legally closed off
regardless), Sabre (sales process). None of these are "start this week"
options.

### V.9 Sub-agent and file-scope confirmation

This research was performed directly, sequentially, vendor-by-vendor, using
only web search/fetch tools — **no sub-agents or forks were spawned for any
part of this task.** This section was appended to the existing file at
`/opt/g/algorand/docs/x402-flight-search-analysis.md` (the same file
containing the original SerpApi analysis in §1-§6 above); no new file was
created, and no existing content in the file was removed or altered. The
file remains untracked/uncommitted, per the same instruction the original
analysis followed.

### V.10 Sources consulted (this section)

- Amadeus self-service portal shutdown: PhocusWire —
  https://www.phocuswire.com/amadeus-shut-down-self-service-apis-portal-developers ;
  corroborating migration-guide coverage (dates: paused registration spring
  2026, decommissioned 2026-07-17) — https://ignav.com/docs/amadeus-self-service-shutdown ,
  https://www.tripgic.com/playbook/amadeus-api-shutdown-migration/ ,
  https://oneclicktraveltech.com/blogs/amadeus-self-service-api-shutdown
- Amadeus for Developers Terms of Use (self-service, 2018 PDF, could not
  extract clean clause text) —
  https://developers.amadeus.com/PAS-EAS/api/v1/cms-gateway/sites/default/files/2018-06/Amadeus%20for%20Developers%20-%20Terms%20of%20Use%20-%20Self-Service%20Test%20Environment%20-%20Feb%202018%20Unprotected_.pdf
- Amadeus Flight Cheapest Date Search / Flight Inspiration Search schema
  mirrors — https://api.apis.guru/docs/amadeus.com/amadeus-flight-cheapest-date-search/1.0.6.html ,
  https://api.apis.guru/docs/amadeus.com/amadeus-flight-inspiration-search/1.0.6.html
- Amadeus Enterprise access process (accreditation, negotiation timeline) —
  https://oneclicktraveltech.com/centerofexcellence/gds/how-to-access-amadeus-gds-api-for-travel-platform ,
  https://edana.ch/en/2025/08/15/amadeus-api-integration-practical-guide-to-access-gds-content/
- Duffel Services Agreement (clauses 2.3, 2.5(d), 2.5(e)) —
  https://duffel.com/services-agreement
- Duffel Terms — https://duffel.com/terms
- Duffel Pricing — https://duffel.com/pricing
- Duffel Offer Request API docs (date/slice parameters) —
  https://duffel.com/docs/api/offer-requests/create-offer-request
- Duffel "What is Excess Search?" help article —
  https://help.duffel.com/hc/en-gb/articles/4412912264466-What-is-Excess-Search
- Kiwi.com Tequila API access status (invitation-only since 2024) —
  https://phptravels.com/blog/comprehensive-guide-to-flights-api-integration ,
  https://kiwicom.github.io/margarita/docs/tequila-api
- Skyscanner Content API Data Services Terms —
  https://www.skyscanner.net/media/content-api-data-services-terms-skyscanner-limited
- Skyscanner EMEA API / Data Products Terms (corroborating language) —
  https://www.skyscanner.net/media/emea-api ,
  https://www.skyscanner.net/media/data-products-terms-and-conditions
- Skyscanner Partners application page —
  https://www.partners.skyscanner.net/product/travel-api
- Sabre API pricing summaries — https://phptravels.com/blog/sabre-api-pricing-breakdown ,
  https://oneclicktraveltech.com/centerofexcellence/gds/sabre-gds-api-cost-guide
- Travelpayouts / Aviasales Data API access requirements —
  https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API

---

## Vendor comparison, round 2: is there anything beyond Duffel?

Status: **research only, no code written.** Follow-up to §1–V.10 above, in direct
response to the owner's question "is there only Duffel?" — i.e. have we
actually canvassed the field, not just stopped at the first plausible name.
Same method as V.1–V.6 (ToS-first, quote the actual clause or say clearly
that none is public; then self-serve-vs-sales-gated; then native
calendar/explore capability; then realistic per-call cost). Web research
only — no account created, no calls made against any vendor, no code
touched, no sub-agents or forks spawned (see R.9).

### R.1 Travelport (JSON API / Travelport+ / Universal API)

**Resale: explicitly and unambiguously prohibited, on top of being sales-
gated — a clean kill, same shape as Skyscanner in V.4.** Two separate
Travelport legal documents both bar this product's exact mechanism, quoted
verbatim from `travelport.com/legal-policies`:

From the **API and SDK Terms of Use**:

> "You may not...copy, sell, license, or distribute the Content; or...grant
> your customers or any other person any rights to the Content."
>
> "You may not use Content to facilitate the sale of any travel inventory or
> ancillaries outside of the Services."
>
> "You shall not...use the Service to perform queries, but then complete
> bookings via a third party's system."

From the **Subscriber Terms and Conditions** (separately governing the
Universal/Smartpoint side of the business):

> "[Subscriber shall] not copy, store, archive, sell, or create a database of
> data or information obtained via the Products and Services (including the
> Travelport API), in whole or in part" (clause 2.3(e))
>
> "Subscriber shall ensure that the Products and Services are not used to
> transfer, provide access to or redistribute any data to any third party"
> (clause 2.3(i))
>
> "Subscriber will not, without Travelport's prior written consent, allow
> third party access to the Travelport API, or transfer or redistribute to
> any third party any data" (clause 7.5)

That is a direct, explicit prohibition on exactly this product's shape
(query once, store, sell processed answers to paying third parties) — no
narrower than Skyscanner's clause, and arguably broader since it separately
bars "us[ing] the Service to perform queries, but then complete bookings via
a third party's system," which would catch even a Duffel-style "here's a
booking link elsewhere" workaround.

- **Access**: **not self-serve.** Both documents describe a formal
  "Developer Contract" / subscriber agreement that must be executed before
  any API access; `MyTravelport` self-registration exists only for *existing*
  Travelport Galileo agency users managing their own account, not for a new
  third party seeking API data access. New agencies are routed to a sales
  form (`travelport.com` "contact sales," onboarding specialists, training) —
  same enterprise-sales pattern as Amadeus post-shutdown and Sabre.
- **Native calendar/explore**: not evaluated further — moot, same logic
  applied to Skyscanner in V.4 (don't price out a vendor that's already
  contractually and access-wise closed off).
- **Pricing / cost-per-answer**: not applicable — killed at the ToS gate
  before pricing is a relevant question, and no public per-call pricing
  exists to look up regardless (negotiated per subscriber agreement).
- **Verdict**: worse than useless as a "we didn't check this one" gap — it's
  a second confirmed dead end, and confirms the original document was right
  not to spend time on it beyond a passing mention.

### R.2 TravelgateX

**Resale terms: no public ToS text found, and the deeper problem is
structural, not just "terms not published."** TravelgateX is not a single
data source with one agreement — it is a **B2B marketplace connecting 350+
buyers to 600+ suppliers**, where, per TravelgateX's own public messaging:
"Before connecting with any of the suppliers integrated on the platform, you
should have a commercial agreement with them." TravelgateX itself is the
connectivity/protocol layer (GraphQL-based Hotel-X / Air-X style pull APIs);
the actual flight content, its price, and its resale terms are each governed
by a **separate bilateral agreement with whichever airline/consolidator
supplies it** through the marketplace. There is no single "TravelgateX
resale clause" to quote — the closest legal pages (`travelgatex.com/
termsandconditions`, `travelgatex.com/legalnotice`) both 308-redirect to the
plain marketing homepage, i.e. the URLs a search engine indexed for these
documents no longer resolve to any legal text.

- **Access**: **not self-serve** — "enterprise oriented and startup ready...
  pay as you go" per TravelgateX's own marketing copy, but every description
  of actually getting connected involves becoming a party to a network of
  individual supplier relationships, which is a heavier lift than even
  Amadeus Enterprise (that's one contract with one vendor; this is N
  contracts with N suppliers, mediated through TravelgateX's marketplace).
  This is structurally the wrong shape for "one ToS-compliant data feed" —
  even if TravelgateX's own platform terms turned out to be resale-friendly,
  each individual supplier's terms would need the same check repeated per
  supplier.
- **Native calendar/explore**: not confirmed either way — not worth
  investigating further given the access-structure problem above makes this
  moot for a "start this week" evaluation.
- **Pricing / cost-per-answer**: not publishable — no flat per-call number
  exists; monetization is per-marketplace-relationship.
- **Verdict**: not viable as a near-term option, for reasons closer to "wrong
  shape of vendor entirely" than "just needs a sales call."

### R.3 Verteil Technologies

**Resale terms: no public ToS found — this vendor doesn't publish terms
before a sales conversation, and says so implicitly by never surfacing a
terms link anywhere in its public developer-facing pages.** Verteil is an
NDC aggregator (IATA NDC Level 4 certified, 50+ airlines behind one API) that
explicitly markets itself to travel-tech resellers/OTAs in its own copy
("any new airline onboarded...available to agents at no additional cost").
That marketing posture is more resale-friendly *in spirit* than most vendors
checked here, but there is no public developer portal with self-serve
signup, pricing, or terms of use to verify it against — access starts with
"write to sales@verteil.com," the same "contact sales" pattern as Amadeus,
Sabre, and Travelport.

- **Access**: **not self-serve.** Registration happens through a sales
  conversation and (per third-party integrator guides describing the
  typical path) an NDC-content agency agreement, not an instant API key.
- **Native calendar/explore**: unconfirmed — NDC content APIs of this shape
  typically mirror the specific-date-per-slice pattern common to Duffel/
  Amadeus's own core Flight Offers Search, but Verteil doesn't publish
  enough documentation without an account to confirm a calendar/explore
  endpoint one way or the other.
- **Pricing / cost-per-answer**: not publishable — no public number exists.
- **Verdict**: a real candidate *in principle* (explicitly reseller-oriented
  marketing), unverifiable *in practice* without starting a sales
  conversation — same "flag and don't guess" treatment as Kiwi Tequila in
  V.3, one tier more closed since Tequila at least has a self-serve
  application form and Verteil doesn't appear to.

### R.4 FlightAPI.io

**Resale: explicitly and affirmatively permitted — the cleanest, most
direct resale grant of any vendor checked in either round of this research,
SerpApi/Skyscanner/Travelport/Duffel included.** From FlightAPI's own Terms
& Privacy page (`flightapi.io/terms-and-privacy`), quoted verbatim:

> "While your paid subscription is active, FlightAPI grants you a
> non-exclusive, worldwide license to access, store, display, analyze, and
> **redistribute—commercially or otherwise—the publicly available data
> returned by FlightAPI's API**"

with a single, narrow restriction:

> "provided that you do not add or alter data points in a way that would
> misrepresent the information actually returned by FlightAPI's API"

That is a direct, no-ambiguity yes to the exact question this whole search
started with — no "except for your customers as envisaged by this
Agreement" carve-out to interpret (Duffel, V.2), no separate "metasearch"
exclusion, no invitation-only gate. It reads like the resale permission the
owner assumed SerpApi would have and didn't.

This good news comes with a real, distinct caveat the other vendors mostly
don't share — **data provenance**. FlightAPI's own terms state:

> "All information returned by FlightAPI's APIs is collected in real time
> from publicly accessible web sources."

and separately disclaim accuracy and push legal-use responsibility onto the
customer:

> "FlightAPI makes no warranties—express or implied—regarding the accuracy,
> completeness, timeliness, or reliability of any data supplied."
>
> "You agree not to use any data obtained through FlightAPI in a manner
> that...violates applicable laws, or otherwise causes injury."

"Collected in real time from publicly accessible web sources" is the same
shape of language SerpApi uses to describe scraping — FlightAPI's own
marketing blog positions the product as *"a solution for obtaining accurate,
real-time, and legally sourced flight data"* as *an alternative to* directly
scraping Google Flights, which implies (without stating outright) that their
own pipeline scrapes airline/OTA/GDS-adjacent sites rather than Google
specifically. I could not find a precise, sourced answer to "exactly whose
sites does FlightAPI scrape, and have those sites' own ToS been checked or
challenged" — this is a real open question, not a confirmed problem the way
SerpApi's Google lawsuit is a confirmed problem. The honest framing: **the
contract FlightAPI signs with us is unambiguously resale-friendly; whether
FlightAPI's own upstream data-collection is on equally solid legal footing
is unverified**, and FlightAPI is a far smaller, less-established vendor
than SerpApi (no litigation history found in either direction — which cuts
both ways: no confirmed problem, but also no track record to lean on). This
is a materially different risk shape than Duffel, whose data comes through
direct airline/GDS NDC connections, not scraping of any kind.

- **Access**: **self-serve, confirmed** — sign up, generate an API key from
  the dashboard, ~20 free trial calls, no sales conversation, no minimum
  commitment beyond the monthly subscription tier chosen.
- **Native calendar/explore**: **no, neither.** Confirmed against FlightAPI's
  own documentation (`flightapi.io/documentation`) — the full endpoint
  roster is Oneway Trip, Round Trip, Multi Trip (all single-date-per-leg,
  same shape as Duffel/SerpApi's core search), plus unrelated Flight
  Tracking, Airport Schedule, and IATA code-lookup endpoints. No
  cheapest-date/calendar endpoint and no origin-only/explore endpoint exist
  in the documented API — a genuine gap, same one Duffel has.
- **Pricing** (`flightapi.io/pricing`, confirmed): Lite $49/mo (30,000
  credits), Standard $99/mo (100,000 credits), Plus $199/mo (500,000
  credits); no free ongoing tier, only a ~20-call trial. Confirmed via the
  Oneway Trip API's own documentation page: **each search call costs 2
  credits**, giving a real per-search cost of:
  - Lite: 30,000 credits ÷ 2 = 15,000 searches ⇒ **≈$0.00327/search**
  - Standard: 100,000 ÷ 2 = 50,000 searches ⇒ **≈$0.00198/search**
  - Plus: 500,000 ÷ 2 = 250,000 searches ⇒ **≈$0.000796/search**
  This is cheaper per call than Duffel's $0.005/search at every tier, and
  dramatically cheaper than SerpApi's $0.01-$0.025 band.
- **Cost-per-answer** (same method as §3.2/V.2, using the Standard tier's
  ≈$0.002/search as a realistic starting point before committing to Plus
  volume):
  - Q1 (single date/route): 2 credits, 1 call ≈ **$0.002**.
  - Q2 (cheapest date in a month): no native calendar, brute-force sweep —
    sparse ~10-date sampling (20 credits) ≈ **$0.02**; full 31-day one-way
    sweep (62 credits) ≈ **$0.06**.
  - Q3 (open-destination "where cheaply"): no native explore endpoint,
    curated 20-40-destination list (40-80 credits) ≈ **$0.04-$0.08**.
  - All three land meaningfully below Duffel's equivalent numbers (§V.2:
    ~$0.05 / ~$0.10-$0.20) purely on raw unit cost, on top of having no
    metasearch/search-to-order-ratio ambiguity at all, since the product is
    designed and licensed as a pure data reseller from day one with no
    booking component to compare search volume against.
- **Onboarding friction**: **lowest of any vendor checked in either round** —
  tied with Duffel for "instant self-serve API key," but with a cleaner
  contractual answer on the resale question specifically.

### R.5 AviationStack

**Wrong product entirely, independent of ToS — not a fare-search API.**
AviationStack (now an APILayer/Ideracorp product) is a real-time flight
*status and tracking* API: departure/arrival times, delays, current
position, historical flight records, airline/airport/aircraft lookups. It
has no fare, price, or search-by-route-and-date endpoint of any kind —
confirmed against its own product description and endpoint list
(`docs.apilayer.com/aviationstack`), which covers real-time flights,
historical flights, routes, and reference lookups but nothing price-related.
This cannot answer any of the owner's three example questions ("what are the
planes," "minimize price," "where cheaply") since none of them are about
price — it answers "is flight X delayed," a different product entirely.

- **Resale**: checked anyway for completeness, since access itself is easy —
  Apilayer's Master SaaS Subscription Agreement (the umbrella contract
  covering AviationStack and Apilayer's other products, e.g. pdflayer,
  vatlayer) contains **no clause affirmatively granting resale/redistribution
  rights** to third-party paying end-customers; it describes a subscription
  access model without addressing downstream resale either way. Per this
  document's own "don't guess" standard, silence here reads as **not
  permitted** (consistent with how pdflayer's sibling terms, which do
  address it, restrict "unauthorized reproduction, publication, disclosure,
  modification, distribution").
- **Access**: self-serve, cheap ($49.99/mo+, 100 free requests/month) — the
  easiest signup of any vendor in this round, which is irrelevant given the
  product-shape mismatch.
- **Verdict**: not a candidate for this product regardless of ToS — flagged
  only because the task asked for AviationStack by name; the reason to
  exclude it is data-fit, not legal.

### R.6 Travelpayouts / Aviasales Data API (distinct from the affiliate Flight Search API already checked in V.5)

**Structurally the same monetization-model mismatch V.5 already found for
the commission-based Flight Search API, now confirmed to extend to the
"direct" Data API too — plus this round surfaced an additional, sharper
kill for the *live* search product that V.5 didn't quote directly.**

New finding not in the original document: Travelpayouts' own FAQ states, for
the real-time Aviasales Flight Search API:

> "A query result must be comprehensively shown to a user and have a 'Book'
> button next to each flight variant... the minimum conversion ratio of
> searches into clicks using the 'Book' button should be 9%, with conversion
> of the 'Book' button clicks into purchases not lower than 5%."

This is a harder, more explicit kill than Duffel's Search-to-Order-Ratio
language (§V.2's 2.3) — it's not "we monitor and may act on abuse," it's a
**published numeric floor** on click-through and purchase conversion that a
pure answer-only x402 product (zero bookings, ever, by design) would violate
by construction from the first query. Separately: "It's forbidden to
automatically collect data from search results using the search API" — a
direct bar on exactly the "agent asks us, we query the vendor, we return a
processed answer" mechanism.

The **Data API** (`api.travelpayouts.com/aviasales/v3/search_by_price_range`,
distinct product, the one the task specifically asked about) is a genuinely
better data-shape match than the live search API — its own documentation
describes "cheapest days to fly" and a `destination=-` wildcard for "all
routes," which is a real native calendar-style and explore-style query,
closer to Kiwi Tequila's NOMAD than anything else self-serve-accessible
checked in this round. However:
- Its actual Terms-of-Use page (`support.travelpayouts.com/hc/en-us/
  articles/203956163-Aviasales-Data-API`) returned an HTTP 403 to automated
  fetching, so I could not directly confirm or rule out whether the
  Book-button/conversion-floor language above applies to this specific
  endpoint too, or only to the live Flight Search API — **stating this as
  genuinely unresolved, not a guess**, per the task's own instructions.
  Third-party integration guides describe the Data API as available to
  affiliates who don't clear the 50,000-MAU bar the live search API
  requires, which is a lower bar but not a different monetization model.
- What is confirmed: the data itself is **not live/bookable** — it is
  described as served "from the cache based on the user's search history
  from Aviasales websites," stored for 7 days. That means even a
  hypothetically ToS-clear version of this product would be answering "what
  have other travelers on Aviasales recently seen this route cost," not "what
  does this route cost right now" — a materially different and weaker
  product than what SerpApi/Duffel/FlightAPI.io all offer (a live quote).
- The underlying monetization model network-wide remains
  **commission-on-completed-booking**, the same structural mismatch V.5
  already identified for the sibling Flight Search API — a flat per-call
  x402 price doesn't map onto an affiliate network's revenue model
  regardless of which specific endpoint is used.
- **Verdict**: not a real reconsideration of V.5's conclusion — confirms it
  from a different angle, with one genuinely new and useful data point (the
  Data API's "cheapest days" / wildcard-destination shape is real and
  interesting) undercut by the same commission-model mismatch plus a new
  discovered limitation (cached historical data, not live pricing) that
  makes it weaker than first assumed even before the ToS ambiguity.

### R.7 Google Flights via an official Google channel

**Confirmed: no such product currently exists, and this isn't a gap in this
research — it's the actual state of the world.** QPX Express, Google's only
historical public flights API (built on the ITA Software acquisition), was
announced for retirement in November 2017 citing "low interest among our
travel partners" and fully shut down 2018-04-10. Nothing has replaced it as
a public product since. Multiple independent, dated sources checked in this
round agree: "As of August 2026, Google Flights has no official public
developer API." The underlying ITA fare engine survives only inside
enterprise partnerships aimed at airlines and large travel sellers directly
(not a self-serve or even sales-gated third-party product an x402 project
this size could realistically access) — the same closed-enterprise shape as
Amadeus Enterprise/Sabre/Travelport, but with no public developer portal at
all, not even a "contact sales" page, to evaluate against.

A handful of SEO-content-mill sites (`trawex.com`, `travelopro.com`) use
phrases like "Google Travel Partner API" in marketing copy aimed at selling
their own integration services; I treated these as **not credible sources
for the existence of a real Google product** — no citation to an actual
Google developer page or partner program backs the phrase, and Google's own
developer documentation site has no such listing. This is consistent with
SerpApi's own business existing specifically *because* no official
alternative exists — if Google offered a licensed resale-permitting flights
API, the scraper-reseller market this whole search started by rejecting
would have far less reason to exist.

- **Verdict**: confirmed dead end, not a new option. Nothing to build
  against here.

### R.8 Other candidates checked, not pursued further

- **Mystifly**: another NDC/GDS-adjacent airfare consolidator/marketplace
  ("Anywhere-to-Anywhere" global airfare marketplace), explicitly built for
  reselling to travel businesses (agent wallets, sub-agent controls,
  commission rules are native platform concepts) — the *marketing posture*
  is the most reseller-friendly of anything checked besides Duffel/
  FlightAPI.io. But: no public pricing, no public terms, documentation and
  sandbox access are both "shared after onboarding" per third-party
  integrator write-ups — same "sales-gated, unverifiable without starting a
  relationship" pattern as Verteil. Not investigated further given the
  access gate makes it a multi-week-minimum path regardless of what its
  terms would say.
- Also checked and ruled out at the search stage, no dedicated subsection
  needed: **Travelfusion** (returned no independent results distinct from
  Mystifly-adjacent consolidator coverage; would need direct site research
  to evaluate and nothing found flagged it as meaningfully different from
  the Mystifly/Verteil consolidator pattern).

### R.9 Sub-agent and file-scope confirmation

This research was performed directly, sequentially, vendor-by-vendor, using
only web search/fetch tools — **no sub-agents or forks were spawned for any
part of this task**, per the explicit instruction that a prior attempt at a
similar task in this project stalled badly trying to delegate. This section
was appended to the existing file at
`/opt/g/algorand/docs/x402-flight-search-analysis.md` (the same file
containing the original SerpApi analysis and the first vendor-comparison
round above); no new file was created, and no existing content in the file
was removed or altered. The file remains untracked/uncommitted, per the same
instruction the original analysis and round 1 both followed.

### R.10 Updated per-vendor summary table (round 2 vendors only — see V.6 for round 1)

| Vendor | Resale permitted | Self-serve signup | Cost/simple search | Q2 realistic cost | Q3 realistic cost | Native calendar/explore |
|---|---|---|---|---|---|---|
| Travelport (JSON API/Travelport+) | **No** — explicit "not...grant your customers...any rights to the Content," no third-party redistribution without written consent | No — Developer Contract + sales process | N/A (moot) | N/A | N/A | Not evaluated (moot) |
| TravelgateX | Unclear — no single clause exists; each supplier relationship is separately negotiated | No — bilateral per-supplier commercial agreements via a marketplace | Not publishable | Not publishable | Not publishable | Not evaluated (wrong structural shape) |
| Verteil Technologies | Unclear — reseller-friendly marketing, no public terms | No — sales conversation (sales@verteil.com) | Not publishable | Not publishable | Not publishable | Unconfirmed |
| **FlightAPI.io** | **Yes, explicitly and unconditionally** — "redistribute—commercially or otherwise" | **Yes** | **≈$0.002-$0.0033** | ≈$0.02 (sampled) / ≈$0.06 (31-day sweep) | ≈$0.04-$0.08 (curated list) | No (neither) — same gap as Duffel |
| AviationStack | No affirmative grant found (and wrong product — status/tracking, not fares) | Yes | N/A (wrong product) | N/A | N/A | N/A (no fare data at all) |
| Travelpayouts Data API | Same commission-model mismatch as V.5; Book-button/conversion-floor applicability to this specific endpoint unconfirmed (403 on ToS page) | Partial — lower bar than the 50k-MAU live API, still affiliate-account-based | N/A — commission model, not per-call | N/A | N/A | **Yes, both** (cheapest-days, wildcard-destination) but data is a 7-day-old cache, not live |
| Google (official channel) | N/A — no product exists | N/A | N/A | N/A | N/A | N/A |
| Mystifly (checked, not pursued) | Reseller-friendly marketing, unverified — quote-based | No — onboarding-gated | Not publishable | Not publishable | Not publishable | Not evaluated |

### R.11 Direct answer: is there only Duffel?

**No — FlightAPI.io is a second real, self-serve, contractually-cleaner
option, though it is not a strictly better choice than Duffel, just a
differently-shaped one.** The field genuinely was worth widening: this round
found one vendor (FlightAPI.io) whose resale grant is *more* unambiguous
than Duffel's — no "metasearch" exclusion to worry about, no
search-to-order-ratio abuse-monitoring language, because the product is
built and licensed from the ground up as a pure data-reseller API with no
booking leg at all. Its raw per-call economics are also better than Duffel's
across all three example questions (roughly 2-3x cheaper at the Standard
tier, without even needing Plus-tier volume).

That is a genuine, material finding — it directly answers "have we found
every real candidate" with "no, we hadn't; here's one more, and it resolves
the exact ambiguity that made Duffel a maybe rather than a yes." But it
doesn't make Duffel obsolete or unambiguously the wrong choice, because
FlightAPI.io trades Duffel's ambiguity for a different, harder-to-quantify
risk: **unverified upstream data provenance.** Duffel's flight content comes
through direct airline and GDS NDC connections — the data's legitimacy is
not in question, only whether *our specific use pattern* (search-only, no
booking) fits inside Duffel's own contract. FlightAPI.io's content is
"collected...from publicly accessible web sources" — likely some form of
scraping one or more airline/OTA/GDS-adjacent sites — and while FlightAPI's
*contract with us* is unambiguous, **whether FlightAPI's own collection
method is on solid legal footing with its upstream sources is genuinely
unknown from public information**, the same category of question that turned
out to matter enormously for SerpApi (§1.4). No lawsuit or public dispute
was found involving FlightAPI.io specifically — but the absence of a known
problem is not the same evidence of safety that Duffel's direct-connection
sourcing provides on its face.

Every other candidate the owner asked to check (Travelport, TravelgateX,
Verteil, AviationStack, Travelpayouts' own direct API) confirms rather than
overturns the original document's pattern: sales-gated, invitation-only,
wrong product shape, or a monetization model (commission/affiliate) that
doesn't map onto a flat per-call x402 price. The official-Google-channel
question has a clean, confirmed-negative answer: no such product has existed
since 2018, and nothing found in this research suggests one is coming.

### R.12 Updated overall recommendation

**This changes the original document's conclusion from "Duffel, with one
open legal question to resolve before launch" to "two viable self-serve
candidates, each with a single open question to resolve before launch,"
not from "uncertain" to "settled."** Concretely:

1. **Keep the Duffel next step exactly as V.7/V.8 already recommended** — a
   direct question to Duffel's sales/support team about whether a
   search-only informational product is acceptable use under clause 2.5(d),
   before writing any code against it. Nothing in this round changes that
   answer or makes it less necessary.
2. **Add FlightAPI.io as a parallel, low-cost, low-commitment path worth
   trialing at the same time**, specifically because its onboarding cost is
   trivial (self-serve signup, ~$49-99/month, no sales conversation, no
   minimum term) — there is no reason to wait on Duffel's answer before
   spending a few dollars confirming FlightAPI.io's actual response quality,
   coverage, and latency against a handful of real routes. Before routing
   any real payment volume through it, do the one piece of diligence this
   research could not complete from public information alone: **ask
   FlightAPI.io directly, in writing, exactly which upstream sources their
   "publicly accessible web sources" language refers to**, and get that
   answer on record before treating it as a production dependency — the
   same category of question that, unasked, is what made SerpApi's exposure
   invisible until Google's lawsuit made it impossible to ignore.
3. **Neither vendor solves the calendar/explore data-shape gap** (§3.2/V.2)
   — both require the same brute-force date-sweep or curated-destination-list
   fan-out as Duffel and SerpApi for questions 2 and 3. The
   per-endpoint-pricing conclusion from §3.3/V.7 stands unchanged regardless
   of which of these two vendors (or both) gets used: a single flat price
   cannot cover all three question types, on any vendor researched so far,
   in either round.
4. **Travelport, TravelgateX, Verteil, AviationStack, and Travelpayouts'
   direct Data API are now confirmed non-starters** for this product, for
   record-keeping — no need to re-check any of these again unless their
   public posture changes (e.g. Travelport publishes a self-serve tier,
   Verteil publishes public terms).

### R.13 Sources consulted (this section)

- Travelport API and SDK Terms of Use —
  https://www.travelport.com/legal-policies/api-sdk-policies-terms-of-use
- Travelport Subscriber Terms and Conditions —
  https://www.travelport.com/legal-policies/sa-terms-conditions
- Travelport+ / MyTravelport self-registration scope —
  https://support.travelport.com/webhelp/MyTravelport/Content/Access.htm ,
  https://www.travelport.com/faqs
- TravelgateX marketplace/bilateral-supplier model —
  https://www.travelgate.com/marketplace ,
  https://blog.travelgate.com/the-top-10-questions-businesses-ask-themselves-before-joining-travelgatex
- TravelgateX legal pages (both redirect to the marketing homepage, no terms
  text resolvable) — https://travelgatex.com/termsandconditions ,
  https://travelgatex.com/legalnotice
- Verteil Technologies product/access pages —
  https://www.verteil.com/product , https://www.verteil.com/
- FlightAPI.io Terms & Privacy —
  https://www.flightapi.io/terms-and-privacy/
- FlightAPI.io Pricing — https://www.flightapi.io/pricing/
- FlightAPI.io Documentation (endpoint roster) —
  https://www.flightapi.io/documentation/
- FlightAPI.io Oneway Trip API docs (credit cost) —
  https://www.flightapi.io/documentation/oneway-trip-api/
- FlightAPI.io positioning vs. Google Flights scraping —
  https://www.flightapi.io/blog/google-flight-api-history-and-alternative/
- AviationStack documentation — https://docs.apilayer.com/aviationstack/docs/api-documentation
- Apilayer Master SaaS Subscription Agreement (umbrella terms) —
  https://www.ideracorp.com/~/media/IderaInc/Files/APILayer/Apilayer%20Master%20Software%20as%20a%20Service%20Subscription%20Agreement%20SaaS%20082523ns%20FORM
- Travelpayouts Aviasales Data API — https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API (403 on direct fetch; content corroborated via search-indexed summaries)
- Travelpayouts Aviasales Flight Search API requirements (Book-button /
  conversion-ratio language) —
  https://support.travelpayouts.com/hc/en-us/articles/210995808-Requirements-for-Aviasales-Flight-Search-API-access ,
  https://support.travelpayouts.com/hc/en-us/articles/204529267-FAQ-about-API
- QPX Express shutdown / no successor —
  https://duffel.com/blog/google-flights-api ,
  https://airlabs.co/google-flights-api-alternatives ,
  https://iproyal.com/blog/google-flights-api/
- Mystifly overview — https://phptravels.com/blog/mystifly-api ,
  https://www.prnewswire.com/news-releases/mystifly-announces-launch-of-new-generation-airline-retailing--shopping-api-platform-301003578.html
