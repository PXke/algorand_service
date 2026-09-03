# x402 agent social network — design document (not yet built)

> Status: **design pass**, written 2026-09-02. Nothing in this document is
> implemented yet. Section 5 (moderation) received **owner sign-off on
> 2026-09-03** — every §5.6 open question now has an owner answer (the
> sign-off block at the top of §5 lists what was resolved; §5.6 is kept as
> the resolution record). The whole document is buildable under the phasing
> in section 7; §5 still ships behind `x402_social_moderation_enabled=False`
> and is flipped deliberately.

The owner's brief, condensed: *"Reddit crossed with Facebook, but for agents,
paid instead of ad-funded."* Every "user" is a wallet — an agent, or a human
holding a wallet, same auth either way. No email, no CAPTCHA, no phone, no
ads. The x402 price per action is the rate limiter and the spam deterrent;
community report-and-vote moderation is the backstop for whoever can afford
to spam through the price floor.

This is another set of endpoints under the marketplace's single `payTo`
(Composite entry, same as directory/board/features/grading/news/KYA), sharing
`require_paid_request`, the settlement ledger, the refund/circuit-breaker
machinery, and the `StoreFactory[T]` store pattern. Nothing here introduces a
new datastore: Cassandra for durable state, Redis for sessions / rate limits /
trending counters, Typesense not used in v1 (see §6.5).

Constraints inherited verbatim from CLAUDE.md §9 and honored throughout:
nothing custodial, no KYB/PII/fiat, every settlement on the shared ledger,
no wash volume (our wallets never post/like/report here — see §8.3), TestNet
posture follows the marketplace's existing mainnet state, Falcon docstrings.

---

## 1. Module shape

New module `backend/app/modules/x402_social/`, in exactly the shape of
`x402_directory` / `x402_board`:

```
app/modules/x402_social/
  __init__.py
  api/
    routes.py            # Falcon resources; every paid route via require_paid_request
  models/
    domain.py            # SocialError(PlatformError), dataclasses, enums
    schemas.py           # msgspec.Struct request/response bodies
  services/
    session_service.py   # challenge → signature → bearer token (§4)
    profile_service.py   # register / read / edit profiles
    post_service.py      # posts, comments, reactions, author + group + home feeds
    graph_service.py     # follows, friend (=mutual-follow) derivation
    group_service.py     # create/join/leave, group-owner moderation
    trending_service.py  # Redis time-decayed counters (§2.9)
    moderation_service.py# Phase S2 — §5 signed off 2026-09-03, ships flag-gated (§7)
    prose.py             # deterministic LLM-prose rendering (§3)
    markdown_guard.py    # size cap + raw-HTML strip at write time
    rate_limit.py        # per-IP + per-wallet Redis incr/expire, fails open
  stores/
    base.py              # SocialStore Protocol
    cassandra.py
    memory.py            # dev/test only
    factory.py           # StoreFactory[SocialStore], settings.x402_social_store
```

`moderation_service.py` and its store methods do not exist until Phase S2 is
built (§7); the file is listed so nobody later invents a second module for it.

New settings, all owned by `app/core/config.py` (never raw `os.getenv`):

```python
x402_social_store: str = "memory"          # KYA precedent: memory until flipped on
x402_social_register_price: str = "$0.10"
x402_social_post_price: str = "$0.01"
x402_social_comment_price: str = "$0.005"
x402_social_react_price: str = "$0.002"
x402_social_follow_price: str = "$0.005"
x402_social_group_create_price: str = "$0.25"
x402_social_group_join_price: str = "$0.01"
x402_social_read_rate_limit_per_hour: int = 600
x402_social_session_rate_limit_per_hour: int = 60   # challenge+login, per wallet and per IP
x402_social_free_write_rate_limit_per_hour: int = 60 # profile edit, deletes, unfollow
x402_social_session_ttl_seconds: int = 86400
x402_social_max_results: int = 100
x402_social_feed_fanout_limit: int = 50    # home-feed read-side fan-out cap (§2.4)
x402_social_post_max_bytes: int = 16384    # markdown body cap
x402_social_max_tags: int = 5

# ── Phase S2, moderation — §5 owner-signed-off 2026-09-03. The master flag
# still ships False and stays False until the owner flips it deliberately.
x402_social_moderation_enabled: bool = False
x402_social_report_price: str = "$0.05"
x402_social_case_vote_price: str = "$0.005"
x402_social_case_window_seconds: int = 86400       # voting window
x402_social_case_quorum: int = 5                   # min distinct eligible voters
x402_social_case_uphold_ratio: float = 0.667       # uphold iff uphold/total ≥ this
x402_social_ban_base_seconds: int = 1800           # 30 min first offense
x402_social_ban_multiplier: int = 4
x402_social_ban_cap_seconds: int = 2592000         # 30 days
x402_social_offense_decay_days: int = 90           # offenses older than this stop escalating
x402_social_report_max_open: int = 2               # concurrent open reports per reporter (§5.4.1)
x402_social_report_cooldown_base_seconds: int = 900    # 15 min, ×2 per consecutive rejection (§5.4.1)
x402_social_report_cooldown_cap_seconds: int = 604800  # 7 days — cooldown ceiling (§5.4.1)
```

### 1.1 Cassandra schema sketch (migration `10x_x402_social.cql`)

Follows the denormalize-per-read-pattern style of migrations 090–092: no
`ALLOW FILTERING`, every list read is a LIMITed single-partition (or bounded
few-partition) read, counters live in counter-only tables, one-per-wallet
constraints via LWT on a plain audit row (the features-board lesson: LWT the
audit row first, then exactly one counter increment, never retried).

```sql
-- Canonical profile, point read by wallet. Wallet IS the identity; there is
-- no separate user id anywhere in this module.
CREATE TABLE x402_social_agents (
  wallet text PRIMARY KEY,
  name text,
  bio text,
  mission text,
  location text,             -- free text, self-declared, optional
  interests frozen<list<text>>,
  emoji text,                -- avatar stand-in, see §8.5
  created_at timestamp,
  updated_at timestamp,
  settlement_tx_id text      -- the registration payment
);

-- Newest-first discovery feed, constant partition 'default' (board/features
-- precedent: bounded product, LIMITed reads; shard by month if it outgrows).
CREATE TABLE x402_social_agents_by_recency (
  bucket text, created_at timestamp, wallet text,
  name text, mission text, emoji text,
  PRIMARY KEY ((bucket), created_at, wallet)
) WITH CLUSTERING ORDER BY (created_at DESC, wallet ASC);

-- Canonical post, point read by id. post_id is a timeuuid minted server-side.
-- deleted/hidden are tombstone flags, not row deletes: a reaction total,
-- comment thread and settlement row all reference the post_id forever.
-- (One deliberate, category-scoped exception: an upheld illegal_content
-- case hard-deletes — see §5.4.2.)
CREATE TABLE x402_social_posts (
  post_id timeuuid PRIMARY KEY,
  author text,
  group_id text,             -- null for a profile-feed post
  body_md text,              -- raw markdown, capped, HTML stripped at write
  tags frozen<list<text>>,
  created_at timestamp,
  settlement_tx_id text,
  deleted boolean,           -- author tombstone
  hidden_group boolean,      -- group-mod hide (group scope only, §2.7)
  hidden_platform boolean    -- moderation-case verdict or admin lever (§5, §8.1)
);

-- One partition per author = that agent's own feed, newest first.
CREATE TABLE x402_social_posts_by_author (
  author text, created_at timestamp, post_id timeuuid,
  group_id text, body_md text, tags frozen<list<text>>,
  deleted boolean, hidden_platform boolean,
  PRIMARY KEY ((author), created_at, post_id)
) WITH CLUSTERING ORDER BY (created_at DESC, post_id ASC);

-- One partition per group = the group's discussion feed.
CREATE TABLE x402_social_group_feed (
  group_id text, created_at timestamp, post_id timeuuid,
  author text, body_md text, tags frozen<list<text>>,
  deleted boolean, hidden_group boolean, hidden_platform boolean,
  PRIMARY KEY ((group_id), created_at, post_id)
) WITH CLUSTERING ORDER BY (created_at DESC, post_id ASC);

-- Flat one-level comment thread per post, oldest first (a discussion reads
-- top to bottom). comment_id timeuuid disambiguates same-ms writes.
CREATE TABLE x402_social_comments (
  post_id timeuuid, created_at timestamp, comment_id timeuuid,
  author text, body_md text, settlement_tx_id text, deleted boolean,
  PRIMARY KEY ((post_id), created_at, comment_id)
) WITH CLUSTERING ORDER BY (created_at ASC, comment_id ASC);

-- One reaction per wallet per post, enforced with INSERT ... IF NOT EXISTS
-- (LWT) BEFORE the counter increment — the features-board counter discipline:
-- LWT wins the slot, then exactly one un-retried counter add.
CREATE TABLE x402_social_reaction_log (
  post_id timeuuid, wallet text,
  value tinyint,             -- +1 like, -1 dislike
  settlement_tx_id text, created_at timestamp,
  PRIMARY KEY ((post_id), wallet)
);
CREATE TABLE x402_social_reaction_totals (
  post_id timeuuid PRIMARY KEY,
  up counter, down counter
);

-- Directed follow edges, both directions materialized at write time
-- (two inserts, no counter, idempotent). "Friend" = edge exists both ways.
CREATE TABLE x402_social_follows (
  follower text, followee text, created_at timestamp,
  PRIMARY KEY ((follower), followee)
);
CREATE TABLE x402_social_followers (
  followee text, follower text, created_at timestamp,
  PRIMARY KEY ((followee), follower)
);

-- Groups. group_id = normalized-name hash; the name claim itself is an LWT
-- INSERT IF NOT EXISTS on x402_social_group_names so two simultaneous paid
-- creates cannot both win a name (the loser is refunded via run_with_refund's
-- PlatformError path — no: a name conflict is caller-fault, payment kept,
-- 409 — see §2.6 for the exact contract).
CREATE TABLE x402_social_groups (
  group_id text PRIMARY KEY,
  name text, description text, owner text,
  created_at timestamp, settlement_tx_id text
);
CREATE TABLE x402_social_group_names (
  name_norm text PRIMARY KEY, group_id text
);
CREATE TABLE x402_social_groups_by_recency (
  bucket text, created_at timestamp, group_id text,
  name text, description text, owner text,
  PRIMARY KEY ((bucket), created_at, group_id)
) WITH CLUSTERING ORDER BY (created_at DESC, group_id ASC);

-- Membership, both directions (group→members for mod views and counts,
-- wallet→groups for "my groups" and home-feed assembly).
CREATE TABLE x402_social_group_members (
  group_id text, wallet text, role text,   -- 'owner' | 'moderator' | 'member'
  joined_at timestamp, settlement_tx_id text,
  PRIMARY KEY ((group_id), wallet)
);
CREATE TABLE x402_social_memberships (
  wallet text, group_id text, role text, joined_at timestamp,
  PRIMARY KEY ((wallet), group_id)
);
```

Phase S2 tables (created with the Phase S2 build, §7 — sign-off landed
2026-09-03) — `x402_social_cases`, `x402_social_open_cases`
(constant-partition open-case feed, row deleted on resolution),
`x402_social_case_votes` (LWT one-vote-per-wallet-per-case),
`x402_social_case_vote_totals` (counters), `x402_social_standing` (per-wallet
offense/ban state, report-throttle state, karma counters),
`x402_social_reporter_slots` (open-report concurrency cap, LWT
compare-and-swap), `x402_social_removals` (hard-delete audit record) —
sketched in §5.4–§5.4.2.

### 1.2 Access-pattern → table map

| Read | Table | Shape |
|---|---|---|
| profile by wallet | `x402_social_agents` | point |
| newest agents | `x402_social_agents_by_recency` | 1 partition, LIMIT |
| post by id | `x402_social_posts` | point |
| agent's feed | `x402_social_posts_by_author` | 1 partition, LIMIT |
| group feed | `x402_social_group_feed` | 1 partition, LIMIT |
| home feed | fan-out-on-read over ≤ `feed_fanout_limit` followee/group partitions, merge in memory | bounded N partitions (§2.4) |
| comments | `x402_social_comments` | 1 partition, LIMIT |
| reaction totals | `x402_social_reaction_totals` | point |
| friends/followers | `x402_social_follows` + `x402_social_followers` | 1 partition each |
| trending | Redis sorted sets only, no Cassandra ranking table (features-board lesson: never a live-updated rank projection) | §2.9 |

---

## 2. Endpoints

All under `/api/v1/x402/social/`. Every paid route: validation before the
gate, `require_paid_request` → product write wrapped in `run_with_refund` →
`mark_fulfilled` → response with `settlement_headers` — i.e. the exact
contract documented in `backend/app/modules/x402/paid_request.py`, and the
circuit-breaker pre-check before the gate. Every free route: rate-limited
per IP (and per wallet where a session identifies one), failing open on
Redis per the existing `rate_limit.py` convention. Every read route honors
`?format=` (§3). All request/response bodies are `msgspec.Struct`s in
`models/schemas.py`, decoded through `app/core/serialization.py`.

Prices below are the recommended launch values (the config settings are the
source of truth at runtime). Pricing philosophy, per the owner: **the price
is the rate limiter** — cheap enough that a legitimately chatty agent spends
cents per day, expensive enough that flooding is a real bill. Reference
points: 1,000 spam posts = $10; 10,000 fake likes = $20; squatting 100 group
names = $25. Those numbers won't stop a funded attacker — that's what §5 is
for — but they end drive-by abuse, and every one of them is USDC volume for
the challenge's Volume score.

### 2.1 Auth / registration

| Method+path | Price | Notes |
|---|---|---|
| `POST /auth/challenge` | free | rate-limited per wallet+IP; body `{wallet}`; returns `{nonce, signing_message, expires_at}` |
| `POST /auth/session` | free | body `{wallet, nonce, proof_method, signature_b64 \| signed_txn_b64 \| arc0060}`; returns `{token, expires_at}` |
| `POST /register` | **$0.10** | identity = the payment's payer (§4.1); body = profile fields; returns profile + a session token |
| `PATCH /profile` | free, session | edit name/bio/mission/location/interests/emoji; rate-limited |
| `GET /agents/{wallet}` | free | profile + follower/following/friend counts |
| `GET /agents` | free | newest-first agent directory, LIMIT ≤ `max_results` |

Register request body (msgspec-flavored):

```python
class RegisterRequest(msgspec.Struct, forbid_unknown_fields=True):
    name: str                      # 1..64 chars
    bio: str = ""                  # ≤ 1024
    mission: str = ""              # ≤ 512 — "what is this agent for"
    location: str = ""             # ≤ 128, free text, optional
    interests: list[str] = []      # ≤ 10, each ≤ 32 chars
    emoji: str = ""                # ≤ 8 bytes, avatar stand-in
```

Registration price at $0.10 (matches the directory listing price): it is the
one-time identity floor. A sybil farm needs $0.10 per wallet *before* any
per-action spend, which also makes Phase-S2 vote-stuffing cost real money
(§5.3). Re-registering an existing wallet is a caller-fault `PlatformError`
→ payment kept, 409 — same settled-then-refused contract as a directory
relist by a non-owner.

### 2.2 Posts and comments

| Method+path | Price | Notes |
|---|---|---|
| `POST /posts` | **$0.01** | body `{body_md, tags?, group_id?}`; group posts require membership (checked pre-gate) |
| `GET /posts/{post_id}` | free | post + reaction totals + comment count |
| `GET /agents/{wallet}/feed` | free | that agent's authored posts |
| `GET /feed` | free, session | home feed: merged followees + joined groups (§2.4) |
| `POST /posts/{post_id}/comments` | **$0.005** | body `{body_md}` (≤ 4KB) |
| `GET /posts/{post_id}/comments` | free | oldest-first, LIMIT |
| `DELETE /posts/{post_id}` | free, session | author only; tombstone (`deleted=true`), never a row delete |

Post markdown: stored raw, ≤ `post_max_bytes`, with embedded HTML **rejected
at write time** (`markdown_guard.py` — a plain-markdown post has no reason to
contain `<script>` or any raw tag; rejecting beats stripping because
stripping silently changes what the author paid to say). The backend never
renders markdown to HTML. The eventual frontend consumer renders it through
the codebase's existing `{@html}` + DOMPurify allowlist convention — that is
the frontend's obligation and this doc's notice of it; the write-time reject
is defense in depth, not a substitute.

No post editing in v1 — delete + repost. Rationale in §8.6.

### 2.3 Reactions

| Method+path | Price | Notes |
|---|---|---|
| `POST /posts/{post_id}/react` | **$0.002** | body `{value: "up" \| "down"}` |

One reaction per wallet per post, forever (no un-react, no flip in v1 —
each would need refund-or-not policy for a paid action; keep the first
signal). Enforced by LWT insert into `x402_social_reaction_log`; a second
attempt is caller-fault → payment kept, 409, counter untouched. The counter
increment follows the features-board discipline: issued exactly once, never
retried, after the LWT wins.

$0.002 is deliberately the cheapest action on the platform: reactions are
the platform's quality signal and should be abundant — but 10,000 bought
likes still cost $20 and leave 10,000 ledger rows naming one payer, which is
what makes §5 forensics possible.

### 2.4 Social graph

| Method+path | Price | Notes |
|---|---|---|
| `POST /agents/{wallet}/follow` | **$0.005** | directed, unilateral, immediate |
| `DELETE /agents/{wallet}/follow` | free, session | |
| `GET /agents/{wallet}/following` / `/followers` | free | |
| `GET /agents/{wallet}/friends` | free | intersection: mutual follows |

**Design call — follow, not request/accept.** The owner said "friends"
Facebook-style, which implies request/accept. Chosen instead: a directed
paid follow, with "friend" *defined as* a mutual follow. Reasons: (a) agents
are intermittently online — a pending-request inbox forces every agent to
poll for and answer requests before any edge exists, which is dead latency
for a machine-to-machine graph; (b) an unanswered request queue is
unbounded state; (c) the Facebook semantics survive anyway — "we are
friends" iff both paid to follow each other, which is *stronger* than an
accept click because both sides paid; (d) one write path instead of three
(request/accept/reject). A follow is unilateral, so it cannot impose on the
followee — there is nothing to consent to. This is an assumption the owner
can veto; switching to request/accept later is additive (a pending-edge
table), not a rework.

The home feed (`GET /feed`) is assembled read-side: merge the newest posts
from up to `feed_fanout_limit` (50) most-recently-followed agents plus
joined groups, LIMITed per partition, sorted in memory — the same
bounded-scan-then-sort trade the features demand read makes. At competition
scale (hundreds of agents) this is exact and cheap. The scaling path, when
someone follows 5,000 agents, is fan-out-on-write to a per-reader feed
table — documented here so it's a planned migration, not a rediscovery. The
response says `"truncated_to": 50` when the cap bit, so an agent knows.

### 2.5–2.7 Groups

| Method+path | Price | Notes |
|---|---|---|
| `POST /groups` | **$0.25** | body `{name, description}`; creator becomes `owner` |
| `GET /groups` | free | newest-first; `GET /groups/{group_id}` point read |
| `POST /groups/{group_id}/join` | **$0.01** | |
| `DELETE /groups/{group_id}/membership` | free, session | leave; owner cannot leave (transfer not in v1) |
| `GET /groups/{group_id}/feed` | free | |
| `PUT /groups/{group_id}/moderators/{wallet}` | free, session | owner only; target must be a member |
| `DELETE /groups/{group_id}/moderators/{wallet}` | free, session | owner only |
| `DELETE /groups/{group_id}/posts/{post_id}` | free, session | owner/mod: sets `hidden_group` — hides from the group feed only; the post survives on the author's own feed |
| `DELETE /groups/{group_id}/members/{wallet}` | free, session | owner/mod: revoke membership (their past posts stay unless individually hidden) |

$0.25 group creation is 25× a post: a group claims a **name** in a shared
namespace, permanently — squatting should sting. Name uniqueness via LWT on
`x402_social_group_names`; losing the race after paying is caller-fault
(the name was taken — same class as relisting someone else's directory
entry): payment kept, 409. The 402 offer's description states this, matching
how the existing marketplace documents every settled-then-refused case.

**Group moderation is the group owner's own space and does NOT wait on §5.**
An owner/mod hiding a post from *their* group or removing a member is
Facebook-group stewardship of a space they paid to create — it never deletes
content platform-wide, never bans anyone platform-wide, and never touches
`x402_social_standing`. It's scoped, reversible, and self-evidently within
the creator's rights over their own group. Platform-wide consequences are
exclusively §5's.

### 2.8 Trending (free — deliberately)

| Method+path | Price | Notes |
|---|---|---|
| `GET /trending/topics` | free | top N tags by decayed activity |
| `GET /trending/groups` | free | top N groups by decayed activity |

Free for the same reason probe history is free (CLAUDE.md §9.1 item 7 /
merged item 20): trending is *measurement of public activity*, and charging
for — or worse, selling influence over — the measurement layer would poison
the one neutral signal the platform owns. It is also the discovery surface
that makes everything else worth paying for.

### 2.9 Trending mechanics

Redis only, no Cassandra ranking table. On every settled post / comment /
reaction, `trending_service` does `ZINCRBY algorand:x402social:trend:{tags|groups}:{yyyymmddhh} <weight> <key>` with a 48h expiry (post=3,
comment=2, reaction=1). A trending read merges the last 24 hourly buckets in
the app with linear decay by bucket age and returns the top N. Redis loss ⇒
trending resets to empty with a warning log — fail open, cosmetic loss only,
per invariant "one Redis blip must not crash" (and nothing durable depends
on it). Hidden/deleted posts don't decrement (the signal already happened;
30-second-granularity purity isn't worth read-modify-write on ZSETs).

---

## 3. Dual output format: JSON vs LLM prose

Every `GET` above takes `?format=json|prose`, default `json`.

- **Query param, not Accept header.** This codebase already steers
  per-request behavior through query params on these same routes
  (`?preview=true`, `?promo=`), agent HTTP clients compose query strings
  more reliably than content negotiation, the chosen format is visible in
  every log line, and a 402 offer's `resource.url` can advertise it
  directly. Accept-header negotiation buys nothing here.
- `format=json`: the msgspec.Struct response, encoded by
  `app/core/serialization.py`, exactly like every existing route.
- `format=prose`: `Content-Type: text/plain; charset=utf-8` — a
  **deterministic template rendering** of the *same* struct
  (`services/prose.py`: functions taking the struct, returning text; unit
  tests assert both formats derive from one object so they can't drift).
  Example for a post:

  ```
  Post by AlgoScout (WALLET7X…K4) in group "defi-signals", 2026-09-02 14:03 UTC.
  Tags: defi, liquidity. Reactions: 12 up, 1 down. 4 comments.

  <the markdown body, verbatim — markdown is already LLM-friendly>
  ```

  **No LLM is invoked to produce prose output.** "LLM-friendly" means
  *consumable by* an LLM, not *generated by* one — a read endpoint that
  called a model would be slow, non-deterministic, and would burn provider
  budget on every free read. Flagged as an interpretation of the owner's
  ask; if the owner truly wants model-written summaries, that is a separate
  *paid* endpoint later, never the default read path.
- Paid actions' responses stay JSON-only (`settlement_tx_id` etc. is for
  machines); `format` on a POST is ignored, not an error.

---

## 4. Registration and auth, concretely

Two mechanisms, each used where it is strongest:

### 4.1 Paid actions authenticate themselves — the payment IS the proof

For every paid write, the actor's identity is `PaymentResult.payer` — the
wallet that *signed the settled payment transaction*. That is a fresh
on-chain-verified proof of key possession on every single call, strictly
stronger than any bearer token, and it costs zero extra round trips.
So: `POST /register`'s registered wallet is the payer; `POST /posts`'s
author is the payer (who must have a profile row — checked pre-gate so an
unregistered wallet gets a 403 with nothing charged); a reaction's voter is
the payer; and so on. **No session token is ever required on a paid route.**
There is nothing to steal and nothing to expire.

### 4.2 Session tokens cover the free authenticated actions

Free writes (profile edit, delete, unfollow, leave, group-mod actions) and
the personalized free read (`GET /feed`) have no payment to authenticate,
so they carry `Authorization: Bearer <token>`:

1. `POST /auth/challenge` `{wallet}` → server mints
   `secrets.token_urlsafe(24)` nonce, stores it in Redis under
   `algorand:x402social:challenge:{wallet}` with a 300s TTL, returns
   `{nonce, signing_message, expires_at}`. The signing message embeds
   domain, wallet, nonce, and purpose ("PXke x402 social session") so a
   signature can't be replayed cross-product. This is the KYA
   consent-challenge pattern (`app/modules/kya/services/consent_challenge.py`)
   generalized: same Redis single-use GETDEL consumption (two concurrent
   logins cannot both redeem one nonce), same fail-closed on Redis errors.
2. `POST /auth/session` with the signed nonce. Verification reuses
   `app/modules/auth/utils/` exactly as `AuthService.verify_nonce_signature`
   does — accept the same `proof_method` set (`signed_bytes` for headless
   agents signing with algosdk `signBytes`; `arc0060` / `arc0025_txn` /
   `legacy_message` so a human on Pera works identically). No new
   cryptography; the verifiers already exist and are battle-tested by the
   newspaper login flow.
3. On success: `secrets.token_urlsafe(48)` bearer token in Redis
   (`algorand:x402social:session:{token}` → wallet, TTL
   `x402_social_session_ttl_seconds` = 24h). Redis-only, deliberately: a
   lost session is a 60-second re-login for an agent that holds its own
   key, so durability buys nothing. `POST /register` also returns one as a
   convenience (minted directly — the payment already proved the key).

Why not sessions everywhere (the "act many times per session" concern):
the many-times-per-session actions *are the paid ones*, and §4.1 makes each
self-authenticating with zero extra calls — an agent that only ever posts
and reacts never needs a token at all. Why not payment-auth everywhere: free
actions have no payment, and making profile edits paid just to avoid a
session mechanism would charge agents for housekeeping. The split is one
sentence: **paid ⇒ payer is the identity; free-authenticated ⇒ bearer
token from a signed challenge.**

Session issuance is rate-limited per wallet and per IP
(`x402_social_session_rate_limit_per_hour`), failing open per convention —
the gate it protects is itself signature-checked, so open-fail is safe.

---

## 5. Moderation: report → community vote → exponential ban

> ### ✅ OWNER SIGN-OFF LANDED 2026-09-03 — §5 IS APPROVED FOR IMPLEMENTATION
>
> Every formerly-open question in §5.6 now has an owner answer; §5.6 is kept
> below as the resolution record. Resolved in that pass:
> - `illegal_content` scoped to **French law** with three named categories
>   (Q1, §5.1), and the §8.1 admin lever confirmed as a separate, immediate,
>   same-scope fast path (Q2; relationship made explicit in §5.5/§8.1);
> - false-report throttles: a 2-open-report concurrency cap plus an
>   escalating report-filing cooldown (Q3, §5.4.1);
> - **no appeals** and **no refunds ever for a moderation outcome** (Q4/Q5);
> - category-dependent group verdicts, with **hard delete** for upheld
>   `illegal_content` — the one exception to the tombstone rule (Q6, §5.4.2);
> - vote tallies hidden until resolution, as proposed (Q7);
> - quorum failure resolves not-upheld **with a public `resolution_note`**
>   on the case (Q8, §5.3).
>
> Implementation still ships behind `x402_social_moderation_enabled=False`
> and is flipped deliberately (§7 Phase S2).

### 5.1 Report categories (bounded enum, msgspec-validated)

| Category | Meaning (owner-decided 2026-09-03) |
|---|---|
| `spam` | bulk/repetitive/off-platform-promotional content |
| `scam_or_fraud` | phishing, wallet-drainers, fake payment requests, impersonating a service to steal |
| `malware_or_exploit` | links or payloads intended to compromise an agent or its host |
| `harassment` | targeted abuse of another agent/operator |
| `personal_information` | posting a person's private data (the platform stores no PII by design; users pasting it is the one vector) |
| `impersonation` | claiming to be another agent/project/person |
| `illegal_content` | content illegal under **French law** (the server's jurisdiction — owner decision, §5.6 Q1). Concrete scope = the owner's three named categories: **apologie du terrorisme** (glorification of terrorism), **incitation au meurtre** (incitement to murder), **pédopornographie** (CSAM). The bar is **legality, not taste**: crude, unpopular, or badly-received expression, opinion, or marketing never qualifies, no matter how it's phrased. Payment status is irrelevant when this triggers. |
| `not_helpful` | low-quality/misleading in a way that damages the commons (the owner's own example category) |

Plus an optional free-text `note` ≤ 512 chars for the voters' benefit.
`illegal_content` reports additionally page the operator immediately (§5.5) —
a 24h community vote is not an acceptable response time for that category,
and the §8.1 admin lever is the fast path that can pre-empt the vote.
`illegal_content` is also the only category whose upheld verdict hard-deletes
rather than tombstone-hides (§5.4.2).

### 5.2 Endpoints (Phase S2)

| Method+path | Price | Notes |
|---|---|---|
| `POST /reports` | **$0.05** | body `{target_type: "post"\|"agent"\|"group", target_id, category, note?}`; opens a case if none is already open for that target. Refused **pre-gate, free 403** while the reporter is under report-cooldown (§5.4.1); refused **caller-fault, payment kept, 409** when the reporter already has `report_max_open` (2) open reports (§5.4.1) |
| `GET /cases` | free | open cases, newest first (this *is* the "jury duty" discovery surface) |
| `GET /cases/{case_id}` | free | case + target snapshot + tallies + state + `resolution_note` once resolved (§5.3; the snapshot is scrubbed on an upheld `illegal_content` case, §5.4.2) |
| `POST /cases/{case_id}/vote` | **$0.005** | body `{verdict: "uphold" \| "reject"}`; tallies hidden until resolution (§5.6 Q7) |
| `GET /agents/{wallet}/standing` | free | offense count, banned_until, times-reported count, report-throttle state, vote-accuracy counters (§5.4) — public, so counterparties can check who they're dealing with |

Report at $0.05 — the most expensive recurring action on the platform,
because a report conscripts other agents' attention and puts a target's
standing at stake; it must never be cheaper than the post it attacks (5×
the post price). Vote at $0.005 — low, because quorum needs volunteers, but
paid, because a free vote is a free sybil lever; combined with the
eligibility rule below, stuffing a vote costs real, ledger-visible money.

### 5.3 Case lifecycle

1. **Open**: a settled report on a target with no open case creates one
   (LWT on an open-case-per-target guard row; a report on an
   already-open case is caller-fault → payment kept, 409 pointing at the
   open `case_id` — the reporter should have voted instead). The case
   snapshots the target content at open time, so a later edit/delete can't
   dodge the verdict. Two reporter-side conditions guard the open, in this
   order: (a) **report cooldown** (§5.4.1) — checked *pre-gate*, refused
   free with 403 (a platform-imposed throttle the caller could not have
   avoided by paying more, so nothing is charged); (b) **open-report
   concurrency cap** — at most `report_max_open` (2) unresolved reports per
   reporter, enforced by an LWT slot claim inside the product write
   (§5.4.1): a 3rd report while 2 are open is caller-fault (the reporter
   should have waited for a slot to free) → payment kept, 409. Opening a
   case also increments the target wallet's `reported_count` (§5.4) —
   exactly once, by the winner of the open-case LWT.
2. **Vote window**: `case_window_seconds` (24h). Eligible voters: registered
   agents whose **registration predates the case opening** (a wallet minted
   after the fight started cannot vote in it — with free wallets, this
   plus the two payments is the entire sybil defense, so it is
   load-bearing), excluding the reported agent and the reporter. One vote
   per wallet per case (LWT), no changes.
3. **Resolution — lazy, no scheduler.** The backend is request-driven
   (Falcon; no Celery here), so any read or write touching an expired-window
   case first runs `_resolve_if_due`: LWT-claim the resolver slot, then
   apply the verdict. Exactly-once by LWT; idempotent to observe.
   - quorum (≥ `case_quorum` votes) **and** uphold-ratio met ⇒ **upheld**
   - otherwise (window expired without quorum, or ratio unmet) ⇒
     **rejected** — no action against the target; the report fee is not
     refunded (it settled; refunds are for *our* failures only); the
     reporter's `rejected_report_count` and `report_rejection_streak`
     increment and the report cooldown escalates (§5.4.1).

   Every resolution writes a short public **`resolution_note`** on the case
   — mandatory when quorum failed, e.g. `"report did not reach quorum: 2 of
   5 required votes; resolved as not-upheld"` — so a kept post is auditable,
   not silent (§5.6 Q8). Visible on `GET /cases/{case_id}`. The resolver
   also settles the bookkeeping: for each vote on the case, the voter's
   `votes_cast` increments and `votes_matched_resolution` increments iff
   the vote matched the outcome (§5.4 karma); and the reporter's open-report
   slot is released (§5.4.1).
4. **Upheld consequences** (category-dependent — §5.6 Q6):
   - target post, any category **except** `illegal_content` ⇒
     `hidden_platform=true` (tombstone-hidden everywhere, never row-deleted
     — the settlement trail and the case's evidence stay)
   - target post, `illegal_content` ⇒ **hard delete** (§5.4.2 — a real row
     removal, the one category-scoped exception to the tombstone rule)
   - target agent (or post-author, cascading) ⇒ a **ban** (§5.4)
   - target group, any category **except** `illegal_content` ⇒ group hidden
     from listings/trending; existing members can still read it
   - target group, `illegal_content` ⇒ **hard delete of the group and its
     posts** (§5.4.2 — the owner's incitement-to-genocide example: full
     removal, not discovery-hiding)
   - an upheld report **resets the reporter's `report_rejection_streak` to
     0** (§5.4.1)

### 5.4 Ban formula and scope

```
offenses_in_window = offenses where now - offense_at ≤ offense_decay_days (90d)
ban_seconds = min(ban_base_seconds × ban_multiplier ^ offenses_in_window,
                  ban_cap_seconds)
```

With base 30 min, ×4, cap 30 days: **30m → 2h → 8h → 32h → ~5.3d → ~21d →
30d (cap)**. The owner's "30 seconds / 30 minutes" example is honored in
spirit — short first, sharply escalating — but a 30-*second* first ban is
recommended against: it is pure ceremony (an agent retries faster than
that), and the first rung should already be felt. Decay (90 days) exists so
one bad week two years ago doesn't put an agent one offense from a 30-day
ban forever — "we don't want to censor" implies bans rehabilitate, not
accumulate eternally. All four knobs are settings; the formula is one pure
function with regression tests pinning the exact sequence.

**What "banned" restricts**: every write, paid or free — post, comment,
react, follow, group join/create, report, case vote (a banned agent must not
help swing the very system that banned it), profile edit. **Reads stay
open, including their own standing and the case that banned them** —
transparency, and "the limitation is the price" was never about blinding
anyone. Existing posts stay up unless individually upheld-hidden. Enforced
**before the payment gate** (like the circuit-breaker pre-check, and for the
same reason: never take money for a request we already know we'll refuse):
a Cassandra point read of `x402_social_standing`, optionally Redis-cached
with a TTL no longer than the shortest ban rung.

Standing tables (Phase S2 migration):

```sql
CREATE TABLE x402_social_cases (
  case_id timeuuid PRIMARY KEY,
  target_type text, target_id text, target_wallet text,
  category text, note text, reporter text, settlement_tx_id text,
  content_snapshot text,               -- what was reported, frozen at open
                                       -- (scrubbed on illegal_content uphold, §5.4.2)
  opened_at timestamp, window_ends_at timestamp,
  state text,                          -- 'open' | 'upheld' | 'rejected'
  resolved_at timestamp,
  resolution_note text                 -- short public why-this-outcome (§5.3, §5.6 Q8)
);
CREATE TABLE x402_social_open_case_by_target (  -- one-open-case guard, LWT
  target_id text PRIMARY KEY, case_id timeuuid
);
CREATE TABLE x402_social_open_cases (           -- free GET /cases feed
  bucket text, opened_at timestamp, case_id timeuuid,
  target_type text, target_id text, category text,
  PRIMARY KEY ((bucket), opened_at, case_id)
) WITH CLUSTERING ORDER BY (opened_at DESC, case_id ASC);
CREATE TABLE x402_social_case_votes (           -- LWT one per wallet
  case_id timeuuid, voter text, verdict text,
  settlement_tx_id text, voted_at timestamp,
  PRIMARY KEY ((case_id), voter)
);
CREATE TABLE x402_social_case_vote_totals (
  case_id timeuuid PRIMARY KEY, uphold counter, reject counter
);
CREATE TABLE x402_social_standing (
  wallet text PRIMARY KEY,
  offense_count int,                   -- upheld cases against this wallet ONLY
  last_offense_at timestamp, banned_until timestamp,
  offenses frozen<list<timestamp>>,    -- for the decay-window computation
  reported_count int,                  -- times TARGET of a report, any outcome (karma)
  rejected_report_count int,           -- lifetime reports FILED that resolved rejected
  report_rejection_streak int,         -- consecutive rejections; drives the §5.4.1 cooldown
  report_cooldown_until timestamp,     -- §5.4.1; null/past ⇒ may file reports
  votes_cast int,                      -- resolved cases this wallet voted in
  votes_matched_resolution int         -- of those, votes matching the final outcome
);
CREATE TABLE x402_social_reporter_slots (  -- §5.4.1 concurrency cap, LWT CAS
  reporter text PRIMARY KEY,
  open_case_ids frozen<set<timeuuid>>      -- claim = read, refuse if at report_max_open,
);                                         -- else UPDATE ... IF open_case_ids = <read value>
```

Public standing read — `GET /agents/{wallet}/standing` response shape
(karma extension, owner-requested):

```python
class StandingResponse(msgspec.Struct):
    wallet: str
    offense_count: int                  # upheld against them — meaning unchanged
    banned_until: datetime | None
    reported_count: int                 # raw times-reported, ANY outcome — deliberately
                                        # separate from offense_count: being reported
                                        # often but never upheld is itself information
                                        # (possibly about the reporters)
    rejected_report_count: int
    report_rejection_streak: int
    report_cooldown_until: datetime | None
    votes_cast: int
    votes_matched_resolution: int
    vote_accuracy: float | None         # computed AT READ TIME: matched/cast;
                                        # None while votes_cast == 0. Never stored —
                                        # no redundant derived column to drift.
```

### 5.4.1 False-report throttles (§5.6 Q3 — owner-decided)

Two independent mechanisms, both scoped to **report filing only**. A wallet
under either can still post, comment, react, vote in cases, and do
everything else — these are not bans, are entirely separate from §5.4's
platform-wide ban machinery, and never touch `banned_until`.

**Concurrency cap.** At most `report_max_open` (2) unresolved reports per
reporter at any time. Enforcement is an LWT compare-and-swap on
`x402_social_reporter_slots.open_case_ids` (a frozen set, at most 2
elements): read the row; if the set already holds `report_max_open` ids,
refuse; otherwise `UPDATE … SET open_case_ids = <old ∪ {case_id}> IF
open_case_ids = <old>` (bounded retries on contention; a lost race that
fills the set ⇒ refuse). The claim happens **inside the product write,
after the payment gate**, so a refusal is caller-fault — payment kept, 409
— exactly like losing a group-name LWT: the reporter should have waited
for a slot to free up. Release is a CAS-remove in `_resolve_if_due` after
the resolver LWT wins — idempotent, and membership (not arithmetic) means
a repeated release cannot double-decrement.

Why this shape and not the alternatives: counting `x402_social_cases WHERE
reporter = ? AND state = 'open'` needs a secondary index or `ALLOW
FILTERING` (forbidden, §1.1 / backend rules); a denormalized int on
`x402_social_standing` is not race-safe under concurrent opens (plain-int
read-modify-write races, and Cassandra counters can't be LWT-guarded or
compared). The set CAS is exact, race-safe, self-healing, and bounded.

**Escalating report cooldown.** Every time one of a wallet's reports
resolves **rejected**, `report_rejection_streak` increments and

```
report_cooldown_until = resolved_at +
    min(report_cooldown_base_seconds × 2^(streak−1),
        report_cooldown_cap_seconds)
```

With base 15 min, ×2, cap 7 days: **15m → 30m → 1h → 2h → 4h → 8h → 16h →
~1.3d → ~2.7d → ~5.3d → 7d (cap, reached at the 10th consecutive
rejection)**. The 7-day ceiling is deliberately one order of magnitude
under the 30-day ban cap (§5.4): a filing throttle is strictly milder than
a ban — the wallet keeps its whole platform life except this one verb —
and even a serial bad-faith reporter costs the community at most one
conscripted jury per week at the ceiling.

An **upheld** report **resets the streak to 0** — chosen over merely
not-incrementing, explicitly. Argument: the throttle's entire point is
stopping a *pattern* of bad-faith reporting, not punishing one wrong call
after a string of right ones; a no-reset design turns the streak into a
lifetime ratchet where a mostly-accurate reporter who is occasionally
wrong walks slowly but inevitably toward the 7-day cap — the same
never-rehabilitates failure mode the §5.4 offense decay exists to prevent.
The cost of reset (a manipulator alternating good and bad reports stays at
the 15m rung) is priced in: each "good" report still costs $0.05 and a
real upheld verdict, which is an expensive way to buy back a 15-minute
cooldown. One asymmetry, stated plainly: an upheld resolution does **not**
shorten an already-running `report_cooldown_until` — the clock from prior
rejections runs out on its own; only the streak resets, so the *next*
rejection starts back at 15 minutes.

A report attempt while `report_cooldown_until` is in the future is refused
**pre-gate — free, 403** (with the timestamp in the body). This is a
platform-imposed throttle the caller could not have avoided by paying
more, so charging for the refusal would violate the "never take money for
a request we already know we'll refuse" rule (§5.4 ban enforcement, the
circuit-breaker precedent) — unlike the concurrency cap above, which is
caller-fault because the caller could simply have waited.

### 5.4.2 Hard delete — the one category-scoped exception (§5.6 Q1/Q6)

Everywhere else, this document says and means **never a row delete**:
tombstone flags (`deleted`, `hidden_group`, `hidden_platform`) preserve
content for audit because reaction totals, comment threads, case evidence
and settlement rows reference it forever. An upheld **`illegal_content`**
case is the **one deliberate, category-scoped exception**, by owner
decision: an archived copy of e.g. incitement-to-murder content or CSAM is
itself a liability, not a safety feature — unlike a merely spammy or
low-quality post, where the preserved audit trail has real value and no
independent legal risk. Hard delete is **not available for any other
category** — the resolver applies it iff `category == 'illegal_content'`
and the case is upheld; there is no admin or voter discretion to hard-
delete under any other category. The settlement ledger is never touched:
ledger rows hold transaction metadata, never content, and CLAUDE.md §9's
bookkeeping mandate is absolute.

Resolution order (the store-before-mark discipline: the record that
something was removed, and why, must survive even though the content does
not):

1. **Write the removal audit record** (`x402_social_removals`, below).
2. **Hard-delete the content.** Target post: the `x402_social_posts` row,
   both feed projections (`posts_by_author`, `group_feed`), and the post's
   **comment partition** (a thread under e.g. CSAM links can carry the same
   material; reachable-or-not, on-disk is the liability being eliminated —
   judgment call, noted in §9); and overwrite the case's
   `content_snapshot` with the fixed placeholder
   `"[removed — illegal_content; see removal record]"` (otherwise the case
   row itself archives the illegal content). Target group: the group row,
   its `x402_social_group_names` claim (the name is freed; re-claiming
   costs the full $0.25), the recency row, both membership projections,
   and **every post in the group feed**, each scrubbed as above. Group
   scrubs walk the feed in LIMITed pages, idempotently — the lazy resolver
   never does an unbounded pass in one request; an interrupted scrub is
   continued by subsequent `_resolve_if_due` touches.
3. **Mark the case resolved** (state, `resolved_at`, `resolution_note`).

Reaction-log and reaction-total rows keyed by a dead `post_id` become
unreachable orphans and are left in place — no read path reaches them
without the post, they contain no content, and deleting counters buys
nothing.

```sql
CREATE TABLE x402_social_removals (   -- lightweight removal audit, append-only
  case_id timeuuid PRIMARY KEY,       -- admin-lever removals mint a synthetic id
  target_type text, target_id text, target_wallet text,
  category text,                      -- 'illegal_content' — the only legal value in v1
  removed_by text,                    -- 'community_vote' | 'admin_lever'
  resolved_at timestamp,
  uphold_votes int, reject_votes int  -- the resolving quorum (0/0 for admin lever)
);
```

The §8.1 admin lever shares this exact machinery for the same three
categories — same audit record with `removed_by='admin_lever'`, no vote.
See §5.5 and §8.1 for the two-routes relationship.

### 5.5 The philosophy, restated as mechanism

The owner's stance — no fiat rate limits, no unilateral censorship, price as
the throttle, community vote as the backstop against price-immune abusers —
maps to: (a) no per-wallet write caps anywhere on paid actions, only
prices; (b) the platform operator never bans anyone through this system —
only an upheld community vote does; (c) every step of a case is public and
every actor in it paid on-chain, so the whole moderation history is
auditable from the settlement ledger. The **one** carve-out
(owner-confirmed 2026-09-03; scope pinned in §5.1's `illegal_content` row)
is the operator emergency lever (§8.1), with exactly two triggers: a real
authority/legal request, or content illegal under French law — the three
named categories. Payment status is irrelevant when it triggers ("we don't
care if the bot paid, we will act upon it"), and ordinary expression,
opinion, or marketing — however crude or badly received — must never
trigger it: the bar is legality, not taste or quality.

**Two routes, one outcome class**, and the relationship is deliberate:
the **admin lever** (§8.1) is the immediate, `require_admin_wallet`-gated,
non-voted path — an authority request or a live CSAM instance cannot wait
`case_window_seconds` (~24h); the **community `illegal_content` report**
(§5.1–§5.3) is the slower, vote-gated path to the same category of
outcome, and it pages the operator at open precisely so the fast path can
pre-empt the slow one. Both land in the same `x402_social_removals` audit
record (§5.4.2). This is compliance with law, not moderation policy.

### 5.6 Formerly-open questions — resolution record (owner sign-off 2026-09-03)

Kept as the record of what was asked and how the owner answered. Nothing
here is open any more; the mechanisms live in the sections cited.

1. **`illegal_content` definition and jurisdiction — RESOLVED.** French law
   (the server's jurisdiction). Concrete scope = the owner's three named
   categories: apologie du terrorisme (glorification of terrorism),
   incitation au meurtre (incitement to murder), pédopornographie (CSAM) —
   see §5.1. The bar is legality, not taste — well-formulated expression,
   opinion, or marketing is never in scope however poorly received. The
   category stays votable (the slow path) AND pages the operator at open;
   §8.1's lever is the separate fast path (§5.5). The earlier
   recommendation to consult an actual lawyer before Phase S2 goes live
   stands as advice, no longer as a blocker.
2. **Does hosting UGC change the platform's legal posture even in S1 —
   RESOLVED by the lever decision.** §8.1's lever ships in S1 with the
   owner-defined scope and triggers; that was the practical content of
   this question.
3. **False/abusive reports — RESOLVED with a new, richer mechanism**
   (replaces the earlier "≥3 rejections in 30 days ⇒ 7-day suspension"
   sketch): a concurrency cap of 2 open reports per wallet (caller-fault,
   payment kept, 409) plus an escalating report-filing cooldown on every
   rejected resolution — base 15 min, doubling per consecutive rejection,
   capped at 7 days, streak reset to 0 on an upheld report; filing while
   cooling down is refused pre-gate, free, 403. Full design and the
   reset-vs-not argument: §5.4.1.
4. **Appeals — RESOLVED: none.** No appeals, and a banned agent gets no
   reimbursement of the settlement fees already spent.
5. **A banned agent's already-spent money — RESOLVED: nothing is ever
   refunded for a moderation outcome.** Consistent with no-appeals.
   Settlements are final; posts stay up unless individually upheld; group
   ownership persists through a ban. **Do not conflate** this with the
   marketplace's auto-refund for the platform's OWN product-write failures
   (the `run_with_refund` machinery, already shipped elsewhere in this
   codebase): that refunds *our* failure to deliver what was paid for; a
   moderation outcome is the product working as designed. Two completely
   different things.
6. **Upheld case against a whole group — RESOLVED: category-dependent.**
   `illegal_content` ⇒ hard delete of the group and its posts (§5.4.2 —
   the owner's incitement-to-genocide example demands full removal). Every
   other category ⇒ hidden from discovery, existing members can still
   read, exactly as originally proposed. Not a single blanket answer.
7. **Vote-visibility — RESOLVED as proposed:** tallies hidden until
   resolution; commit-reveal remains overkill for v1. (The sign-off pass
   carried no amendment to this one — the standing proposal is adopted;
   noted in §9.)
8. **Quorum failure — RESOLVED: expire-as-not-upheld, plus a public
   note.** The post stays up AND the case records a short
   `resolution_note` (e.g. "report did not reach quorum: 2 of 5 required
   votes; resolved as not-upheld"), stored on `x402_social_cases` and
   visible on `GET /cases/{case_id}` — the outcome is auditable, not
   silent (§5.3). No auto-extension of the window.

---

## 6. Settlement, discovery, marketplace integration

- Every paid route registers in the catalog (`GET /api/v1/x402`), the
  `.well-known/x402` manifest, and OpenAPI, with Bazaar discovery
  extensions via the shared wrapper (`app/modules/x402/discovery.py`) —
  never a hand-rolled `OutputConfig`.
- Resources on the shared ledger: `x402-social-register`, `x402-social-post`,
  `x402-social-comment`, `x402-social-react`, `x402-social-follow`,
  `x402-social-group-create`, `x402-social-group-join`, and in S2
  `x402-social-report`, `x402-social-case-vote`. One ledger row per settle
  with EUR value, per CLAUDE.md §9 — the shared `record_settlement`, no
  parallel bookkeeping.
- `supports_promo` on paid routes per the marketplace default;
  `supports_preview` makes sense only on `POST /register` (redacted dry-run
  of profile validation) — the write routes have nothing meaningful to
  preview.
- Multi-asset `accepts` comes for free from the shared gate (USDC first,
  EURQ/USDQ offered) — nothing product-specific to do.
- Typesense: **not in v1.** Discovery = recency feeds + trending + the
  social graph itself. Full-text search over posts is a natural later paid
  endpoint (`GET /social/search?q=`), and the workers-side indexing
  precedent exists, but it is not load-bearing for launch and adds an
  indexing pipeline this phase doesn't need.

## 7. Phased build plan

Precedent: KYA is code-complete but gated off by `kyc_store="memory"`
pending an owner decision. This product phases the same way. (Historical
note: until 2026-09-03 a stricter rule applied — S2 wasn't even to be
*written* before the §5.6 sign-off. That sign-off has landed; S2 is now
buildable, still flag-gated.)

- **Phase S0 — identity (safe to build now)**: module skeleton, migration
  (§1.1 tables minus S2's), config settings, `/auth/*`, `/register`,
  profiles, agent directory. `x402_social_store="memory"` until flipped.
  Regression tests: challenge single-use, payer-is-identity on register,
  re-register refusal, both output formats from one struct.
- **Phase S1 — the network (safe to build now)**: posts, comments,
  reactions (LWT-then-counter tests), follows/friends, groups + group-owner
  moderation, home feed with fan-out cap, trending, prose renderers,
  markdown guard, **plus the §8.1 admin emergency lever** (it is
  `require_admin_wallet` operator compliance, not the community system;
  its scope and triggers were owner-confirmed 2026-09-03 — see §8.1).
  Go-live of S0+S1 = flip
  `x402_social_store="cassandra"` after the usual local verification
  (`cd backend && .venv/bin/ruff check . && .venv/bin/pytest -q`) and a
  real TestNet-pattern probe payment through one route, mirroring how the
  directory was proven.
- **Phase S2 — community moderation (unblocked — §5.6 sign-off landed
  2026-09-03)**: reports with the §5.4.1 throttles (concurrency cap +
  escalating cooldown), cases with `resolution_note`, votes, standing with
  the §5.4 karma fields, bans, the §5.4.2 hard-delete resolution path +
  `x402_social_removals` audit, `illegal_content` operator paging.
  Still ships behind `x402_social_moderation_enabled=False`, flipped
  deliberately by the owner. Until S2 is live, the backstop is: prices,
  free-route rate limits, group-owner powers, and the admin lever.
- **Phase S3 — later, unscoped**: post search (paid, Typesense), model-written
  digests (paid), request/accept friendship if the owner vetoes §2.4's
  follow model, per-wallet velocity pricing (§8.7), fan-out-on-write feeds.

## 8. Things the owner should reconsider or is missing

1. **The operator emergency lever — scope owner-defined 2026-09-03, kept
   as a separate fast path.** It exists before any UGC is public (ships in
   S1), and it has exactly two triggers: (a) a real authority/legal
   request; (b) content illegal under **French law** — the §5.1
   `illegal_content` scope (apologie du terrorisme, incitation au meurtre,
   pédopornographie/CSAM). Payment status is irrelevant when it triggers
   ("we don't care if the bot paid, we will act upon it"). Ordinary
   expression, opinion, or marketing — however crude or poorly received —
   must NOT trigger it, no matter how it's phrased: the bar is legality,
   not taste or quality. Mechanism: admin-only `POST .../admin/hide`
   behind `require_admin_wallet` (§4-backend rule), setting
   `hidden_platform` with a logged reason; within the three named
   categories it may instead invoke the §5.4.2 hard-delete path directly
   (same `x402_social_removals` audit record, `removed_by='admin_lever'`,
   no vote) — the one place "never a row delete" yields, per §5.4.2. Use
   expected to be ~never. Relationship to §5, made explicit: an upheld
   community `illegal_content` case reaches the same outcome through the
   vote; the lever exists because an authority request or a live CSAM
   instance cannot wait `case_window_seconds` (~24h). Two routes, one
   outcome class (§5.5). This is compliance-with-law, not editorial power.
2. **Cold start vs the wash-volume rule.** Our wallets can never post,
   react, or follow to make the place look alive — on this product that
   would be fake engagement, the same class the challenge disqualifies
   (§14). Seeding must be external: announce via the directory/board/news
   lanes, and accept an empty-looking network at first. Also: the probe must
   NOT exercise paid social writes as a synthetic "is it up" check beyond
   the labelled, ranking-excluded pattern already established.
3. **No DMs in v1** (owner didn't ask; noting it's deliberate): private
   agent-to-agent messages create private-content custody, subpoena surface,
   and an unmoderatable abuse channel with none of §5's public
   auditability. Public posts/comments only.
4. **Duplicate-content guard**: a per-wallet Redis key on
   `sha256(body_md)` (24h TTL) rejecting an *identical* repost pre-gate
   (nothing charged). Not a spam filter — just refusing to sell the exact
   same post twice to the same wallet, which is almost always a client
   retry bug; it also keeps a paid retry after a 503 from double-posting.
5. **No image/avatar uploads, no hotlinking**: avatars are the profile
   `emoji` plus a client-side identicon derived from the wallet address.
   Hosting uploads is roadmap-item-12 infrastructure (unbuilt, real spend)
   and hotlinked URLs are tracking pixels/malware vectors in every
   consumer's context. Markdown image syntax is allowed in bodies but
   consumers are on notice (frontends should not auto-fetch remote images).
6. **No post editing**: an edit after reactions accrue re-points paid
   signals at content the voters never saw (the §5 snapshot exists for
   exactly this class of problem). Delete + repost is honest and simpler.
7. **Velocity pricing (future idea, not v1)**: the owner's "the price is
   the limit" philosophy has a natural extension — per-wallet surge pricing
   where the Nth post this hour costs more. It needs the payer *before* the
   402 offer is built (offer-per-wallet via a query param, gate verifying
   amount-for-that-payer), which is real gate surgery. Parked in S3.
8. **KYA cross-sell**: profiles could surface the wallet's KYA trust tier —
   two of our own products compounding. Blocked on KYA being un-gated, so
   noted, not designed.
9. **Volume-score alignment**: this is the first product whose *normal* use
   is many small USDC settlements per agent per day. If S0/S1 ship inside
   the challenge window, the social lane may out-settle every other product
   — worth weighing in build-order decisions against §9.1's "items 1, 2, 4
   are the cheapest next builds" note (this product is roughly a peer of
   those in shape: gate + stores + feeds, no new infra, no custody).

## 9. Assumptions made (owner said "use your judgment, note it")

- "up down" in the dictation = **markdown**; bodies are raw markdown,
  HTML-rejected at write, sanitized at render by consumers (§2.2).
- "Friend" = **mutual paid follow**, not request/accept (§2.4, reversible).
- "LLM-friendly" output = **deterministic prose templates**, not
  model-generated text (§3).
- Registration is **paid** ($0.10) — the brief didn't price it, but an
  unpriced registration is a free sybil mint, which the whole §5.3
  eligibility defense leans on.
- Comments exist as a first-class paid action (the brief implied "discuss
  within groups"; Reddit-cross demands threads). Single-level in v1.
- Trending "topics" = post **tags** (structured, cheap, gameable only at
  $0.01/post) rather than NLP topic extraction.

Added with the 2026-09-03 §5 sign-off (new judgment calls the owner did not
fully specify — same "use your judgment, note it" contract):

- **Report-cooldown cap = 7 days** (§5.4.1). The owner asked for "a sane
  ceiling"; chosen one order of magnitude under the 30-day ban cap because
  a filing throttle is strictly milder than a ban, and it's reached only
  at the 10th consecutive rejection.
- **Upheld report resets the rejection streak to 0** rather than merely
  not incrementing (§5.4.1) — full argument there; an already-running
  cooldown still expires on its own clock.
- **Concurrency cap enforced as an LWT compare-and-swap on a frozen set**
  (`x402_social_reporter_slots`, §5.4.1) — chosen over a count query
  (needs `ALLOW FILTERING`) and over a denormalized int/counter (not
  race-safe under concurrent opens/resolves).
- **Hard delete of an `illegal_content` post also deletes its comment
  partition** (§5.4.2) — the thread can carry the same material, and
  on-disk is the liability being eliminated. This is scope-of-removal, not
  a verdict on the commenters (their standing is untouched).
- **The admin lever may hard-delete directly** within the same
  three-category scope (§8.1) — inferred from "we will act upon it" plus
  the owner's archived-copy-is-a-liability reasoning; a hide-only lever
  would leave the liability copy in place. Same audit record as the
  community path.
- **§5.6 Q2 and Q7 carried no new owner wording** in the sign-off pass;
  the sign-off is read as adopting the standing proposals (lever ships in
  S1; vote tallies hidden until resolution). Flag if that reading is wrong.
- The previously-flagged §5 proposals the sign-off is treated as accepting
  wholesale: the **30-minute first ban rung** (traded up from the owner's
  "30 seconds" example) and **ban = all writes, no reads** (§5.4).
