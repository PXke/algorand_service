from __future__ import annotations

import os


def env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


CASSANDRA_HOSTS = env_str("CASSANDRA_HOSTS", "127.0.0.1")
CASSANDRA_KEYSPACE = env_str("CASSANDRA_KEYSPACE", "algorand_platform")
CASSANDRA_LOCAL_DC = env_str("CASSANDRA_LOCAL_DC", "datacenter1")
# Required when the cluster runs PasswordAuthenticator (prod host does).
CASSANDRA_USERNAME = env_str("CASSANDRA_USERNAME", "")
CASSANDRA_PASSWORD = env_str("CASSANDRA_PASSWORD", "")
REDIS_URL = env_str("REDIS_URL", "redis://localhost:6379/0")
ALGOD_URL = env_str("ALGOD_URL", "https://testnet-api.algonode.cloud").rstrip("/")
# Sent as the X-Algo-API-Token header when set (prod node requires it; algonode does not).
ALGOD_TOKEN = env_str("ALGOD_TOKEN", "")
NEWS_FEED_BUCKET = env_str("NEWS_FEED_BUCKET", "main")
NEWS_MAX_ARTICLES_PER_DAY = min(max(1, env_int("NEWS_MAX_ARTICLES_PER_DAY", 7)), 7)
NEWS_MAX_BREAKING_PER_DAY = env_int("NEWS_MAX_BREAKING_PER_DAY", 2)
NEWS_STRICT_DAILY_CAP = env_bool("NEWS_STRICT_DAILY_CAP", True)
CRAWL_PAUSE_WHEN_PUBLISH_CAP_FULL = env_bool("CRAWL_PAUSE_WHEN_PUBLISH_CAP_FULL", True)

# Crawler lanes — see crawler_config table + docs/modules/crawler-types.md
# Env overrides DB when set. Legacy HTTP/BROWSER aliases still work.
CRAWLER_HTTP_ENABLED = env_bool("CRAWLER_HTTP_ENABLED", True)
CRAWLER_BROWSER_ENABLED = env_bool("CRAWLER_BROWSER_ENABLED", False)
CRAWLER_WEB_SPA_ENABLED = env_bool("CRAWLER_WEB_SPA_ENABLED", False)
CRAWLER_MAIL_ENABLED = env_bool("CRAWLER_MAIL_ENABLED", True)
CRAWLER_CHAIN_ENABLED = env_bool("CRAWLER_CHAIN_ENABLED", True)
CRAWLER_METRICS_ENABLED = env_bool("CRAWLER_METRICS_ENABLED", True)
# Respect robots.txt for frontier crawling (politeness). The robots.txt per host
# is fetched once and cached; a disallowed URL is skipped before fetching.
CRAWLER_RESPECT_ROBOTS = env_bool("CRAWLER_RESPECT_ROBOTS", True)
CRAWLER_USER_AGENT = env_str(
    "CRAWLER_USER_AGENT", "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"
)
# Per-URL recrawl cooldown: a given link is fetched at most once per this window
# (skipped at enqueue AND before fetch). Stops the frontier re-crawling the same
# page every time a link is rediscovered. 6 hours.
CRAWL_URL_RECRAWL_COOLDOWN_SECONDS = env_int("CRAWL_URL_RECRAWL_COOLDOWN_SECONDS", 21600)

WRITER_ENRICHMENT_ENABLED = env_bool("WRITER_ENRICHMENT_ENABLED", True)
WRITER_ENRICHMENT_PROBE_DOMAIN = env_bool("WRITER_ENRICHMENT_PROBE_DOMAIN", True)
WRITER_ENRICHMENT_FETCH_TWEETS = env_bool("WRITER_ENRICHMENT_FETCH_TWEETS", True)
WRITER_EDITORIAL_BRIEFS_ENABLED = env_bool("WRITER_EDITORIAL_BRIEFS_ENABLED", True)

ARTICLE_EDIT_WINDOW_HOURS = env_int("ARTICLE_EDIT_WINDOW_HOURS", 24)
BREAKING_INLINE_DRAIN = env_bool("BREAKING_INLINE_DRAIN", False)
NEWS_STANDARD_INTERVAL_HOURS = env_int("NEWS_STANDARD_INTERVAL_HOURS", 3)
NEWS_MIN_DIFF_LINES = env_int("NEWS_MIN_DIFF_LINES", 3)
PUBLISH_IMMEDIATE_PRIORITY = env_int("PUBLISH_IMMEDIATE_PRIORITY", 95)
PUBLISH_QUEUE_DRAIN_SECONDS = env_int("PUBLISH_QUEUE_DRAIN_SECONDS", 900)
PUBLISH_BREAKING_DRAIN_SECONDS = env_int("PUBLISH_BREAKING_DRAIN_SECONDS", 120)
PUBLISH_QUEUE_BATCH_LIMIT = env_int("PUBLISH_QUEUE_BATCH_LIMIT", 50)

# Queue maintenance (Phase 5 — archive & defer)
PUBLISH_DEFER_PRIORITY_THRESHOLD = env_int("PUBLISH_DEFER_PRIORITY_THRESHOLD", 45)
PUBLISH_DEFER_AFTER_HOURS = env_int("PUBLISH_DEFER_AFTER_HOURS", 24)
PUBLISH_ANNOUNCE_EXPIRE_HOURS = env_int("PUBLISH_ANNOUNCE_EXPIRE_HOURS", 72)
PUBLISH_QUEUE_MAINTENANCE_SECONDS = env_int("PUBLISH_QUEUE_MAINTENANCE_SECONDS", 3600)

OFFICIAL_MAIL_FROM_DOMAINS = env_str(
    "OFFICIAL_MAIL_FROM_DOMAINS",
    "algorand.foundation,algorand.com",
)

MAIL_IMAP_HOST = env_str("MAIL_IMAP_HOST", "")
MAIL_IMAP_PORT = env_int("MAIL_IMAP_PORT", 993)
MAIL_IMAP_USER = env_str("MAIL_IMAP_USER", "")
MAIL_IMAP_PASSWORD = env_str("MAIL_IMAP_PASSWORD", "")
MAIL_IMAP_FOLDER = env_str("MAIL_IMAP_FOLDER", "INBOX")
MAIL_POLL_SECONDS = env_int("MAIL_POLL_SECONDS", 300)
MAIL_NEWS_SERVICE_ID = env_str("MAIL_NEWS_SERVICE_ID", "algorand-foundation-mail")
MAIL_NEWS_DISPLAY_NAME = env_str("MAIL_NEWS_DISPLAY_NAME", "Algorand Foundation Mail")

SCRAPE_ENGINE_DEFAULT = env_str("SCRAPE_ENGINE_DEFAULT", "auto")
# Allowlist of domains the Playwright engine may render (SPAs / heavy-JS sites).
BROWSER_SCRAPE_DOMAINS = env_str("BROWSER_SCRAPE_DOMAINS", "")
BROWSER_HEADLESS = env_bool("BROWSER_HEADLESS", True)
BROWSER_TIMEOUT_MS = env_int("BROWSER_TIMEOUT_MS", 35_000)
BROWSER_WAIT_MS = env_int("BROWSER_WAIT_MS", 2500)
BROWSER_CHANNEL = env_str("BROWSER_CHANNEL", "")
BROWSER_STORAGE_STATE_PATH = env_str("BROWSER_STORAGE_STATE_PATH", "")

SCRAPE_COOLDOWN_SECONDS = env_int("SCRAPE_COOLDOWN_SECONDS", 3600)
# Exponential backoff on consecutive scrape failures (e.g. 429 storms):
# first failure waits the base, each subsequent one multiplies up to the cap.
# Reset to zero on the next successful scrape.
SCRAPE_BACKOFF_BASE_SECONDS = env_int("SCRAPE_BACKOFF_BASE_SECONDS", 600)
SCRAPE_BACKOFF_MAX_SECONDS = env_int("SCRAPE_BACKOFF_MAX_SECONDS", 21600)
SCRAPE_BACKOFF_MULTIPLIER = env_float("SCRAPE_BACKOFF_MULTIPLIER", 2.0)
# A host that no longer resolves (dead domain) or resolves to a non-public IP
# won't recover on the normal retry cadence — park it for a long while instead.
DEAD_HOST_COOLDOWN_SECONDS = env_int("DEAD_HOST_COOLDOWN_SECONDS", 604800)
WEEKLY_DIGEST_MAX_BODY_CHARS = env_int("WEEKLY_DIGEST_MAX_BODY_CHARS", 1500)
CHAIN_TAIL_MAX_ROUNDS_PER_RUN = env_int("CHAIN_TAIL_MAX_ROUNDS_PER_RUN", 8)

PRICE_ANALYSIS_ENABLED = env_bool("PRICE_ANALYSIS_ENABLED", True)
PRICE_ANALYSIS_ASSET_ID = env_str("PRICE_ANALYSIS_ASSET_ID", "algorand")
PRICE_ANALYSIS_SERVICE_ID = env_str("PRICE_ANALYSIS_SERVICE_ID", "weekly-digest")

WEEKLY_DIGEST_LOOKBACK_DAYS = env_int("WEEKLY_DIGEST_LOOKBACK_DAYS", 7)
WEEKLY_DIGEST_FEED_SCAN_LIMIT = env_int("WEEKLY_DIGEST_FEED_SCAN_LIMIT", 200)
WEEKLY_DIGEST_MAX_ARTICLES = env_int("WEEKLY_DIGEST_MAX_ARTICLES", 25)
WEEKLY_DIGEST_INCLUDE_FEED = env_bool("WEEKLY_DIGEST_INCLUDE_FEED", True)

PRICE_METRICS_ENABLED = env_bool("PRICE_METRICS_ENABLED", True)
PRICE_METRICS_ASSET_ID = env_str("PRICE_METRICS_ASSET_ID", PRICE_ANALYSIS_ASSET_ID)
PRICE_METRICS_POLL_SECONDS = env_int("PRICE_METRICS_POLL_SECONDS", 3600)
# CoinGecko politeness: cache responses in Redis so concurrent tasks (collector,
# weekly digest, price analysis) share one call and transient 429s serve stale.
COINGECKO_CACHE_TTL = env_int("COINGECKO_CACHE_TTL", 300)  # spot tick, 5 min
COINGECKO_WEEKLY_CACHE_TTL = env_int("COINGECKO_WEEKLY_CACHE_TTL", 3600)  # 7d chart, 1h
COINGECKO_NAME_TTL = env_int("COINGECKO_NAME_TTL", 604800)  # asset name is static, 7 days
COINGECKO_STALE_TTL = env_int("COINGECKO_STALE_TTL", 86400)  # last-good fallback on error
PRICE_METRICS_SAMPLE_LIMIT = env_int("PRICE_METRICS_SAMPLE_LIMIT", 200)
PRICE_METRICS_BRIEF_MAX_CHARS = env_int("PRICE_METRICS_BRIEF_MAX_CHARS", 4000)

MISTRAL_API_KEY = env_str("MISTRAL_API_KEY", "")
MISTRAL_API_BASE = env_str("MISTRAL_API_BASE", "https://api.mistral.ai/v1").rstrip("/")
MISTRAL_MODEL = env_str("MISTRAL_MODEL", "mistral-small-latest")
# The article writer runs an agentic tool loop. We track mistral-small-latest so
# it auto-rolls onto the newest Small (currently Small 4 = 2603), the best
# tool-caller on this account by a ToolCall-15 benchmark (2026-06-15): Small 4 83%
# (perfect Tool Selection + Error Recovery) > Devstral-2 / Medium-3.1 80% > Large-3
# 70%. Trade-off of "-latest": aliases can be throttled to a lower TPM tier than a
# dated pin — if that bites, pin a date (e.g. MISTRAL_MODEL_WRITER=mistral-small-2603).
# Moved to Medium (2026-06-19): better instruction-following / long-context
# adherence than Small, which reduces attention drift on the recency/temporal
# constraints (the chronological-collapse failures). NOTE: the ToolCall-15
# benchmark had Small ahead on raw tool-calling — if Stage-1 research tool
# reliability regresses, consider per-stage models (Small for research tools,
# Medium for Stage-2 generation) or re-pin Small here.
MISTRAL_MODEL_WRITER = env_str("MISTRAL_MODEL_WRITER", "mistral-medium-latest")
MISTRAL_MODEL_DIGEST = env_str("MISTRAL_MODEL_DIGEST", MISTRAL_MODEL)
MISTRAL_MODEL_PREMIUM = env_str("MISTRAL_MODEL_PREMIUM", "mistral-small-latest")
# Mistral Small 4 is a HYBRID reasoning model: the reasoning_effort param toggles
# between fast instruct ("none", ~Small 3.2 chat) and deep step-by-step reasoning
# ("high", ~Magistral verbosity). We run composition at "high". Set to "" to omit
# the param (e.g. when pinning a non-reasoning fallback model that rejects it).
# Mistral is used for composition only, so this applies to all Mistral calls.
MISTRAL_REASONING_EFFORT = env_str("MISTRAL_REASONING_EFFORT", "high")
# Output cap per call. A full JSON article (title+summary+long body+tags) can
# exceed 4096 and get truncated mid-string → JSON parse fails → template junk.
# 12000 comfortably fits a ~4000-word long-form article once JSON-escaped; the
# reservation against TPM is trivial on the 375k-TPM pinned model.
MISTRAL_MAX_TOKENS = env_int("MISTRAL_MAX_TOKENS", 12000)
# Writer agentic-loop context management. mistral-medium-latest exposes a 256k
# token window (mistral-small ~128k — override via env if you switch the writer
# model down); cost is not the constraint here, so tool results stay generous and
# we only elide the OLDEST ones when the whole conversation nears this limit.
MISTRAL_CONTEXT_TOKENS = env_int("MISTRAL_CONTEXT_TOKENS", 256000)
# Per-tool-result character cap (structure-preserving — see token_budget). Large
# enough to carry a full article body; ~24k chars ≈ a long page.
MISTRAL_TOOL_RESULT_MAX_CHARS = env_int("MISTRAL_TOOL_RESULT_MAX_CHARS", 24000)
# Headroom kept below the context limit (response max_tokens is reserved on top)
# so token-estimate error never tips a request over the edge.
MISTRAL_CONTEXT_SAFETY_TOKENS = env_int("MISTRAL_CONTEXT_SAFETY_TOKENS", 4000)
MISTRAL_TIMEOUT_SECONDS = env_int("MISTRAL_TIMEOUT_SECONDS", 120)
MISTRAL_ENABLED = env_bool("MISTRAL_ENABLED", False)
MISTRAL_FALLBACK_TEMPLATE = env_bool("MISTRAL_FALLBACK_TEMPLATE", True)
MISTRAL_MAX_SOURCE_CHARS = env_int("MISTRAL_MAX_SOURCE_CHARS", 12000)
# Periodic re-scrape of ALL monitored sources to detect content diffs and compose
# updates. Heavy (scrapes every source), so keep it infrequent — it is the writer's
# main background churn. 1h; lower only if you need faster update detection.
MISTRAL_DIFF_POLL_SECONDS = env_int("MISTRAL_DIFF_POLL_SECONDS", 3600)
# Agentic writer tool-round cap. 10 is plenty for genuine research; higher just
# lets a confused model loop (it was re-calling the same price/market tools 15-20x
# per article). The loop also dedupes identical (tool+args) calls as a backstop.
MISTRAL_MAX_TOOL_ROUNDS = env_int("MISTRAL_MAX_TOOL_ROUNDS", 10)
# Two-stage compose: a cold research pass (tools, low temp for deterministic tool
# calls) followed by a warm generation pass (tools removed, prompt swapped, higher
# temp to break AI-speak). Off = legacy single agentic loop at the write temp.
WRITER_TWO_STAGE = env_bool("WRITER_TWO_STAGE", True)
# Source-type router: a static landing page (root domain, no article path) is
# written as an evergreen profile, not breaking news — the deterministic fix for
# chronological context collapse (stale homepage banners written up as "just
# launched"). Off = always use the news prompt.
SOURCE_TYPE_ROUTER_ENABLED = env_bool("SOURCE_TYPE_ROUTER_ENABLED", True)
# Research-phase temperature: low so tool selection/arguments are deterministic.
MISTRAL_TEMP_RESEARCH = env_float("MISTRAL_TEMP_RESEARCH", 0.15)
# Generation-phase temperature: higher to vary prose structure.
MISTRAL_TEMP_WRITE = env_float("MISTRAL_TEMP_WRITE", 0.6)
# Two-stage compose: after generation, the heuristic grader runs deterministically
# (the warm pass has no tools, so the model can't call review_draft itself). A draft
# graded below this triggers exactly one revision pass with the issues fed back.
WRITER_REVIEW_ENABLED = env_bool("WRITER_REVIEW_ENABLED", True)
WRITER_REVIEW_MIN_GRADE = env_float("WRITER_REVIEW_MIN_GRADE", 7.0)
# Length is LAX: any article in [LENGTH_OK_MIN, LENGTH_OK_MAX] words is fine —
# length is not a graded dimension and not a target. Research DEPTH drives the
# grade instead, so the model fetches context rather than padding to a word count.
LENGTH_OK_MIN_WORDS = env_int("LENGTH_OK_MIN_WORDS", 250)
LENGTH_OK_MAX_WORDS = env_int("LENGTH_OK_MAX_WORDS", 2000)
# Stage-1 research FLOOR: after the research pass, if the writer made fewer than
# this many distinct tool calls (EXCLUDING review_draft self-checks), it is sent
# back once to dig deeper before it may write. Enforced mechanically, so research
# depth is no longer a graded dimension. Aim ~6 genuine research calls.
RESEARCH_MIN_TOOL_CALLS = env_int("RESEARCH_MIN_TOOL_CALLS", 6)
RESEARCH_FLOOR_ENABLED = env_bool("RESEARCH_FLOOR_ENABLED", True)
RESEARCH_FLOOR_MAX_PASSES = env_int("RESEARCH_FLOOR_MAX_PASSES", 1)
# Public base URL for linking to an article page from generated content (the
# weekly digest links each highlight here). Hash route into the Flutter SPA.
PUBLIC_ARTICLE_BASE_URL = env_str(
    "PUBLIC_ARTICLE_BASE_URL", "https://algorand.pxke.me/#/news/articles"
)
# Public site root (path-based, NO hash) — used to build canonical article URLs
# for IndexNow pings, matching the sitemap/SSR canonical URLs.
PUBLIC_SITE_URL = env_str("PUBLIC_SITE_URL", "https://algorand.pxke.me").rstrip("/")
# IndexNow key: instantly notifies Bing (→ Ecosia/DuckDuckGo/Yahoo), Yandex,
# Seznam and Naver when an article publishes. Public by design — also served as
# a static {key}.txt at the site root for verification. Empty disables pinging.
INDEXNOW_KEY = env_str("INDEXNOW_KEY", "63e7ffa13f3ca734700ca375c0581b41")
# Client-side rate limiting (Redis-coordinated across all workers). Spacing is a
# hard floor on time between calls (leaky bucket) — at least 15s/call to stay
# well under the per-second AND tokens/minute caps and avoid 429 storms.
MISTRAL_MIN_REQUEST_INTERVAL_SECONDS = env_float("MISTRAL_MIN_REQUEST_INTERVAL_SECONDS", 15.0)
MISTRAL_MAX_RETRIES = env_int("MISTRAL_MAX_RETRIES", 4)
# Retry backoff on 429 / 5xx / network errors: base * 2**attempt, capped. Honors
# Retry-After when present. Larger so a rate-limited call waits meaningfully.
MISTRAL_BACKOFF_BASE_SECONDS = env_float("MISTRAL_BACKOFF_BASE_SECONDS", 15.0)
MISTRAL_BACKOFF_MAX_SECONDS = env_float("MISTRAL_BACKOFF_MAX_SECONDS", 300.0)


def mistral_configured() -> bool:
    return MISTRAL_ENABLED and bool(MISTRAL_API_KEY.strip())


URL_QUEUE_ENABLED = env_bool("URL_QUEUE_ENABLED", True)
URL_QUEUE_DRAIN_SECONDS = env_int("URL_QUEUE_DRAIN_SECONDS", 60)
# How many URLs the drain crawls per tick. One per tick (paced by the beat
# interval) keeps requests gentle — ~1 page / 10s with URL_QUEUE_DRAIN_SECONDS=10.
URL_QUEUE_DRAIN_BATCH = env_int("URL_QUEUE_DRAIN_BATCH", 1)
# A newly approved domain harvests its first N pages at high priority (jumping
# the frontier queue), then its remaining pages fall back to normal priority.
CRAWL_INITIAL_HARVEST_TARGET = env_int("CRAWL_INITIAL_HARVEST_TARGET", 20)
CRAWL_INITIAL_HARVEST_PRIORITY = env_int("CRAWL_INITIAL_HARVEST_PRIORITY", 50)
# Hard cap on pages harvested per domain in a rolling window — stops crawl
# explosion on huge sites (e.g. allo.info indexes every on-chain transaction).
# Counts FETCHED pages (Redis), so low-relevance/unindexed pages still count.
CRAWL_MAX_PAGES_PER_DOMAIN = env_int("CRAWL_MAX_PAGES_PER_DOMAIN", 50)
CRAWL_PAGECOUNT_TTL = env_int("CRAWL_PAGECOUNT_TTL", 604800)  # 7d rolling window
# Recency gate: skip composing when a page's own publish date (from metadata) is
# older than this. No date on the page => not gated. Catches stale blog/news;
# the timeliness micro-check (separate) catches recent pages about past events.
RECENCY_GATE_ENABLED = env_bool("RECENCY_GATE_ENABLED", True)
PAGE_STALE_MAX_AGE_DAYS = env_int("PAGE_STALE_MAX_AGE_DAYS", 90)
# Cap NEW article composes per domain per rolling day — stops a churning page
# (news aggregator, SPA whose rendered text shifts each scrape) from being
# re-composed every poll and re-proposed in review over and over.
COMPOSE_MAX_PER_DOMAIN_PER_DAY = env_int("COMPOSE_MAX_PER_DOMAIN_PER_DAY", 1)
COMPOSE_DAILY_TTL = env_int("COMPOSE_DAILY_TTL", 86400)
# Minimum spacing between two articles from the same registrable domain. The
# daily count cap above bounds volume; this bounds *clustering* — it stops the
# same project being published twice in a short window (the "two Pera Wallet
# articles in 24h" case) even when both are under the daily count. 0 disables.
COMPOSE_DOMAIN_COOLDOWN_HOURS = env_int("COMPOSE_DOMAIN_COOLDOWN_HOURS", 168)

# Stage-1 relevance gate: drop a candidate before it ever enters the publish
# queue when the relevance scorer is confident the page is off-topic. Keeps
# clearly-irrelevant pages from being composed and proposed for review at all.
# The floor is intentionally low so borderline pages still flow through for
# classifier training — only confidently-irrelevant content is dropped.
ENQUEUE_RELEVANCE_GATE_ENABLED = env_bool("ENQUEUE_RELEVANCE_GATE_ENABLED", True)
ENQUEUE_RELEVANCE_MIN_SCORE = env_float("ENQUEUE_RELEVANCE_MIN_SCORE", 0.25)
# After an admin rejects a review, suppress re-enqueueing that exact URL for
# this long (seconds). Stops a rejected page re-entering the candidate queue the
# moment its content hash shifts again. Default 7 days.
URL_REJECT_COOLDOWN_TTL = env_int("URL_REJECT_COOLDOWN_TTL", 604800)
# Near-duplicate guard, applied AT COMPOSITION (not enqueue — the set of
# published articles can grow between a candidate being queued and composed).
# Skip composing when a recently published headline is at least this Jaccard-
# similar (title+tags tokens). 1.0 disables; ~0.8 = "almost the same headline".
NOVELTY_GATE_ENABLED = env_bool("NOVELTY_GATE_ENABLED", True)
# 0.6 = skip when 60%+ of significant headline tokens overlap a recent article.
# Title-token Jaccard catches near-identical headlines, not loose paraphrases
# (that needs embeddings); the post-compose grader novelty flags the softer cases.
NOVELTY_MAX_SIMILARITY = env_float("NOVELTY_MAX_SIMILARITY", 0.6)
# Content-level novelty: retrieve recently-published articles textually closest to
# a candidate from the Typesense articles index (title+summary+body), then score
# overlap against the candidate's title+summary tokens. Catches same-topic /
# different-headline dupes that the title-only Jaccard misses. Only articles
# published within this window are considered (0 disables the content check).
# Default covers the full decay horizon below so age-weighting can taper old
# matches rather than the window hard-cutting them off.
NOVELTY_CONTENT_WINDOW_HOURS = env_int("NOVELTY_CONTENT_WINDOW_HOURS", 24 * 70)
# Age-decay of the similarity penalty: a near-duplicate published within
# NOVELTY_DECAY_FULL_DAYS counts at full weight (novelty can hit 0); the weight
# then eases linearly to 0 by NOVELTY_DECAY_ZERO_DAYS, so re-covering a story is
# penalized hard for a week and freely allowed again after ~10 weeks.
NOVELTY_DECAY_FULL_DAYS = env_int("NOVELTY_DECAY_FULL_DAYS", 7)
NOVELTY_DECAY_ZERO_DAYS = env_int("NOVELTY_DECAY_ZERO_DAYS", 70)
# Selection ranking is driven by the trained signals ONLY — how relevant and how
# novel a candidate is. These two weights set the queue priority; the old
# topic/trust/service-weight heuristics are kept for observability but no longer
# decide what we write about (they over-rated off-topic service-discovery pages).
RELEVANCE_PRIORITY_WEIGHT = env_int("RELEVANCE_PRIORITY_WEIGHT", 100)
NOVELTY_PRIORITY_WEIGHT = env_int("NOVELTY_PRIORITY_WEIGHT", 100)

# YouTube transcripts (Stage 2) via a third-party API — the prod IP is anti-bot
# blocked for direct yt-dlp/captions. Provider-agnostic: URL template (may use
# {video_id} / {video_url}), API key, and the auth header name. Inert until set.
YOUTUBE_TRANSCRIPT_ENABLED = env_bool("YOUTUBE_TRANSCRIPT_ENABLED", False)
YOUTUBE_TRANSCRIPT_API_URL = env_str("YOUTUBE_TRANSCRIPT_API_URL", "")
YOUTUBE_TRANSCRIPT_API_KEY = env_str("YOUTUBE_TRANSCRIPT_API_KEY", "")
YOUTUBE_TRANSCRIPT_AUTH_HEADER = env_str("YOUTUBE_TRANSCRIPT_AUTH_HEADER", "x-api-key")
YOUTUBE_TRANSCRIPT_TIMEOUT = env_int("YOUTUBE_TRANSCRIPT_TIMEOUT", 30)
# Pay the (metered) transcript API at most once per video, even when the video
# is later skipped by the enqueue gate and so never leaves a snapshot. Bounds
# credit burn; transient failures may retry after this TTL. 30 days.
YOUTUBE_TRANSCRIPT_ATTEMPT_TTL = env_int("YOUTUBE_TRANSCRIPT_ATTEMPT_TTL", 2592000)
DISCOVERY_MODE_ENABLED = env_bool("DISCOVERY_MODE_ENABLED", True)
EXTERNAL_LINK_DISCOVERY_ENABLED = env_bool("EXTERNAL_LINK_DISCOVERY_ENABLED", True)
SPA_FALLBACK_ENABLED = env_bool("SPA_FALLBACK_ENABLED", True)
CONTENT_CATEGORIZATION_ENABLED = env_bool("CONTENT_CATEGORIZATION_ENABLED", True)
PUBLISH_CLASSIFIER_ENABLED = env_bool("PUBLISH_CLASSIFIER_ENABLED", True)
PUBLISH_CLASSIFIER_MODEL_PATH = env_str(
    "PUBLISH_CLASSIFIER_MODEL_PATH",
    "data/models/publish_classifier.pkl",
)
# Learned article grader: logistic regression on the captured grade dimensions
# → P(approved). Falls back to the heuristic weighted sum below the min-sample
# threshold (cold start). Both classes must be present to train.
GRADER_MODEL_PATH = env_str("GRADER_MODEL_PATH", "data/models/article_grader.pkl")
GRADER_MIN_SAMPLES = env_int("GRADER_MIN_SAMPLES", 40)
# The grader becomes text-aware (TF-IDF of the article body hstacked with the
# heuristic subscores) only once at least this many labelled rows carry the
# article text — below it, the text features would just memorise. Until then it
# trains scalar-only. Article text is captured from the deploy on 2026-06-18, so
# this ramps up as you label new reviews.
GRADER_TEXT_MIN_SAMPLES = env_int("GRADER_TEXT_MIN_SAMPLES", 60)
# Article length grading: the grader's length subscore is a Gaussian peaking at
# LENGTH_TARGET_WORDS, width LENGTH_SIGMA_WORDS. Editorial preference is ~800-word
# pieces, so both thin stubs and bloated walls score low (≈0.6 at ±sigma; with
# σ=350 the >0.6 band is roughly 500–1100 words).
LENGTH_TARGET_WORDS = env_int("LENGTH_TARGET_WORDS", 800)
LENGTH_SIGMA_WORDS = env_int("LENGTH_SIGMA_WORDS", 350)
CLASSIFIER_CONFIDENCE_THRESHOLD = float(os.getenv("CLASSIFIER_CONFIDENCE_THRESHOLD", "0.8"))
CLASSIFIER_SAMPLING_THRESHOLD = float(os.getenv("CLASSIFIER_SAMPLING_THRESHOLD", "0.0"))
CLASSIFIER_RETRAIN_CRON_HOUR = env_int("CLASSIFIER_RETRAIN_CRON_HOUR", 3)
CLASSIFIER_RETRAIN_CRON_MINUTE = env_int("CLASSIFIER_RETRAIN_CRON_MINUTE", 30)

# Max Mistral compositions held for admin review per drain run (cost/burst cap).
REVIEW_COMPOSE_BATCH_LIMIT = env_int("REVIEW_COMPOSE_BATCH_LIMIT", 3)

# Follow links found on crawled web pages (one hop per crawl, bounded by the
# frontier gate in domain_tracker and per-page caps below).
WEB_LINK_DISCOVERY_ENABLED = env_bool("WEB_LINK_DISCOVERY_ENABLED", True)
# Comma-separated extra dead-end domains for the frontier blocklist.
FRONTIER_BLOCKLIST_EXTRA = env_str("FRONTIER_BLOCKLIST_EXTRA", "")

# Probability that a newly discovered domain must be classified by an admin
# before the crawler may explore it (1.0 = every new domain is held).
FRONTIER_SAMPLING_THRESHOLD = float(os.getenv("FRONTIER_SAMPLING_THRESHOLD", "1.0"))
# Pre-enqueue relevance gate: an unknown external domain whose link (URL +
# anchor text) shows no crypto/Algorand signal is dropped outright — not even
# previewed or held — so off-topic domains (realtor.com, jwplayer, ...) never
# enter the frontier. Approved domains are unaffected.
# DISABLED: pre-crawl relevance heuristics on URL/anchor/preview-meta wrongly
# dead-ended real Algorand domains (pact.fi, perawallet, algorand.co) — a landing
# page's metadata is a poor relevance signal. Relevance is now judged on the
# CRAWLED page content (scrape_from_queue_item's classifier), never on preview.
FRONTIER_LINK_RELEVANCE_GATE = env_bool("FRONTIER_LINK_RELEVANCE_GATE", False)
FRONTIER_PREVIEW_AUTOREJECT = env_bool("FRONTIER_PREVIEW_AUTOREJECT", False)
FRONTIER_PREVIEW_MIN_SCORE = env_float("FRONTIER_PREVIEW_MIN_SCORE", 1.0)
# Content-based domain relevance (classify_pending_domains): a pending domain
# whose CRAWLED page text scores below this is clearly off-topic. Used only when
# that task is invoked with auto_reject=True (validate the scores first).
FRONTIER_CONTENT_REJECT_SCORE = env_float("FRONTIER_CONTENT_REJECT_SCORE", 0.2)
# Score-gated frontier auto-approve: when enabled, a newly discovered unknown
# domain whose landing-page preview score (0-10 keyword+classifier scale, same as
# FRONTIER_PREVIEW_MIN_SCORE) is at least FRONTIER_AUTO_APPROVE_SCORE — or whose
# name carries an Algorand signal — is approved and crawled immediately instead of
# held for admin review. Below the threshold it is STILL held pending (never
# auto-rejected). Default off so review stays the safe baseline; raise the score
# to be stricter, lower it for more reach.
FRONTIER_AUTO_APPROVE_ENABLED = env_bool("FRONTIER_AUTO_APPROVE_ENABLED", False)
FRONTIER_AUTO_APPROVE_SCORE = env_float("FRONTIER_AUTO_APPROVE_SCORE", 3.0)
# Cap on inline landing-page previews fetched per crawled page. Each preview is a
# blocking HTTP GET + classifier pass, so a link-heavy page could otherwise stall
# one drain task for minutes; unknown domains past the cap are still HELD pending
# (a human / the classify_pending_domains task previews them later), never dropped.
FRONTIER_PREVIEW_MAX_PER_PAGE = env_int("FRONTIER_PREVIEW_MAX_PER_PAGE", 8)
# Auto-flagged (non-admin) irrelevant domains are re-checked after this many days,
# in case they became relevant. Admin rejects are permanent regardless.
FRONTIER_RECRAWL_DAYS_IRRELEVANT = env_int("FRONTIER_RECRAWL_DAYS_IRRELEVANT", 7)

# Hard cap on items waiting in the admin classifier queue. Once reached, the
# pipeline stops composing/holding new reviews until the admin clears some.
MAX_PENDING_REVIEWS = env_int("MAX_PENDING_REVIEWS", 1)

# Minimum spacing between two articles released to the feed from the approved
# queue (admin-approved, over-cap items). Default 1 hour.
APPROVED_FEED_MIN_GAP_SECONDS = env_int("APPROVED_FEED_MIN_GAP_SECONDS", 3600)
# Whether a backlog of admin-approved articles still awaiting the (paced, ~1/h)
# feed release should PAUSE new-content intake (the diff-check / "pull top
# topic"). Default False: classification intake is upstream of and independent
# from the public-feed drip — a couple of approvals must not starve the review
# queue for hours.
PAUSE_INTAKE_ON_FEED_BACKLOG = env_bool("PAUSE_INTAKE_ON_FEED_BACKLOG", False)

# Let the Mistral writer call live tools (price, chain head, platform search)
# on demand while composing. Off = single-shot prompt with pre-gathered context.
WRITER_TOOLS_ENABLED = env_bool("WRITER_TOOLS_ENABLED", True)

# Investigative-journalism tools for the writer agent (Phase 1: free/no-key
# OSINT lookups). Optional API keys: OPENSANCTIONS_API_KEY,
# OPENCORPORATES_API_TOKEN, COURTLISTENER_TOKEN, GITHUB_TOKEN.
INVESTIGATIVE_TOOLS_ENABLED = env_bool("INVESTIGATIVE_TOOLS_ENABLED", True)

# Free public Bluesky post search for community sentiment (no Twitter/X, no key).
BLUESKY_SEARCH_ENABLED = env_bool("BLUESKY_SEARCH_ENABLED", True)

# Self-hosted SearXNG metasearch for general web research (no Google, no key,
# no per-query cost). Empty = web search tool disabled.
SEARXNG_URL = env_str("SEARXNG_URL", "").rstrip("/")

# MTTH gatekeeper — deterministic pre-publish gate (completeness rules +
# trace<->article numeric entailment). Runs before the learned ModernBERT model
# exists, so it gates on the cheap signals first.
#   ENABLED  : compute the signals and attach them to the review metadata.
#   ENFORCE  : actually act on a failure. Default OFF — publishing is
#              outward-facing, so ship in shadow mode and flip to enforce once
#              the signals are trusted on real drafts.
GATEKEEPER_ENABLED = env_bool("GATEKEEPER_ENABLED", True)
GATEKEEPER_ENFORCE = env_bool("GATEKEEPER_ENFORCE", False)
# Article is flagged when the grounded fraction of its numeric claims falls below
# this (too many figures with no anchor in the tool trace).
GATEKEEPER_FACT_MIN = env_float("GATEKEEPER_FACT_MIN", 0.80)
# Trained MTTH model (state_dict from gatekeeper.training). When this file is
# absent the model heads are dormant: the deterministic gate still runs, and the
# article grader falls back to the sklearn grader, then the heuristic floor.
GATEKEEPER_MODEL_PATH = env_str("GATEKEEPER_MODEL_PATH", "data/models/gatekeeper_mtth.pt")
