# x402 USDC liquidity-routing product — research and design

> **Note (2026-09-10):** "KYC" below means exchange-side customer verification of a human, not this repo's former KYA module (removed 2026-09-10, [ADR-0006](adr/ADR-0006-x402-consolidation.md)); the `x402_uptime` module cited below as the pattern to copy (bool-setting gate, fail-soft quote cache) and the `x402_uptime_price`/`x402_grading_score_price`/`x402_features_demand_price`/`kyc_lookup_price` comparables were all removed 2026-09-10 too — `x402_scan` is the surviving bool-gated stateless-product pattern, and `x402_news_search_price`/`x402_scan_price` the surviving anchors.

Status: **research only, no code written**. Written in response to the owner's
brief for an x402-paid endpoint that computes and sells the best
currently-available route (and its real, current cost) for moving USDC onto
or off Algorand — in **both directions**, symmetrically. Per instructions
this file is left untracked for review, not committed.

Scope guard, restated up front because it shapes every section below: this
is a pure **information/routing product**. We never take custody, never
execute anything on the payer's behalf, never act as counterparty to any
swap or transfer. We compute and report an answer — the same shape as the
live `x402_news` paid search and the unbuilt roadmap items 14
("Storage price-discovery query") and 22 ("EURQ liquidity/settlement data
feed"). The earlier custodial-bridge idea is shelved pending a legal
opinion and is **not** designed here. See §8 for the full "does NOT do"
list.

**Bottom line up front:** the product is buildable now, on free public
APIs, with live verified data — and the research materially changed the
route picture from the brief's starting assumption. The imagined default
route ("swap USDC→ALGO on a DEX, move ALGO to an exchange, convert on the
other side") is in 2026 usually the *worse* of the two rails that are
actually solid:

1. **Exchange rails carrying native USDC-on-Algorand directly** — Kraken
   confirmed first-party (own product blog, added 2026-01-22); Binance
   confirmed first-party as *opened* (own support announcement,
   2022-11-30; current status unverified); Coinbase claimed but **not**
   first-party-confirmed (see §1.1). Deposit USDCa, withdraw USDC on
   Base/Solana/Ethereum/etc. No DEX hop, no ALGO price exposure.
   **Hard precondition (§1.6): a human-owned, pre-KYC'd exchange account.**
   No exchange rail is reachable from a bare wallet — every one of these
   requires a real person's identity behind the account, so for the
   wallet-only autonomous agents this marketplace actually serves, this
   rail exists only when a human sponsor set the account up in advance.
2. **DEX + ALGO leg** — the brief's assumed route. Its on-Algorand swap
   is the one leg an unverified, wallet-only agent can execute from a
   bare wallet, live-quotable today via two working aggregator APIs
   (verified with real quotes during this research) — but it adds
   measurable price impact (0.15% at $1k rising to ~3.9% at $50k on
   today's books) *plus* ALGO volatility exposure in transit *plus* two
   conversion spreads. And its cross-chain ALGO hop, as originally
   imagined, *also* passes through an exchange — see below.

That §1.6 finding reshapes the value proposition, more brutally than
first expected: **no confirmed, fully wallet-only cross-chain USDC rail
exists today.** Exchange rails need a human-owned KYC'd account; the
DEX+ALGO route's exchange hop needs the same; the only wallet-native
cross-chain paths are Portal (wrapped USDC, poor exit liquidity — §1.3)
and Allbridge (unconfirmed — §1.2). For a wallet-only agent the product
is therefore not "pick the cheapest of several usable rails" — it is
"learn, for half a cent, that your executable options are bad and
exactly how bad, get a real per-size quote for the least-bad one, and
see precisely what a human-sponsored exchange account would save you."
That is still a sellable answer (arguably a more honest one than any
competitor gives — it saves an agent from discovering this by losing
money), but the response must segment routes by accessibility —
`wallet_only` vs `requires human-owned KYC'd exchange account` — not
rank them as if every payer could use every row. It also raises the
stakes on proving Allbridge one way or the other (§1.2): if it works, it
is not just the cheapest rail, it is the only *complete* wallet-only
one.

A third rail, **Allbridge Core** native-USDC bridging to Algorand, was
*announced* (partnership 2025-09-30, claimed live 2026-01-16) and would be
the best rail on paper (0.3% pool fee, self-hostable quote API) — but no
independent confirmation that the integration actually functions could be
obtained: every launch source traces back to Allbridge's own blog, a
partnership press release, or Algorand Foundation reposting it, and their
hosted API did not respond from either box that tried (this session and
an independent retry — DNS/connect failure both times; the live app is a
JS SPA whose chain selector could not be confirmed either). Until a real
quote round-trips through their API or app, Allbridge belongs in the
product's `excluded[]` list with an "unconfirmed" reason (§1.2, §2) —
worth re-checking at build time, not a peer option today.

The product's honest value is exactly this comparison: an agent asking
"cheapest way to move 500 USDC from Base to Algorand right now" gets a
ranked answer with per-leg costs, each cost labelled by data grade
(live quote / static estimate) and timestamp. That is sellable, current,
and computable from sources verified below.

---

## 1. Rail inventory — what actually exists in September 2026

Everything in this section was verified during this research session
(2026-09-04) by live API call, fetched primary source, or dated
announcement, except where explicitly marked *unverified*.

### 1.1 Exchange rails: native USDCa deposit/withdrawal (confirmed)

- **Kraken** — "USDC deposits and withdrawals now available on Algorand!",
  Kraken's own blog plus Algorand Foundation announcement and PR Newswire
  release dated **2026-01-22**. Confirmed: deposit, withdraw, hold, spend
  USDCa on Kraken and the Krak app; Kraken also runs an Algorand node.
  Sources: blog.kraken.com (product/new-features), algorand.co/blog
  "usdc-on-algorand-now-available-on-kraken", prnewswire 2026-01-22.
- **Coinbase** — **claimed, not first-party-confirmed.** The launch claim
  rests on an Algorand Foundation post (X, mid-2024): "USDC on Algorand
  is live on @Coinbase! Now, Algorand users can deposit and withdraw their
  USDCa directly on Coinbase," plus third-party how-to guides (e.g.
  Lofty's help center). A targeted search of coinbase.com /
  help.coinbase.com found no Coinbase-authored announcement or asset-page
  statement that the exchange supports the Algorand network for USDC
  send/receive (their USDC marketing page notes USDC "lives natively on"
  Algorand, which is a statement about Circle's issuance, not about
  Coinbase's deposit/withdrawal rails). This does not match the Kraken
  standard (Kraken's own product blog) — treat Coinbase as unconfirmed
  until its own asset/network documentation or app confirms the rail,
  and rank it accordingly (estimate-grade at best, `status: unconfirmed`
  in the rails inventory).
- **Binance** — official announcement "Binance Completes Integration of
  USD Coin (USDC) on Algorand Network, Opens Deposits and Withdrawals",
  dated **2022-11-30** (fetched and date-checked — note this integration
  *post-dates* Binance's September 2022 auto-conversion episode).
  Whether the ASA wallet is enabled *today* was not verified — Binance
  suspends individual network wallets routinely; a builder must check the
  live asset-config page at build time and the product must treat
  per-exchange availability as a config-maintained, dated fact, not a
  constant.

Because both endpoints of the corridor speak native USDC, an
exchange-rail route is: deposit USDCa (Algorand finality ~3s, exchange
credit typically fast for Algorand's instant finality) → internal balance
→ withdraw USDC on the destination network for that network's withdrawal
fee. Reverse direction is symmetric.

**Fee data problem (important):** exchange withdrawal fees are dynamic and
are NOT available from any public unauthenticated API. Kraken's and
Binance's fee endpoints require an authenticated account API key;
Coinbase passes through network fees. Third-party fee aggregators exist
(withdrawfees.com, coinmarketfees.com — both claim near-real-time refresh
via exchange APIs) but scraping them for resale would repeat the exact
ToS trap documented in `docs/x402-flight-search-analysis.md`. Numbers
found this session even conflict (Kraken USDC-on-Ethereum reported as
0.6 USDC by one aggregator and ~$2.50 by another). Consequence for the
design: **exchange-leg fees are estimate-grade config data with an
`as_of` date, owned in `config.py` and refreshed by a human or an
authenticated read-only exchange key — never presented as a live quote.**
(A read-only exchange API key held server-side for *fee lookup only* is
not custody — no funds, no withdrawal permission — but it is an
operational secret and an owner decision, so v0 ships without it.)

### 1.2 Allbridge Core: native-USDC bridge — ANNOUNCED, NOT CONFIRMED FUNCTIONING

- Partnership announced 2025-09-30 (Decrypt, DL News, chainwire,
  algorand.co); Allbridge and the Foundation **claim** Algorand went live
  on Allbridge Core on 2026-01-16, enabling native USDC transfers between
  Algorand and ~15 other chains (Solana, Ethereum, Base, Sui, Stellar
  among them), no wrapped assets. **Every one of those sources is
  Allbridge's own blog, the partnership press release, or Algorand
  Foundation republishing it — none independently demonstrates the
  integration functions.** Attempts to verify directly failed: the hosted
  API was unreachable from two independent vantage points (details
  below), and the live app is a JS SPA whose chain selector could not be
  inspected without a browser session. Treat as **unconfirmed** until a
  real quote is obtained from their live API or app.
- Fees (from docs-core.allbridge.io, fetched): pool-based transfers pay
  "a total fee of 0.3% ... 0.15% on the sending side and 0.15%" on the
  receiving side, plus a relayer fee varying with destination gas, plus
  pool price impact; CCTP/OFT-backed routes pay "a flat 0.1% fee" plus
  relayer fee, with no pool impact. The Algorand side is necessarily
  pool-based (Algorand has no CCTP — given in the brief and consistent
  with everything found).
- **API**: Allbridge publishes an open-source, self-hostable REST API
  (github.com/allbridge-io/allbridge-core-rest-api) with exactly the
  endpoints this product needs: `GET /bridge/receive/calculate` (tokens
  received for a given send), `GET /bridge/send/calculate` (inverse),
  `GET /gas/fee`, `GET /bridge/details`. Self-hosting sidesteps
  hosted-API ToS/rate questions.
  Their hosted endpoint (`core.api.allbridgecoreapi.net`) did not respond
  from this dev box (connection failed outright), and an independent
  retry from a second box hit the same failure (DNS timeout); the calc
  endpoints were therefore **never exercised live**. The self-hostable
  repo exists and is documented, but until a real calculate round-trip
  succeeds (hosted or self-hosted) — and specifically returns an
  Algorand-legged quote — nothing here counts as verification that the
  Algorand corridor works.
- **Health/volume caveat:** Allbridge itself celebrated reaching only
  ~$600k *all-time* bridging volume on Algorand (per 2026 reports —
  self-reported, same sourcing caveat as everything above). Even if the
  corridor is proven live later, it is thin and young; a graduated
  `allbridge_direct` route must carry this as a `warnings[]` item.

### 1.3 Wormhole / Portal (exists; not a USDC route worth ranking)

Wormhole has supported Algorand since May 2022, and Algorand Foundation
announced Wormhole **Native Token Transfers (NTT)** on Algorand in July
2025. Both confirmed. But NTT is an issuer-adopted standard for specific
tokens; nothing found indicates Circle's USDC uses NTT on Algorand.
USDC moved through classic Portal arrives as *wrapped* Wormhole USDC —
a different ASA from native USDCa with (as far as could be determined)
no meaningful DEX liquidity on Algorand. *Confidence: the wrapped-vs-
native mechanics are well-established Portal behaviour, but current
wrapped-USDC ASA liquidity on Algorand was not directly measured this
session — verify via the Vestige pools endpoint before final copy.* For
v0: list Portal as "exists, not recommended for USDC; delivers a wrapped
asset with poor exit liquidity," don't rank it.

### 1.4 Messina bridge: compromised, never recommend (confirmed)

Messina's cross-chain bridge was exploited on **2026-03-13/14** — a token
multiplication flaw in the bridge's `hop()` function let an attacker
forge transfer approvals and drain ~475M OPUL from escrow vaults on
Arbitrum, Ethereum and BSC (Opulous published a full forensic report at
opulous.org/hack-report; the Opulous staking platform shut down after
2026-04-30). Messina.one itself still operates (mALGO staking was being
promoted by the Foundation in January 2025). Design consequence: Messina
must appear in the product, if at all, only as an explicit
`status: "compromised_2026-03"` warning row. A routing product that
silently omits a known-exploited bridge is less useful than one that
names it and says why it's excluded.

### 1.5 The Algorand DEX leg: live-quotable today (verified by live calls)

Two independent aggregators expose free, keyless, hosted quote APIs, both
exercised successfully during this research:

- **Vestige** (`api.vestigelabs.org` — note: the older documented host
  `free-api.vestige.fi` is **dead**, Cloudflare 530 "Origin DNS error";
  docs at about.vestige.fi still point at it). OpenAPI-documented
  endpoints include `GET /swap/v4` (best aggregated route),
  `GET /swap/v4/fee`, `GET /pools`, `GET /assets/price`. Live quote
  obtained: `from_asa=31566704` (native USDCa) `to_asa=0` (ALGO),
  `mode=sef`, returning amount out, per-provider split, network fee and
  `price_impact`. Docs state free-tier endpoints are cached ≥1 minute;
  premium uncached access exists (contact team@vestige.fi). No explicit
  resale prohibition found in their docs ("FREE API to any projects
  building in the Algorand Ecosystem"), but the docs page is thin —
  **ToS must be confirmed with Vestige before launch** (flight-search
  lesson: check before, not after).
- **Folks Router** (`api.folksrouter.io/v2/fetch/quote`, base URL read
  from their official SDK source; keyless for the standard tier, `/pro`
  tier takes an `x-api-key`). Live quote obtained for the same pair.
  Folks Router charges a 0.1% fee *on executed swaps* (docs.folks.finance,
  quoted: "Folks Router charges a 0.1% flat fee on the swap output
  amount") — quoting is free; the fee matters only as a cost line in the
  route we report. No published ToS for the quote API found — same
  pre-launch confirmation needed as Vestige.
- Tinyman and Pact both ship SDKs (tinyman-py-sdk, pact-py-sdk) that
  compute quotes from on-chain pool state via algod — a fallback path
  that depends on no third party's web API at all, at the cost of more
  code. Not needed for v0 given two working aggregators, worth noting as
  the degradation path.

**Measured liquidity reality (live, 2026-09-04, USDCa→ALGO fixed-input
via Vestige `/swap/v4`):**

| size | amount out (ALGO) | price impact |
|---|---|---|
| $1,000 | 11,005.8 | 0.15% |
| $10,000 | 109,271.7 | 0.86% |
| $50,000 | 529,603.4 | 3.87% |

Cross-check: Folks Router quoted 10,990.5 ALGO for the same $1,000
(0.09% impact by its own measure) — the two aggregators agree within
~0.14%, which is exactly the kind of cross-source sanity check the
product should run per request. Context numbers, same day: Tinyman TVL
$5.57M, Pact TVL $1.25M (DefiLlama API), total USDCa lockup across
Vestige-tracked pools ~$1.66M, whole-chain Algorand TVL ~$30M. Against
the ~$751M/month USDC transacted volume from the June 2026 Algo Insights
report, the ~15x turnover tightness from the brief is corroborated: spot
books are thin, and any DEX-leg answer degrades fast with size — which
is precisely why a per-size, per-moment quote is worth paying for.

### 1.6 KYC accessibility — does the exchange rail even exist for an unverified agent?

This product's actual customers are autonomous x402 agents: wallet-only,
no persistent human identity, no KYC. That makes "can the payer actually
use this rail?" as load-bearing as its fee. Checked per exchange, same
first-party sourcing standard as the rest of this doc:

- **Binance: no non-KYC tier, full stop.** Binance's own announcement
  ("Important Changes About Binance Identity Verification", August 2021,
  binance.com/en/support/announcement) made Intermediate verification —
  government ID plus facial verification — mandatory for **all** products
  and services, explicitly including crypto deposits, trades and
  withdrawals; unverified legacy accounts were cut to withdraw-only
  during 2021. First-party, unambiguous.
- **Coinbase: no non-KYC tier.** Coinbase's help center
  (help.coinbase.com "Verify your identity on Coinbase", id-doc
  verification article) requires a valid government identity document
  (legal name, DOB, address, document number) plus camera capture as a
  baseline account requirement. No deposit/withdraw-without-ID tier is
  documented. First-party. (This rail was already flagged unconfirmed
  for USDCa anyway, §1.1.)
- **Kraken: the lightest requirements of the three, but still a real
  person's identity.** Kraken's own support pages state crypto deposits
  and withdrawals are "available to all levels," and its base tier
  (Starter, most non-US regions) collects name, date of birth, physical
  address and phone number — no government ID *document* at that tier.
  *Sourcing caveat:* support.kraken.com timed out repeatedly from this
  box for automated fetches (bot/datacenter-IP detection, not a
  geo-block — confirmed by the owner loading the same page successfully
  on a phone); the tier details above come from Kraken's own support
  articles as surfaced in search excerpts, corroborated by a third-party
  2026 explainer (cryptsy.com). **Base-tier (Starter) crypto withdrawal
  limit: $5,000/day — confirmed directly by the owner reading Kraken's
  own "Deposit and withdrawal limits by verification level" page
  (2026-09-04).** This resolves former open question 8: the doc's $1k
  and $10k comparison sizes fit comfortably under a Starter account; the
  $50k size does not and requires Intermediate/Pro verification (full ID)
  regardless of which route is cheaper — the response must say so rather
  than silently price a route the payer's account tier can't execute.
  "No ID document" is not "no KYC," either: Starter still binds a real
  person's PII (name/DOB/address/phone) under Kraken's ToS.
- **Programmatic account creation: none found.** No exchange of the
  three documents a public API for creating and verifying an account
  programmatically; onboarding products that do exist (institutional /
  embedded offerings) sit *behind more* KYC/KYB, not less. Absence is
  hard to prove exhaustively, but the conclusion is safe: **an agent
  cannot mint itself an exchange account.** The only realistic model —
  name it plainly — is **"human-owned, pre-KYC'd, agent-operated"**: the
  agent's owner completes KYC once as themselves, then hands the agent a
  scoped API key (withdrawal permission plus a pre-approved address
  allowlist, standard API-key features on Kraken and Binance). The
  account, the liability and the ToS relationship stay with the human;
  the agent is an operator, not a customer.

**Design consequence:** every route in the product carries an `access`
field — `"wallet_only"` (DEX+ALGO leg; a proven bridge later, §1.2) vs
`"human_kyc_account"` (every exchange rail) — and an optional request
param (`access=wallet_only`) filters to what the payer can actually
execute. Ranking never mixes the classes as peers: the top-level answer
is "best executable route for you" first, "what a human-sponsored
account would save" second. §2 and §5 reflect this.

---

## 2. What the endpoint computes

One paid call answers: *"I have N USDC on chain A and want USDC on chain
B (one of A/B is Algorand). What are my current options, what does each
really cost end-to-end, and how long does each take?"* Both directions
are first-class (owner scope note, this session): `from_chain=algorand&
to_chain=base` and `from_chain=base&to_chain=algorand` are the same code
path with source/destination swapped, and the response shape is
identical either way.

Candidate routes generated per request:

1. **exchange_rail** (one row per exchange in the config table) —
   deposit USDCa / withdraw USDC-on-B (or the reverse). Cost = config
   fee table (withdrawal fee for the destination network, deposit free)
   → `data_grade: "estimate"`, always with `as_of`. Only exchanges whose
   rail is first-party-confirmed rank; Coinbase stays
   `status: unconfirmed` in the inventory until §1.1's flag clears.
   `access: "human_kyc_account"` (§1.6): only executable through a
   human-owned, pre-KYC'd account the agent has been given credentials
   for — the response says so on every exchange row, and these rows never
   outrank a wallet-only route in the headline answer; they appear as
   "what a sponsored account would save."
2. **dex_plus_algo_leg** — swap USDC↔ALGO on-chain (live aggregator
   quote, size-specific price impact + aggregator fee + network fee),
   move ALGO through an exchange, convert on the far side. Carries an
   explicit `volatility_exposure: "ALGO in transit"` warning. DEX leg is
   `"quote"`, exchange leg `"estimate"`. Note the ALGO leg *also* ends at
   an exchange, so the pure form of this route inherits the same §1.6
   precondition — the only fully `access: "wallet_only"` variant today is
   the on-Algorand DEX swap itself plus whatever wallet-native off-ramp
   exists on the far side; the route generator must be honest about where
   each variant's wallet-only reach ends.
3. **excluded[]** — rails deliberately not ranked, each with a reason:
   Allbridge Core (announced 2025-09-30 / claimed live 2026-01-16, but no
   independent confirmation found that the integration actually functions
   — treat as unconfirmed until a real quote is obtained from their live
   API or app), Portal (wrapped asset, thin exit liquidity), Messina
   (exploited 2026-03), anything else found dead. Naming what we excluded
   and why is part of the product's trust story. If Allbridge is later
   proven live (§1.2), it graduates into the ranked list as
   `allbridge_direct` (0.3% pool fee or 0.1% flat CCTP/OFT-backed, +
   relayer fee + pool impact, quote-grade via their calculate API) — the
   design accommodates it, the ranking today does not include it.

Routes are ranked by net effective cost (USDC out per USDC in) with ETA
as a tiebreak field, never hidden. Every leg carries `data_grade`
(`quote` | `estimate` | `static`), `as_of` (UTC), and `source`. The
top-level response repeats the worst data grade present in the winning
route so an agent can gate on it programmatically.

Sketch of the response body (abridged):

```json
{
  "direction": {"from_chain": "base", "to_chain": "algorand", "asset": "USDC"},
  "amount_in": "500.00",
  "routes": [
    {
      "kind": "exchange_rail",
      "venue": "kraken",
      "access": "human_kyc_account",
      "amount_out_est": "497.50",
      "net_cost_pct": 0.5,
      "eta_minutes": [5, 60],
      "legs": [
        {"step": "deposit", "venue": "kraken", "network": "base",
         "fee_usdc": "0.00", "data_grade": "estimate",
         "as_of": "2026-09-01", "source": "config fee table"},
        {"step": "withdraw", "venue": "kraken", "network": "algorand",
         "fee_usdc": "2.50", "data_grade": "estimate",
         "as_of": "2026-09-01", "source": "config fee table"}
      ],
      "warnings": ["exchange fees are dated estimates, not live quotes"]
    },
    {"kind": "dex_plus_algo_leg", "access": "human_kyc_account",
     "wallet_only_reach": "on-Algorand swap leg only", "data_grade": "quote",
     "...": "..."}
  ],
  "wallet_only_summary": "No confirmed fully wallet-only route exists for this corridor today; see excluded[] and each route's access field.",
  "excluded": [
    {"venue": "Allbridge Core", "reason": "announced 2025-09-30 / claimed live 2026-01-16, but no independent confirmation found that the integration actually functions — treat as unconfirmed until a real quote is obtained from their live API or app"},
    {"venue": "Messina", "reason": "bridge exploited 2026-03-13, do not use"},
    {"venue": "Portal/Wormhole", "reason": "delivers wrapped USDC, thin exit liquidity"}
  ],
  "disclaimer": "Informational quote only. We execute nothing, custody nothing, and are counterparty to nothing.",
  "settlement_tx_id": "..."
}
```

## 3. Freshness honesty — how "live" the answer really is

- **DEX leg: genuinely live**, minus Vestige's ≥1-minute free-tier cache.
  Two independent sources allow a per-request cross-check; if they
  diverge beyond a threshold (say 1%), say so in `warnings[]`.
- **Allbridge leg: not served in v0** — excluded as unconfirmed (§1.2,
  §2). If later proven live, it would be quote-grade when their
  calculate API answers.
- **Exchange leg: never live** in v0. It is a curated config table with
  dates, and the response says so. This is the single biggest honesty
  constraint of the product and it must be visible in the payload, in
  the endpoint description, and in the Bazaar discovery text — an agent
  paying $0.005 must not believe it bought a live Kraken fee quote.
- **ETAs: coarse estimates always** (Algorand finality ~3s is the only
  hard number in the chain; exchange credit/withdrawal processing and
  bridge relay times are ranges).

Infrastructure this actually needs, compared honestly against the
existing products: **2–3 outbound HTTP calls per paid request** (Vestige
+ Folks; +1 for Allbridge only if it graduates from `excluded[]`;
DefiLlama TVL can be cached daily),
against `x402_uptime`'s 1 and `x402_news`'s 0. A Redis quote cache
(30–60s TTL, keyed by direction+size bucket, modeled directly on
`x402_uptime/services/cache.py`'s fail-soft pattern) keeps repeat load
off the upstreams. **No new datastore** (Cassandra/Redis only rule
holds: nothing here needs a store at all beyond the shared settlement
ledger — like `x402_uptime`, gate the product on a bool setting, not a
`store_setting`). **No workers/ beat is required for v0** — everything
is computable on demand; a periodic fee-table-staleness reminder or a
daily TVL snapshot beat is optional later. Net: moderately more ongoing
surface than any existing x402 product because of the *curated exchange
fee table* — that table needs a named refresh routine (human or
authenticated key, §1.1) or its `as_of` dates will silently rot. This is
the product's real recurring cost; flagging it per the brief.

## 4. Architecture sketch (established patterns, no new machinery)

New module `backend/app/modules/x402_liquidity/` (name open — see §7):

```
x402_liquidity/
  __init__.py
  api/routes.py        # register_x402_liquidity_routes(app)
  services/
    routing_service.py # candidate generation + ranking (pure logic, injected fetchers)
    sources.py         # vestige/folks fetchers (allbridge only if it graduates,
                       #   §1.2), httpx, hard timeouts,
                       #   error -> {"error": ...} per CLAUDE.md §2.8, never []
    cache.py           # Redis quote cache, fail-soft, uptime-cache pattern
    rate_limit.py      # free preview limiter (CLAUDE.md §9: every free endpoint)
```

Routes (mirroring `x402_news/api/routes.py` line-for-line in shape):

- `GET /api/v1/x402/liquidity/route` — **paid**. Query params:
  `from_chain`, `to_chain` (exactly one must be `algorand`; the other
  from a config-owned allowlist — base, ethereum, solana, sui, stellar,
  polygon, arbitrum to start), `amount` (USDC, bounded, e.g. 1–1,000,000),
  optional `include=dex_leg`, optional `access=wallet_only` (§1.6 filter
  — restrict the answer to routes executable from a bare wallet, with
  the `wallet_only_summary` still present either way). All validation
  *before* the payment gate
  (a malformed request is a 400 and never charged — the news-route
  discipline). `circuit_breaker.is_tripped` checked pre-gate. Then
  `require_paid_request(price=settings.x402_liquidity_route_price,
  resource="x402-liquidity-route", extensions=describe_json_endpoint(...))`,
  product work inside `run_with_refund` (all live sources failing raises
  → refund; *partial* degradation — e.g. Folks up, Vestige down — serves
  with `warnings[]` and is still a fulfilled product), then
  `mark_fulfilled`. `?preview=true` (redacted: route kinds and excluded[]
  visible, numbers withheld, rate-limited per IP) and promo support,
  copied from the ping/uptime reference.
- `GET /api/v1/x402/liquidity/rails` — **free, rate-limited**: the static
  rail inventory (which exchanges/bridges exist, status flags, fee-table
  `as_of` dates, the Messina/Portal exclusions with reasons). Free for
  the same reason probe history is free (owner call 2026-08-31): the
  trust-signal layer costs nothing to serve and markets the paid quote.

Config (one owner, `backend` `Settings` in `app/core/config.py`, new
`── x402 liquidity routing ──` block): `x402_liquidity_enabled: bool =
False` (Product gate via `bool_setting`, like uptime — prototype until
sources are proven from prod), `x402_liquidity_route_price`,
`x402_liquidity_chains` allowlist, `x402_liquidity_quote_cache_ttl_seconds`,
`x402_liquidity_rate_limit_per_hour`, upstream base URLs + timeouts, and
the exchange fee table (JSON string setting with per-entry `as_of`, or a
small checked-in data module read through config — either way
config-owned, never `os.getenv` in the module). Catalog: one `Product`
entry in `x402_catalog/services/catalog.py` `PRODUCTS`, two
`CatalogRoute`s, `store_setting=None`, `bool_setting=
"x402_liquidity_enabled"`, and registration wired in
`falcon_main.py::_register_x402_routes` like the other eleven modules.

Invariants that bite here, called out for the builder: sources.py
helpers return `{"error": ...}` on failure, never an empty list a ranker
would read as "no routes exist" (§2.8); Redis cache reads/writes fail
soft (§2.9); outbound calls have hard timeouts and bounded response
reads (§4); no SSRF surface exists because upstream hosts are a fixed
config allowlist, never caller-supplied — keep it that way.

## 5. Pricing recommendation

Marginal cost per call is a few free-API HTTP round-trips — near zero,
like news search — but unlike news search the answer depends on curated,
maintained data (the fee table, the rail-status inventory) and on 2–3
external calls per request, like a lighter `x402_scan`. Calibrating
against the live roster: `x402_news_search_price` $0.001 (zero external
work, own data), `x402_uptime_price` $0.001 (one external call),
`x402_scan_price` $0.01 (real sandboxed external work),
`x402_grading_score_price` $0.03, `kyc_lookup_price` /
`x402_features_demand_price` $0.05 (curated aggregate reads).

**Recommend `x402_liquidity_route_price = "$0.005"`** — 5x the
single-external-call floor for a multi-source aggregation with curation
burden, still trivially affordable against the money it saves (one
avoided 0.86% slippage surprise on a $10k move is $86; the quote is half
a cent). The free `/rails` route plus redacted `?preview=true` follow
the established funnel (free trust layer → cheap paid quote). Multi-asset
`accepts` from day one per §9's constraint; USDC is the enabled asset.

§1.6 does not change the price, but it changes what the price buys, and
the endpoint description must say it plainly: for a wallet-only agent
the paid answer is frequently "no fully wallet-only route exists for
this corridor today — here is the least-bad executable option, priced
per your size, and here is what a human-sponsored exchange account
would save." That negative-but-quantified answer is a fulfilled product
(the same way an uptime check that says "it's down" is), not a refund
case; the description and Bazaar discovery text must set that
expectation before payment, so the answer never reads as a failure to
deliver.

## 6. What this explicitly does NOT do (non-negotiable boundary)

- **No custody, ever.** We never hold, receive, forward, or escrow any
  user asset. The x402 payment for the quote itself goes to the existing
  receive-only `payTo` — that is the only value that ever touches us.
- **No execution.** We never build, sign, relay, or submit a swap,
  bridge, deposit, or withdrawal transaction for the payer — not even
  "unsigned transactions for convenience." (Both aggregator APIs offer
  transaction-building endpoints; we deliberately do not call them.
  Returning ready-to-sign transaction payloads is the top of the
  scope-creep slide toward being an execution venue — out.)
- **No counterparty role.** We are principal to nothing: no quotes *we*
  fill, no inventory, no spread capture, no atomic-swap leg.
- **No bridge.** The shelved custodial-bridge idea stays shelved; nothing
  here locks, mints, or pools funds. If a future change would require any
  of the above, it is a different product needing the legal opinion first.
- **No affiliate/referral execution links** without a separate owner
  decision (Folks Router's API accepts a `referrer` param — using it
  would entangle us with execution economics; not in v0).
- Quotes are informational, not offers; the response `disclaimer` field
  says so on every call.

## 7. Open questions (flag-and-stop items before build)

1. **ToS confirmations** (the flight-search lesson, do these *first*):
   Vestige free-API commercial-resale terms (none found published — ask
   team@vestige.fi); Folks Router hosted-API terms (none found);
   Allbridge hosted-API terms (moot if we self-host their open-source
   REST API, which is the recommended path).
2. **Allbridge is excluded until proven** (§1.2, §2) — the entire
   integration is announcement-only; their hosted API failed from two
   independent boxes. Graduation condition: a real Algorand-legged
   calculate round-trip from the prod host or a self-hosted instance
   (or a confirmed transfer in their live app). Until then it stays in
   `excluded[]`, never ranked.
3. **Exchange fee-table ownership** — who/what refreshes it, and is a
   read-only authenticated exchange key (fee lookup only) acceptable to
   the owner? V0 works with hand-curated dated values either way.
4. **Module name** — `x402_liquidity` used above; `x402_corridor` or
   `x402_transfer_routes` are alternatives. Cosmetic, but catalog keys
   and resource strings are forever-ish once agents integrate.
5. **Where this sits in the §9.1 roadmap** — it is a new item (closest
   kin: 14 and 22 in shape). Needs an owner line in CLAUDE.md when/if
   approved so the next agent doesn't re-derive it.
6. **Binance USDCa wallet status today** (§1.1) — verify at build time.
7. **Wrapped-USDC liquidity claim** (§1.3) — measure via Vestige `/pools`
   before shipping the exclusion copy, so the stated reason is a number.
8. ~~**Kraken base-tier details**~~ — **resolved 2026-09-04**: withdrawal
   limit confirmed at **$5,000/day** for the Starter tier, read directly
   by the owner from Kraken's own limits page (automated fetches from
   this box remain blocked by bot detection, not a geo-block — confirmed
   by the owner loading the same page successfully on a phone). $1k/$10k
   sizes fit; $50k does not and needs full-ID verification regardless of
   route cost.
9. **The human-sponsored-account model** (§1.6) — presenting
   `human_kyc_account` routes at all presumes the payer might have a
   human sponsor. Whether to also publish a short "how to sponsor your
   agent" explainer (owner sets up the exchange account, agent gets a
   withdrawal-scoped API key with an address allowlist) is a product
   copy decision for the owner — it markets the comparison rows but
   edges toward advice; nothing custodial either way.

## Sources (primary ones used)

- Kraken/USDCa launch: blog.kraken.com "USDC deposits and withdrawals now
  available on Algorand"; algorand.co/blog/usdc-on-algorand-now-available-on-kraken;
  prnewswire.com 2026-01-22 release.
- Binance USDCa: binance.com/en/support/announcement/... (first-party,
  fetched; dated 2022-11-30 — current wallet status unverified).
  Coinbase USDCa: Algorand Foundation on X (status 1805675736767832315)
  and lofty.ai help article only — **no first-party Coinbase source
  found** (targeted coinbase.com/help.coinbase.com search came up
  empty); flagged unconfirmed in §1.1.
- Allbridge (all launch sources first-party/PR-derived — no independent
  confirmation, see §1.2): algorand.co/news + decrypt.co/342174 +
  chainwire 2025-09-30 partnership; allbridge.io/blog/core/discoverAlgorand;
  claimed live date 2026-01-16 via Bitget/Gate news summaries
  (secondary, republishing the same announcement);
  docs-core.allbridge.io fees page (fetched, quoted);
  github.com/allbridge-io/allbridge-core-rest-api.
- Wormhole: algorand.co/blog NTT announcement (July 2025);
  coindesk.com 2022-05-17.
- Messina exploit: opulous.org/hack-report (forensic report, exploit
  2026-03-13/14).
- Vestige: about.vestige.fi/api/api-docs; live calls to
  api.vestigelabs.org (`/openapi.json`, `/swap/v4`, `/assets/price`,
  `/pools`) on 2026-09-04; free-api.vestige.fi observed dead (CF 530).
- Folks Router: docs.folks.finance/functionalities/folks-router (0.1%
  fee, quoted); github.com/Folks-Finance/folks-router SDK source (base
  URL `api.folksrouter.io`); live call to `/v2/fetch/quote` 2026-09-04.
- TVL: api.llama.fi `/tvl/tinyman`, `/tvl/pact`,
  `/v2/historicalChainTvl/Algorand`, 2026-09-04.
- Liquidity context: algorand.co June/July 2026 Algo Insights reports.
- KYC tiers (§1.6): Binance "Important Changes About Binance Identity
  Verification" (binance.com/en/support/announcement, Aug 2021,
  first-party); Coinbase "Verify your identity on Coinbase"
  (help.coinbase.com id-doc-verification, first-party); Kraken support
  articles "Verification levels explained" / "Verification level
  requirements" / "Deposit and withdrawal limits by verification level"
  (support.kraken.com — first-party pages, but unreachable from this box:
  content via search excerpts, corroborated by cryptsy.com's 2026
  explainer; re-confirm at build time, open question 8).
