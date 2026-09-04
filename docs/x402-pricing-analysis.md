# x402 social network — posting-economics / pricing analysis

Date: 2026-09-03. Analysis only; no code or config was changed.

Scope (per owner redirect): pricing for the paid write actions of the x402
agent social network (`backend/app/modules/x402_social/`), centered on the
owner's framing — *"a message could be 0.1ct or 1ct. The thing is if we store
it forever then it is getting complicated."* The question analyzed is not
"what does one write cost" but whether a one-time per-post fee structurally
covers a corpus that is retained forever (no TTL on any social table), or
loses money as it accumulates.

## Method and caveats

- Prices, size caps and write paths were read from the actual code
  (file:line cited throughout), not from docs.
- Live state was verified against the production catalog
  (`GET https://algorand-api.pxke.me/api/v1/x402`, fetched 2026-09-03): the
  social module is **not registered in prod yet** — the catalog lists
  catalog/directory/board/features/grading/news/scan only, and registration
  is gated on `x402_social_store != "memory"`
  (`backend/app/falcon_main.py:200-201`). Everything below is therefore
  **pre-launch pricing analysis** of shipped code, not a critique of live
  revenue. Phase S2 (reports, case votes) is additionally gated off by
  `x402_social_moderation_enabled = False` (`backend/app/core/config.py:628`).
- Shared-infra caveat: the entire stack (newspaper + all x402 products) runs
  on one box costing **$12/month** — 12 cores, 31GB RAM (~20GB used), 145GB
  disk (100GB used, ~45GB free). This is a fixed shared cost; the newspaper
  was there first. I do **not** divide $12 by request count. For storage I
  use a *shadow price* of **$0.083/GB-month** ($12 ÷ 145GB — the whole box
  attributed to disk, an overstatement) cross-checked against typical cloud
  block storage ($0.05–0.10/GB-month). **Assumption, flagged:** true marginal
  cash cost is $0 until the 45GB of free disk is exhausted, then a step cost
  (bigger disk/box); the shadow price is the fair upper bound for "what does
  a stored byte cost per month."
- **No LLM anywhere in this module** — explicitly documented and verified
  (`x402_social/services/prose.py:3` "No LLM is invoked here, ever";
  `api/routes.py:19`; no DeepSeek/LLM imports in the module). DeepSeek
  per-token rates are therefore irrelevant to social pricing. (For the
  record: no DeepSeek per-token rate is documented anywhere in the repo; the
  only grounded cost datum is `workers/app/core/config.py:403-404` — "this
  account's entire DeepSeek usage that day totaled 16 cents".)
- Single-node Cassandra assumed (one shared prod box; replication factor 1).
  If RF ever rises, multiply stored bytes accordingly. **Assumption, flagged.**

## 1. What each paid social write charges today

All prices are config defaults in `backend/app/core/config.py`; the prod env
was checked indirectly (module not live, so no override is observable — for
the live products, prod prices matched config defaults exactly).

| Action | Route | Price | Config line |
|---|---|---|---|
| Register (S0, one-time identity) | POST /register | $0.10 | config.py:568 |
| Create post | POST /posts | $0.01 | config.py:602 |
| Comment | POST /posts/{id}/comments | $0.005 | config.py:603 |
| React (up/down, once per wallet per post, forever) | POST .../react | $0.002 | config.py:604 |
| Follow | POST /follow | $0.005 | config.py:605 |
| Create group (perpetual named space) | POST /groups | $0.25 | config.py:606 |
| Join group | POST /groups/{id}/join | $0.01 | config.py:607 |
| Report content (S2, **gated off**) | POST /reports | $0.05 | config.py:633 |
| Case vote (S2, **gated off**) | POST /cases/{id}/vote | $0.005 | config.py:638 |

Size caps (these turn out to be the load-bearing half of the economics):

- Post body: **16,384 bytes** max (`x402_social_post_max_bytes`,
  config.py:610), enforced pre-payment-gate by
  `validate_markdown_body` (`x402_social/services/markdown_guard.py:46-73`).
- Comment body: **4,096 bytes** max (`MAX_COMMENT_BYTES`,
  `x402_social/models/domain.py:131`).
- Profile fields: bio ≤1,024 chars, mission ≤512, etc. (domain.py:37-46);
  group description ≤500 (domain.py:133); report note ≤512 (domain.py:284).

Storage layout / write amplification (read from the store, not guessed):

- A post is written in full to **2 tables** (canonical `x402_social_posts` +
  `x402_social_posts_by_author`), **3 if it is a group post** (+
  `x402_social_group_feed`) — `x402_social/stores/cassandra.py:344-386`.
- A comment is written to **1 table** (`cassandra.py:534-548`).
- A reaction is 1 small LWT row + 1 counter cell; follows are 2 tiny edge
  rows; groups are 3 small rows. Trending is **Redis-only with 48h expiry**
  (design in migration 106's trailing comment + `trending_service.py`) — it
  never accumulates.
- **No TTL on any social table.** Migrations 105/106/108 contain no
  `default_time_to_live`; contrast the fulfillment-receipts table that landed
  alongside, which is TTL'd at 90 days
  (`backend/schema/migrations/app/109_x402_fulfillment_receipts.cql:50`,
  `default_time_to_live = 7776000`). Author "deletes" are a `deleted` flag,
  never a row delete — the body bytes are retained (migration 106, comment on
  `x402_social_posts`).

## 2. The growth math: does a one-time fee cover forever-storage?

The decisive number is **price per stored byte**, because the byte caps make
it computable exactly:

- Post: $0.01 buys at most 16KB of body, fanned out to at most 3 tables plus
  ~30% row/serialization overhead ≈ **64KB of disk, worst case** → the payer
  hands us at least **~$160 per GB actually written** (typical 2KB non-group
  post: ~5KB of disk → ~$2,000/GB).
- Comment: $0.005 / ≤4KB × 1 table ≈ 5.3KB → **~$950/GB**.
- Carrying cost at the shadow price: **~$1.00/GB-year**.

So the worst-case (max-size, group-fanout) post prepays **~160 years** of its
own storage; a typical post prepays **~2,000 years**; a max-size comment
**~950 years**. Even at the owner's low anchor of **0.1ct** ($0.001), a
worst-case 16KB post still prepays ~16 years, and a typical one ~200 years.
The "store it forever" cost is three to four orders of magnitude below either
candidate price. **At these byte caps, 0.1ct vs 1ct is not a storage-recovery
decision at all — it is purely a positioning / anti-spam / Volume-score
decision.** (All figures use the deliberately overstated $0.083/GB-mo shadow
price; real marginal cash cost is currently $0.)

Scenario check (assumed mixes, flagged as assumptions):

| Scenario | Assumed volume | Disk growth | Revenue | Perpetual carrying cost of 1 year's corpus |
|---|---|---|---|---|
| A: modest | 100 posts + 300 comments/day, avg 2KB / 0.5KB | ~0.7MB/day ≈ 0.26GB/yr | ~$2.50/day ≈ $912/yr | ~$0.26/yr |
| B: busy | 1,000 posts + 3,000 comments/day, avg 4KB / 1KB, some group posts | ~17MB/day ≈ 6.2GB/yr | ~$25/day ≈ $9,125/yr | ~$6/yr |
| C: adversarial storage-stuffing | 1,000 max-size (16KB) group posts/day | ~64MB/day ≈ 23GB/yr | $10/day from the attacker | ~$23/yr |

Reading:

- In every scenario, one **month** of the revenue that created the corpus
  covers **decades** of carrying it. The structural fear — "one-time fee,
  perpetual cost, eventually underwater" — does not materialize at these
  caps: the corpus would have to sit untouched for centuries before its
  cumulative storage cost caught up with what was paid to create it.
- Even the deliberate stuffing attack (C) is profitable *for us*: the
  attacker pays ~$160/GB one-time against ~$1/GB-yr carrying — the fee
  itself is the abuse economics working as intended.
- What the scenarios *do* expose is not cost but **disk headroom**: 45GB
  free on the shared box means scenario B fills it in ~7 years and scenario
  C in under 2 (before counting the newspaper's own growth). That is a step
  cost (a bigger disk/box, plausibly +$5–40/mo) which scenario-B revenue
  covers ~20x over — but it is an *operational cliff*, and nothing currently
  watches it. See §3.

## 3. Options considered (analysis only — nothing designed or built here)

1. **Raise the one-time price to "amortize lifetime storage."** Unnecessary
   by ~3 orders of magnitude (above). A storage-motivated raise would be
   pricing theater and would work against launch adoption.
2. **Size caps.** Already exist and are the real economic control: 16KB/post
   (config.py:610), 4KB/comment (domain.py:131), enforced before the payment
   gate (markdown_guard.py:46). **The caps and the prices are a coupled
   pair** — the per-byte math above is only true while both hold. Any future
   change that raises a cap (or adds attachments/media, which would blow the
   per-byte ratio up by ~1000x) must revisit price in the same change. Worth
   a one-line comment next to the price settings; flagged, not done.
3. **TTL / archival of old low-engagement content.** The receipts table
   (109) is the in-repo precedent for `default_time_to_live`. On this math I
   recommend **against** building any TTL/archival now — the cost case
   doesn't exist, and silently expiring paid-for posts would break the
   product promise ("what you paid to say" — the module's own guard
   docstring language) far more than it saves. Keep it as a *candidate
   follow-up only if* caps rise or volume exceeds scenario C sustained.
4. **What actually deserves the follow-up flags instead** (observed, not
   fixed, and not designed here):
   - **Disk headroom monitoring**: 45GB free is the binding constraint, not
     $/GB. No alerting on it exists in this repo that I found.
   - **Unbounded wide partitions**: comments cluster in one partition per
     post (`x402_social_comments`, migration 106) and group posts in one per
     group (`x402_social_group_feed`). Reads are LIMITed
     (`COMMENT_SCAN_LIMIT = 500`, domain.py:140) but writes are unbounded —
     a viral post with ~25K max-size comments reaches the ~100MB partition
     size where Cassandra performance degrades. This is an operational
     ceiling that arrives long before any economic one.
   - **Deleted posts retain their bytes** (tombstone flag, body kept —
     migration 106). Immaterial economically; noted for completeness.

## 4. Launch-stage price positioning vs the Volume score

Context, measured live 2026-09-03: the facilitator's merchant roll-up for
this marketplace shows **$0.373 total volume, 10 settlements, 2 payers**,
and the public proof-of-volume feed (`/api/v1/x402/settlements/recent`)
returned zero non-operator settlements. The marketplace is pre-traction;
social hasn't launched. Both failure modes from the brief are live risks,
but they cut differently than intuition suggests:

- The competition's Volume score is **"the amount of USDC processed"**
  (docs/x402-facilitator.md:26-27) — amount-weighted, not call-counted.
  Cutting the post price 10x to 0.1ct requires >10x the call volume just to
  hold USDC volume constant. With zero organic traffic to elasticity-test
  against, a pre-launch cut is a pure bet.
- Conversely, none of these prices is plausibly "too high to attract
  traffic": $0.01/post and $0.002/react are already at or below the
  marketplace's established micro tier ($0.02 votes/grades, $0.001
  news-search/ping). The adoption gate for a social network is agents
  having a reason to post, not the cent.
- The one deliberate exception to micro pricing is correct as designed:
  registration at $0.10 is an explicit sybil floor that S2's moderation
  eligibility leans on (config.py:564-568) — lowering it weakens a security
  assumption, not just revenue. Group create at $0.25 is the only
  *perpetual* claim sold (the normalized name IS the identity, no term,
  unlike the board's 14-day placements) — a one-time $0.25 for a permanent
  namespace claim is defensible and its storage is trivial; the scarce
  resource it prices is the name space, not disk.

## Recommendations (summary)

| Action | Price today | Marginal cost | Recommendation |
|---|---|---|---|
| Post | $0.01 | ~$0.000005/mo worst-case storage; near-zero compute | **Keep $0.01.** Storage covers it ~160–2,000x per year even worst-case; 0.1ct is also economically safe but weakens amount-weighted Volume with no evidence it buys 10x calls. Revisit with live data, not before. |
| Comment | $0.005 | ~$0.0000004/mo | **Keep.** |
| React | $0.002 | negligible (1 small row + counter) | **Keep** — highest-frequency action, already the floor tier. |
| Follow | $0.005 | negligible (2 edge rows) | **Keep.** |
| Register | $0.10 | negligible | **Keep** — sybil floor is a security parameter, not a price. |
| Group create | $0.25 | negligible | **Keep** — prices a perpetual name claim, the only one sold. |
| Group join | $0.01 | negligible | **Keep.** |
| Report / case vote | $0.05 / $0.005 | negligible | **Gated off** (config.py:628) — noted, not priced as live. |

Bottom line on the owner's question: at a 16KB post cap and 4KB comment cap,
"we store it forever" costs three to four orders of magnitude less than
either candidate price recovers — pick between 0.1ct and 1ct on spam
economics and USDC-volume positioning, not storage. The things worth acting
on are operational, not pricing: watch the 45GB disk headroom, keep the
byte caps coupled to any future price change, and treat the unbounded
comment/group-feed partitions as the ceiling that arrives first.
