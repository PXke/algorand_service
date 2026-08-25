"""Environment-driven configuration for the workers service."""

from __future__ import annotations

import os
from pathlib import Path


def env_str(name: str, default: str) -> str:
    """Read a string environment variable, falling back to `default` when unset."""
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to `default` when unset/blank."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable, falling back to `default` when unset/blank."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_float(name: str, default: float) -> float:
    """Read a float environment variable, falling back to `default` when unset/blank."""
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


def _algod_token() -> str:
    """The literal ALGOD_TOKEN env var if set; otherwise read it from ALGOD_TOKEN_FILE.

    prod's local algod writes its token to /var/lib/algorand/algod.token -- workers.env
    only ever set the FILE var, never the literal token itself, so this was silently
    resolving to "" and every chain lookup 401'd against the local node -- root-caused
    live 2026-08-17 mid-recompose-batch via a real "lookup_asset ... 401 Unauthorized"
    error, not caught by any test since ALGOD_TOKEN_FILE was never exercised locally.
    """
    token = env_str("ALGOD_TOKEN", "").strip()
    if token:
        return token
    path = env_str("ALGOD_TOKEN_FILE", "").strip()
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# Sent as the X-Algo-API-Token header when set (prod node requires it; algonode does not).
ALGOD_TOKEN = _algod_token()
# Public AlgoNode testnet INDEXER (history-capable, unlike algod) — backs the
# writer's testnet_lookup tool for verifying a project's Testnet txns/app deploys.
TESTNET_INDEXER_URL = env_str("TESTNET_INDEXER_URL", "https://testnet-idx.algonode.cloud").rstrip(
    "/"
)
# Public AlgoNode testnet ALGOD-compatible endpoint -- the indexer above has
# no concept of box storage, so reading a contract's boxes (e.g. counting how
# many entries a Testnet registry app actually holds) needs algod's own
# /v2/applications/{id}/boxes route instead.
TESTNET_ALGOD_URL = env_str("TESTNET_ALGOD_URL", "https://testnet-api.algonode.cloud").rstrip("/")
# Public AlgoNode MAINNET indexer — read-only, name-search capable (unlike
# algod, which only looks up assets by numeric id). Backs lookup_asset_by_name.
MAINNET_INDEXER_URL = env_str("MAINNET_INDEXER_URL", "https://mainnet-idx.algonode.cloud").rstrip(
    "/"
)
NEWS_FEED_BUCKET = env_str("NEWS_FEED_BUCKET", "main")
NEWS_MAX_ARTICLES_PER_DAY = min(max(1, env_int("NEWS_MAX_ARTICLES_PER_DAY", 3)), 7)
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
# page every time a link is rediscovered. 30 days.
CRAWL_URL_RECRAWL_COOLDOWN_SECONDS = env_int("CRAWL_URL_RECRAWL_COOLDOWN_SECONDS", 30 * 86400)

WRITER_ENRICHMENT_ENABLED = env_bool("WRITER_ENRICHMENT_ENABLED", True)
WRITER_ENRICHMENT_PROBE_DOMAIN = env_bool("WRITER_ENRICHMENT_PROBE_DOMAIN", True)
WRITER_ENRICHMENT_FETCH_TWEETS = env_bool("WRITER_ENRICHMENT_FETCH_TWEETS", True)
# Gates the editorial-assignment pipeline (app.modules.newspaper.editorial_assignment):
# admin-authored briefs actively enqueue a "write this topic" article on creation
# and, on their cadence, an in-place refresh of the resulting article.
WRITER_EDITORIAL_BRIEFS_ENABLED = env_bool("WRITER_EDITORIAL_BRIEFS_ENABLED", True)

ARTICLE_EDIT_WINDOW_HOURS = env_int("ARTICLE_EDIT_WINDOW_HOURS", 24)
NEWS_STANDARD_INTERVAL_HOURS = env_int("NEWS_STANDARD_INTERVAL_HOURS", 8)
NEWS_MIN_DIFF_LINES = env_int("NEWS_MIN_DIFF_LINES", 3)
# CONTENT_UPDATE-specific relevance floor: a service-diff item below this is
# never enqueued at all, regardless of diff size. Kept separate from (and
# stricter than) FRONTIER_CONTENT_REJECT_SCORE below, which governs initial
# domain discovery and is deliberately lenient on a page's first crawl.
CONTENT_UPDATE_RELEVANCE_FLOOR = env_float("CONTENT_UPDATE_RELEVANCE_FLOOR", 0.35)
# Reformat/reshuffle diff veto: when the diff's ADDED text is mostly the same
# words as its REMOVED text (token-overlap ratio at/above this), the page was
# redesigned/reflowed, not updated — no new information, no story. Three real
# "nothing happened" articles motivated this (Tinyman Medium reformat,
# zk-colorsort date tick, Blockshake redesign). 0 disables.
NEWS_REFORMAT_SIMILARITY = env_float("NEWS_REFORMAT_SIMILARITY", 0.85)
# Hard cap on a crawled page's stored body — a real page's readable text never
# gets remotely close to this; something this large is almost always non-text
# content misread as a page (a binary file like an .apk, an oversized asset)
# rather than a legitimately huge article. Root-caused 2026-08-06: a 23MB body
# blew straight past Cassandra's 16MB native-protocol message limit as an
# UNCAUGHT InvalidRequest, failing the whole index_crawled_page task. 500K
# chars leaves ~32x headroom under that limit for the rest of the row.
CRAWLED_PAGE_BODY_MAX_CHARS = env_int("CRAWLED_PAGE_BODY_MAX_CHARS", 500_000)
PUBLISH_QUEUE_DRAIN_SECONDS = env_int("PUBLISH_QUEUE_DRAIN_SECONDS", 900)

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

# capture_screenshot tool (2026-08-11): where captured PNGs are saved on disk
# and the public URL prefix nginx serves them under. STORAGE_DIR must be
# OUTSIDE any release dir -- releases get replaced wholesale on every deploy,
# but a screenshot embedded in a live article must keep resolving. See
# deploy/nginx/algorand-platform.conf's /media/screenshots/ location, which
# serves this same directory from the persistent `shared/` tree, not a
# release path. Empty STORAGE_DIR is a deliberate kill switch: the tool
# refuses rather than silently writing to some default path that may not
# exist or be writable on a given host.
SCREENSHOT_STORAGE_DIR = env_str("SCREENSHOT_STORAGE_DIR", "")
SCREENSHOT_PUBLIC_BASE_URL = env_str(
    "SCREENSHOT_PUBLIC_BASE_URL", "https://algorand.pxke.me/media/screenshots"
)

# play_interactive tool (2026-08-11, owner request: discover a game's actual
# mechanics through a few real clicks/inputs, not master or complete it).
# Bounded on purpose -- exploring a system takes a handful of steps; an
# unbounded budget would let one compose turn into an open-ended playthrough
# and burn the compose's time/cost budget on a single tool.
PLAY_INTERACTIVE_MAX_STEPS = env_int("PLAY_INTERACTIVE_MAX_STEPS", 8)

# connect_wallet tool (2026-08-11, agent-wallet Phase 1: WalletConnect LOGIN
# only, see workers/app/modules/wallet/signer.py's docstring for the actual
# security boundary). Off by default -- a dedicated MainNet keypair (holds
# real ALGO) whose signing is allowlisted down to zero capability to move
# value or change account control, but still gated behind an explicit
# opt-in like every other capability-boundary flag here (INVESTIGATIVE_TOOLS_
# ENABLED, MISTRAL_ENABLED) rather than being on by default.
AGENT_WALLET_ENABLED = env_bool("AGENT_WALLET_ENABLED", False)
# Read only inside workers/app/modules/wallet/signer.py. Empty = not configured.
AGENT_WALLET_MNEMONIC = env_str("AGENT_WALLET_MNEMONIC", "")
# Separate, smaller budget from PLAY_INTERACTIVE_MAX_STEPS -- connect_wallet is
# a materially higher-stakes action (it signs something, even if narrowly
# scoped) than a generic click/type.
WALLET_CONNECT_MAX_PER_COMPOSE = env_int("WALLET_CONNECT_MAX_PER_COMPOSE", 1)

SCRAPE_COOLDOWN_SECONDS = env_int("SCRAPE_COOLDOWN_SECONDS", 3600)
# Watch cadence for monitored services: a healthy source is re-scraped for
# diffs at most once per this many days (the diff IS the update story, so
# polling faster only burns requests). Fractional values work for testing;
# <= 0 falls back to SCRAPE_COOLDOWN_SECONDS. Failed polls retry on the
# shorter SCRAPE_COOLDOWN_SECONDS instead of losing a whole window.
#
# Raised from 7 to 30 (2026-08-02, NFDomains incident): weekly was frequent
# enough that a service with no real news still got a full re-explainer
# article every few weeks, reworded around whatever headline stat had ticked
# since the last poll. A real product update deserves reporting whenever it
# happens; a page that's simply still online doesn't need re-introducing
# every week. See also ARTICLE_DUPLICATE_BODY_SIMILARITY, which catches the
# same failure mode after the fact if this cadence alone isn't enough.
SERVICE_RESCRAPE_DAYS = env_float("SERVICE_RESCRAPE_DAYS", 30.0)
# Service-watch context: the snapshot/diff/compose unit for a web service is an
# AGGREGATE of its recently harvested pages across all of the service's domains
# (never just the homepage). ~48k chars ≈ 12k tokens for the composer. Sections
# are ordered by URL so the aggregate is stable and the weekly diff shows real
# content evolution, not page reshuffling.
SERVICE_CONTEXT_ENABLED = env_bool("SERVICE_CONTEXT_ENABLED", True)
SERVICE_CONTEXT_MAX_CHARS = env_int("SERVICE_CONTEXT_MAX_CHARS", 48_000)
SERVICE_CONTEXT_MAX_PAGES = env_int("SERVICE_CONTEXT_MAX_PAGES", 12)
SERVICE_CONTEXT_PER_PAGE_CHARS = env_int("SERVICE_CONTEXT_PER_PAGE_CHARS", 6_000)
SERVICE_CONTEXT_MAX_AGE_DAYS = env_int("SERVICE_CONTEXT_MAX_AGE_DAYS", 30)

# Bluesky source lane (opt-in via CRAWLER_BLUESKY_ENABLED). A service whose
# scrape_url is a bsky.app profile is polled through the free public AppView;
# each original post becomes a publish signal. Cap posts read per source poll.
BLUESKY_MAX_POSTS_PER_SOURCE = env_int("BLUESKY_MAX_POSTS_PER_SOURCE", 20)
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
# Chain-tools indexer/algod cache: tiered by how fast the underlying data
# actually changes (same philosophy as COINGECKO_*_TTL above).
CHAIN_CACHE_TTL_STATIC = env_int("CHAIN_CACHE_TTL_STATIC", 604800)  # 7d: permanent once confirmed (asset params, historical blocks/txns)
CHAIN_CACHE_TTL_SLOW = env_int("CHAIN_CACHE_TTL_SLOW", 300)  # 5m: changes with activity, not sub-minute (app state, holder balances, tx aggregates)
CHAIN_CACHE_TTL_FAST = env_int("CHAIN_CACHE_TTL_FAST", 60)  # 1m: current-state snapshots (account balance, consensus stats)
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
# Model split (owner decision, 2026-07-12): LARGE writes — final prose and the
# digest need the strongest instruction-following (headline rules, format
# rules, banned lexicon) — while SMALL does the mechanical work: the agentic
# tool loop (Small 4 leads the ToolCall-15 benchmark: 83% > Medium-3.1 80% >
# Large-3 70%, 2026-06-15) and translations. Prod workers.env has pinned
# writer/digest to Large since 2026-07-10; these defaults now match so losing
# an env line can't silently downgrade the writer.
MISTRAL_MODEL_WRITER = env_str("MISTRAL_MODEL_WRITER", "mistral-large-latest")
# Agentic research tool loop + research-floor nudges. Small 4 leads on tool
# selection and error recovery; research does not need Large's prose fidelity.
MISTRAL_MODEL_RESEARCH = env_str("MISTRAL_MODEL_RESEARCH", "mistral-small-latest")
# Research Digest synthesis (Stage 1→2 handoff): also reader-facing prose.
MISTRAL_MODEL_DIGEST = env_str("MISTRAL_MODEL_DIGEST", "mistral-large-latest")
MISTRAL_MODEL_PREMIUM = env_str("MISTRAL_MODEL_PREMIUM", "mistral-small-latest")
# Translations are mechanical localization of an already-written article — no
# research, no tools, no editorial judgment — and fire 5x per published article
# (one per language), so they don't need (or justify) the writer's Large tier.
MISTRAL_MODEL_TRANSLATE = env_str("MISTRAL_MODEL_TRANSLATE", "mistral-small-latest")
# Mistral Small 4 is a HYBRID reasoning model: the reasoning_effort param toggles
# between fast instruct ("none", ~Small 3.2 chat) and deep step-by-step reasoning
# ("high", ~Magistral verbosity). We run composition at "high". Set to "" to omit
# the param (e.g. when pinning a non-reasoning fallback model that rejects it).
# Mistral is used for composition only, so this applies to all Mistral calls.
MISTRAL_REASONING_EFFORT = env_str("MISTRAL_REASONING_EFFORT", "high")

# DeepSeek: a second provider behind the same OpenAI-compatible connector
# (llm_openai_compatible.MistralProvider already speaks plain /chat/completions
# JSON, nothing Mistral-specific in the wire format) — additive, off by default
# (empty key), never changes Mistral's own behavior. Confirm these model
# identifiers against DeepSeek's current docs before relying on them; the
# marketing names (e.g. "DeepSeek-V4-Flash") are not always the literal API
# `model` string.
DEEPSEEK_API_KEY = env_str("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = env_str("DEEPSEEK_API_BASE", "https://api.deepseek.com").rstrip("/")
DEEPSEEK_MODEL_WRITER = env_str("DEEPSEEK_MODEL_WRITER", "deepseek-chat")
DEEPSEEK_MODEL_RESEARCH = env_str("DEEPSEEK_MODEL_RESEARCH", "deepseek-chat")
DEEPSEEK_MODEL_DIGEST = env_str("DEEPSEEK_MODEL_DIGEST", "deepseek-chat")
DEEPSEEK_MODEL_TRANSLATE = env_str("DEEPSEEK_MODEL_TRANSLATE", "deepseek-chat")
# Per-language override: languages in this list translate via DeepSeek
# (translate_article) instead of the local CPU engines, independent
# of LLM_PROVIDER_TRANSLATE's global mistral/deepseek routing. Multi-article
# side-by-side testing (2026-08-23) found local quality is a genuine wash
# against DeepSeek for most languages -- some local wins, some DeepSeek wins,
# never by much -- except Pashto, where the local seq2seq engine
# (SeamlessM4T) repeatedly collapsed list/table-heavy blocks into
# repetition-loop degeneration, once destroying every citation in a source
# list outright. DeepSeek cost is negligible (this account's entire DeepSeek
# usage that day totaled 16 cents), so there's no cost reason to keep a
# language on a confirmed-broken local path. Comma-separated language codes;
# empty entries ignored.
DEEPSEEK_TRANSLATE_LANGS = frozenset(
    lang.strip() for lang in env_str("DEEPSEEK_TRANSLATE_LANGS", "ps").split(",") if lang.strip()
)
# The LLM quality rubric (article_quality_llm.py) shares the research-tier
# Mistral model but gets its OWN provider knob, separate from research's
# LLM_PROVIDER_RESEARCH — a compose can route its tool-calling research loop
# to one provider while keeping the rubric on another (e.g. DeepSeek's extra
# research depth is worth it, but Mistral's rubric grading is trusted more
# while DeepSeek's is newer/less proven).
DEEPSEEK_MODEL_RUBRIC = env_str("DEEPSEEK_MODEL_RUBRIC", "deepseek-chat")
# DeepSeek's thinking mode returns reasoning in a separate reasoning_content
# field, but BOTH reasoning_content and content draw from the same max_tokens
# budget (visible as usage.completion_tokens_details.reasoning_tokens) —
# root-caused 2026-08-05: a real article-write call under MISTRAL_MAX_TOKENS
# (12000, tuned for Mistral, which doesn't visibly consume budget on
# reasoning the same way) burned its entire budget on reasoning_content and
# came back with an EMPTY content field, failing as "non-JSON content: ".
# DeepSeek supports up to 384k output tokens, so there's ample room for a
# larger ceiling without approaching a real limit.
DEEPSEEK_MAX_TOKENS = env_int("DEEPSEEK_MAX_TOKENS", 40000)

# Benchmark-candidate providers (2026-08-14, ahead of a DeepSeek pricing
# change): same additive shape as DEEPSEEK_* above -- empty API key by
# default, never changes existing behavior. MODEL_* defaults are the exact
# names supplied when this work was scoped (Gemini 3.7 / GPT 5.6 "Luna" /
# Kimi K3 / GLM 5.2) -- placeholders to override once the real API's exact
# model-ID string is confirmed, not verified against any provider's docs.
OPENAI_API_KEY = env_str("OPENAI_API_KEY", "")
OPENAI_API_BASE = env_str("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL_WRITER = env_str("OPENAI_MODEL_WRITER", "gpt-5.6-luna")
OPENAI_MODEL_RESEARCH = env_str("OPENAI_MODEL_RESEARCH", "gpt-5.6-luna")
OPENAI_MODEL_DIGEST = env_str("OPENAI_MODEL_DIGEST", "gpt-5.6-luna")
OPENAI_MODEL_TRANSLATE = env_str("OPENAI_MODEL_TRANSLATE", "gpt-5.6-luna")
OPENAI_MODEL_RUBRIC = env_str("OPENAI_MODEL_RUBRIC", "gpt-5.6-luna")

KIMI_API_KEY = env_str("KIMI_API_KEY", "")
KIMI_API_BASE = env_str("KIMI_API_BASE", "https://api.moonshot.ai/v1").rstrip("/")
KIMI_MODEL_WRITER = env_str("KIMI_MODEL_WRITER", "kimi-k2.7-code")
KIMI_MODEL_RESEARCH = env_str("KIMI_MODEL_RESEARCH", "kimi-k2.7-code")
KIMI_MODEL_DIGEST = env_str("KIMI_MODEL_DIGEST", "kimi-k2.7-code")
KIMI_MODEL_TRANSLATE = env_str("KIMI_MODEL_TRANSLATE", "kimi-k2.7-code")
KIMI_MODEL_RUBRIC = env_str("KIMI_MODEL_RUBRIC", "kimi-k2.7-code")
# Same shape as DEEPSEEK_MAX_TOKENS above: confirmed live 2026-08-14 that
# Kimi K3's thinking cannot be disabled at any setting, and its reasoning
# tokens draw from the same completion budget as the visible answer -- a
# small explicit max_tokens (e.g. the LLM quality rubric's 800) risks the
# exact 2026-08-06 DeepSeek incident (reasoning consumes the whole budget,
# content comes back empty) recurring on a different provider.
KIMI_MAX_TOKENS = env_int("KIMI_MAX_TOKENS", 40000)

GLM_API_KEY = env_str("GLM_API_KEY", "")
GLM_API_BASE = env_str("GLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4").rstrip("/")
GLM_MODEL_WRITER = env_str("GLM_MODEL_WRITER", "glm-5.2")
GLM_MODEL_RESEARCH = env_str("GLM_MODEL_RESEARCH", "glm-5.2")
GLM_MODEL_DIGEST = env_str("GLM_MODEL_DIGEST", "glm-5.2")
GLM_MODEL_TRANSLATE = env_str("GLM_MODEL_TRANSLATE", "glm-5.2")
GLM_MODEL_RUBRIC = env_str("GLM_MODEL_RUBRIC", "glm-5.2")

# Gemini's native API is NOT OpenAI-compatible (contents/parts, functionCall,
# role="model") -- GeminiProvider (llm_gemini_provider.py) translates to/from
# it, unlike the OpenAI-compatible providers above which share one wire format.
GEMINI_API_KEY = env_str("GEMINI_API_KEY", "")
GEMINI_API_BASE = env_str(
    "GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta"
).rstrip("/")
GEMINI_MODEL_WRITER = env_str("GEMINI_MODEL_WRITER", "gemini-3.7")
GEMINI_MODEL_RESEARCH = env_str("GEMINI_MODEL_RESEARCH", "gemini-3.7")
GEMINI_MODEL_DIGEST = env_str("GEMINI_MODEL_DIGEST", "gemini-3.7")
GEMINI_MODEL_TRANSLATE = env_str("GEMINI_MODEL_TRANSLATE", "gemini-3.7")
GEMINI_MODEL_RUBRIC = env_str("GEMINI_MODEL_RUBRIC", "gemini-3.7")

# Anthropic's Messages API is also not OpenAI-compatible (top-level `system`
# field separate from `messages`, tool_use/tool_result content blocks instead
# of tool_calls, `usage.{input,output}_tokens` instead of `usage.
# {prompt,completion}_tokens") -- AnthropicProvider (llm_anthropic_provider.py)
# translates to/from it, same reasoning as Gemini above. Added 2026-08-14 at
# the owner's request to include Claude Sonnet 5 in the provider comparison.
ANTHROPIC_API_KEY = env_str("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_BASE = env_str("ANTHROPIC_API_BASE", "https://api.anthropic.com/v1").rstrip("/")
ANTHROPIC_MODEL_WRITER = env_str("ANTHROPIC_MODEL_WRITER", "claude-sonnet-5")
ANTHROPIC_MODEL_RESEARCH = env_str("ANTHROPIC_MODEL_RESEARCH", "claude-sonnet-5")
ANTHROPIC_MODEL_DIGEST = env_str("ANTHROPIC_MODEL_DIGEST", "claude-sonnet-5")
ANTHROPIC_MODEL_TRANSLATE = env_str("ANTHROPIC_MODEL_TRANSLATE", "claude-sonnet-5")
ANTHROPIC_MODEL_RUBRIC = env_str("ANTHROPIC_MODEL_RUBRIC", "claude-sonnet-5")

# "synthesize" (default): Stage 1->2 handoff is an LLM-synthesized Research
# Digest (llm_compose._synthesize_research_digest) — the only thing Stage
# 2 sees, no raw trace. "raw": skip that synthesis pass entirely and hand
# Stage 2 the (generously capped, not summarized) raw tool trace directly —
# an experiment enabled by a large-context provider (DeepSeek's 1M) not
# needing the compression a smaller context forced. Comparing the two is the
# point: synthesis is a real lossy step (see 2026-08-05 digest-drop fixes)
# but so is any compression scheme; this isn't assumed better, just testable.
RESEARCH_DIGEST_MODE = env_str("RESEARCH_DIGEST_MODE", "synthesize").strip().lower()

# Per-purpose provider routing: "mistral" (default, unchanged behavior) or
# "deepseek". CANARY_PCT (0-100) sends that percentage of calls to the OTHER
# provider instead of the configured default, so a purpose can stay put while
# sampling the alternative for comparison — compose_sessions.model records
# whichever model actually ran, so a canary is visible after the fact with no
# extra plumbing. A canary or explicit override that resolves to "deepseek"
# silently falls back to Mistral if DEEPSEEK_API_KEY is unset (see
# llm_purpose_router._select_provider).
LLM_PROVIDER_WRITER = env_str("LLM_PROVIDER_WRITER", "mistral").strip().lower()
LLM_PROVIDER_WRITER_CANARY_PCT = env_int("LLM_PROVIDER_WRITER_CANARY_PCT", 0)
LLM_PROVIDER_RESEARCH = env_str("LLM_PROVIDER_RESEARCH", "mistral").strip().lower()
LLM_PROVIDER_RESEARCH_CANARY_PCT = env_int("LLM_PROVIDER_RESEARCH_CANARY_PCT", 0)
LLM_PROVIDER_DIGEST = env_str("LLM_PROVIDER_DIGEST", "mistral").strip().lower()
LLM_PROVIDER_DIGEST_CANARY_PCT = env_int("LLM_PROVIDER_DIGEST_CANARY_PCT", 0)
LLM_PROVIDER_TRANSLATE = env_str("LLM_PROVIDER_TRANSLATE", "mistral").strip().lower()
LLM_PROVIDER_TRANSLATE_CANARY_PCT = env_int("LLM_PROVIDER_TRANSLATE_CANARY_PCT", 0)
LLM_PROVIDER_RUBRIC = env_str("LLM_PROVIDER_RUBRIC", "deepseek").strip().lower()
LLM_PROVIDER_RUBRIC_CANARY_PCT = env_int("LLM_PROVIDER_RUBRIC_CANARY_PCT", 0)
# Output cap per call. A full JSON article (title+summary+long body+tags) can
# exceed 4096 and get truncated mid-string → JSON parse fails → template junk.
# 12000 comfortably fits a ~4000-word long-form article once JSON-escaped; the
# reservation against TPM is trivial on the 375k-TPM pinned model. Shared
# across providers (llm_openai_compatible.py's base class, and used directly
# by llm_anthropic_provider.py too) -- not Mistral-specific, hence the LLM_
# name despite the env var staying MISTRAL_MAX_TOKENS (renaming that would
# silently break prod .env files).
LLM_MAX_TOKENS = env_int("MISTRAL_MAX_TOKENS", 12000)
# Writer agentic-loop context management — the FALLBACK only. Each provider
# now asks its own GET /v1/models for each model's real max_context_length at
# construction (llm_openai_compatible._fetch_model_metadata, cached per model
# per process) and uses that instead whenever the live lookup succeeds. This
# default is what a client falls back to if that lookup fails (network blip,
# endpoint down) — it stopped mattering for correctness on 2026-07-15, when a
# hardcoded comment here ("mistral-small ~128k") turned out to be stale:
# Mistral had silently upgraded the "-latest" aliases to 262144 without
# changing the model name. A single research/writer constant is enough now
# since the real number comes from the live model, not this file.
LLM_CONTEXT_TOKENS = env_int("MISTRAL_CONTEXT_TOKENS", 256000)
# Per-tool-result character cap (structure-preserving — see token_budget). Large
# enough to carry a full article body; ~24k chars ≈ a long page.
LLM_TOOL_RESULT_MAX_CHARS = env_int("MISTRAL_TOOL_RESULT_MAX_CHARS", 24000)
# Headroom kept below the context limit (response max_tokens is reserved on top)
# so token-estimate error never tips a request over the edge.
LLM_CONTEXT_SAFETY_TOKENS = env_int("MISTRAL_CONTEXT_SAFETY_TOKENS", 4000)
# DeepSeek's own docs: a request that hasn't started inference within 600s
# (10 minutes) gets closed server-side -- the OLD 480s default sat 2 minutes
# SHORT of that window, so a request DeepSeek would still have honored could
# get abandoned client-side first, triggering a retry that piles a duplicate
# request onto an already-loaded server instead of easing it. 660s clears
# DeepSeek's own window with a minute of margin (confirmed live 2026-08-17:
# special editions already had this right via the multiplier below, just
# never applied to the standard-tier default).
LLM_TIMEOUT_SECONDS = env_int("MISTRAL_TIMEOUT_SECONDS", 660)
# Special editions' research client resends the full accumulated tool-call
# trace every chat_with_tools round; root-caused 2026-08-04 (Humanitarian
# Network recompose) at round 16 / 49 tool calls, a single round's request
# exceeded the plain 120s timeout on 5 straight attempts and the whole
# 21-minute compose was lost. Same *4-style scaling convention as the other
# special-edition knobs (RESEARCH_MIN_TOOL_CALLS, RESEARCH_FLOOR_MAX_PASSES,
# LLM_MAX_TOOL_ROUNDS) but deliberately smaller (2x, not 4x): the
# compose_lock is a GLOBAL mutex, so a special edition stuck retrying a truly
# dead API would block every other compose on the platform for the full
# worst-case retry window -- 2x balances "tolerate a slow big-context round"
# against "don't let one stuck special edition wedge the whole pipeline."
LLM_TIMEOUT_SPECIAL_EDITION_MULTIPLIER = env_int(
    "MISTRAL_TIMEOUT_SPECIAL_EDITION_MULTIPLIER", 2
)
# REQUIRED for article generation — there is no template fallback (owner
# decision 2026-07-14: a lesser, robotic article is worse than no article).
# Nothing gets composed until this is True with a valid MISTRAL_API_KEY.
MISTRAL_ENABLED = env_bool("MISTRAL_ENABLED", False)
# ~48k chars ≈ 12k TOKENS of source context for the composer — sized to carry
# the full service-watch aggregate (SERVICE_CONTEXT_MAX_CHARS), not one page.
# The original intent was 12k tokens; an earlier reading as 12k CHARS silently
# quartered the composer's context.
LLM_MAX_SOURCE_CHARS = env_int("MISTRAL_MAX_SOURCE_CHARS", 48_000)
# Stage-1 RESEARCH sees a smaller source clip. The research pass only decides
# what to look up — it doesn't write from the source — yet its user prompt is
# re-sent on EVERY tool round (up to LLM_MAX_TOOL_ROUNDS, plus the research
# floor's extra pass), so full-size source there multiplies input tokens ~14x
# per article for no research benefit. Stage-2 generation (a single call)
# always gets the full LLM_MAX_SOURCE_CHARS clip.
LLM_RESEARCH_SOURCE_CHARS = env_int("MISTRAL_RESEARCH_SOURCE_CHARS", 16_000)
# Stage-2's single write call has NO context-budget trimming the way the
# multi-round research loop does (fit_messages_to_budget only runs inside
# chat_with_tools) -- and unlike the raw-source clip above, the digest +
# special-edition enumeration/outline handed to Stage 2 were never capped at
# all, since they're already-synthesized (expected-compact) model output, not
# raw scrape text. Root-caused live 2026-08-07: a special-edition recompose's
# accumulated digest+enumeration+outline grew large enough that the write
# call came back with an EMPTY completion (not a clean context-length error)
# on both the original attempt and chat_json_object's own built-in corrective
# retry -- the whole 31-minute compose lost with nothing to show for it. This
# caps the combined digest+enumeration+outline block specifically (the parts
# with no upstream size limit), leaving plenty of room under
# LLM_CONTEXT_TOKENS for a genuinely deep special edition while making
# runaway growth a clean truncation instead of a silent empty response.
LLM_STAGE2_EXTRAS_MAX_CHARS = env_int("MISTRAL_STAGE2_EXTRAS_MAX_CHARS", 160_000)
# Periodic re-scrape of ALL monitored sources to detect content diffs and compose
# updates (MISTRAL_DIFF_POLL_SECONDS, default 600s) is read directly via
# os.getenv in celery_app.py's beat schedule — not duplicated here, since a
# module-level constant here was never actually consumed by anything.
# Agentic writer tool-round cap. Originally 10 to stop a confused model looping
# (it was re-calling the same price/market tools 15-20x per article) — the loop's
# (tool+args) dedup now guards against that specific failure mode independently,
# and a prod session audit (2026-07-05) found 65% of composes still hit the old
# cap of 10 mid-research (many distinct tool calls, not repeats) and got forced
# into "write now" with an incomplete picture. Raised to 14, then to 24
# (2026-07-15, owner request): a research-hostile story (4 dead/quiet NFT
# marketplaces, many 0-result searches) hit the DIGEST_GAP_FILL ceiling and
# never got a real chance to fill its gaps. Raised 24 -> 48 (2026-08-13, owner
# request): the LumiRogue recompose that finally used play_interactive to
# actually enter the demo AND verified the Testnet-footer/mainnet-code
# discrepancy on-chain ran the full 24 rounds and still had more threads
# worth chasing (leaderboard provenance, NFD segments, guest-collection
# history) -- research-hungry stories with a rich on-chain surface were
# genuinely round-constrained, not just slow. Each round costs one more
# throttled LLM call (LLM_MIN_REQUEST_INTERVAL_SECONDS apart); a compose
# using the full budget now runs ~35-40min of research alone, which is WHY
# the compose-task time limits below were widened alongside this change --
# raising this constant without them risks the exact silent mid-compose
# kill the 2026-08-04 Humanitarian Network incident hit at the OLD ceiling.
# The (tool+args) dedup guard still bounds true runaway loops.
LLM_MAX_TOOL_ROUNDS = env_int("LLM_MAX_TOOL_ROUNDS", 48)
# Every celery task that composes an article (recompose_published,
# recompose_review, recompose_session_service,
# publish_from_chain_event, drain_to_compose (formerly
# drain_standard_publish_queue), compose_artifact_now) previously relied on
# the app-wide
# task_soft_time_limit/task_time_limit (celery_app.py, 1800s/1860s = 30/31min)
# -- fine when LLM_MAX_TOOL_ROUNDS was 24 and a deep compose ran ~22min,
# but the 24->48 raise above pushes a full-budget research pass alone to
# ~35-40min, which would get SIGTERM'd mid-compose exactly like the
# 2026-08-04 Humanitarian Network incident (that one lost a 21-minute compose
# to the OLD, tighter per-round Mistral HTTP timeout; this is the same
# failure shape one level up, at the whole-task level). Per-task override
# (not raising the app-wide default) so every OTHER celery task's timeout
# stays untouched -- same reasoning translate_article_batch_task already
# documents for its own override.
COMPOSE_TASK_SOFT_TIME_LIMIT = env_int("COMPOSE_TASK_SOFT_TIME_LIMIT", 5400)  # 90m
COMPOSE_TASK_TIME_LIMIT = env_int("COMPOSE_TASK_TIME_LIMIT", 5700)  # 95m
# Voxtral audio transcription (local YouTube pipeline) — same Mistral account,
# different endpoint (/audio/transcriptions, multipart) from the chat models above.
MISTRAL_VOXTRAL_MODEL = env_str("MISTRAL_VOXTRAL_MODEL", "voxtral-mini-latest")
MISTRAL_VOXTRAL_TIMEOUT = env_int("MISTRAL_VOXTRAL_TIMEOUT", 120)
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
LLM_TEMP_RESEARCH = env_float("MISTRAL_TEMP_RESEARCH", 0.15)
# Generation-phase temperature: higher to vary prose structure.
LLM_TEMP_WRITE = env_float("MISTRAL_TEMP_WRITE", 0.6)
# Two-stage compose: after generation, the heuristic grader runs deterministically
# (the warm pass has no tools, so the model can't call review_draft itself). A draft
# graded below this triggers a revision pass with the issues fed back, up to
# WRITER_REVISION_MAX_PASSES times — a pass that comes back clean stops early
# (2026-07-13: raised 1 -> 2 after a real critical_distance regression on the one
# allowed pass went unfixed; each extra pass costs one more Mistral revision call
# + one more grading call, only spent when a draft is still flagged).
WRITER_REVIEW_ENABLED = env_bool("WRITER_REVIEW_ENABLED", True)
WRITER_REVIEW_MIN_GRADE = env_float("WRITER_REVIEW_MIN_GRADE", 7.0)
WRITER_REVISION_MAX_PASSES = env_int("WRITER_REVISION_MAX_PASSES", 2)
# 2026-08-11 (owner request): the revision call used to be a plain no-tools
# chat_json_object -- it could only reorganize/reword facts already sitting
# in the research digest, even when a rubric/gate issue is exactly the kind
# a fresh tool call could resolve (an unverified claim, a stale figure, a
# dead link with a real replacement one search away). Giving revision its
# own bounded tool-call budget instead of reusing LLM_MAX_TOOL_ROUNDS:
# revision is meant to be surgical (fix the handful of flagged issues), not
# a second full research pass, and each of the WRITER_REVISION_MAX_PASSES
# outer passes gets this budget independently.
WRITER_REVISION_TOOL_MAX_ROUNDS = env_int("WRITER_REVISION_TOOL_MAX_ROUNDS", 8)
# Stage 3 qualitative rubric (Small tier): narrative synthesis + technical depth,
# scored 1-5. quality_needs_revision() triggers on strictly-below, so this is the
# lowest score considered PASSING — 4, not 3: a 3 still carries real, written
# issues ("reads like a pitch", "not synthesized") that the old default of 3
# silently let through (3 < 3 is False), so the rubric's own feedback was
# generated but never fed back into a revision.
WRITER_QUALITY_LLM_ENABLED = env_bool("WRITER_QUALITY_LLM_ENABLED", True)
WRITER_QUALITY_LLM_MIN_SCORE = env_int("WRITER_QUALITY_LLM_MIN_SCORE", 4)
# Length is LAX: any article in [LENGTH_OK_MIN, LENGTH_OK_MAX] words is fine —
# length is not a graded dimension and not a target. Research DEPTH drives the
# grade instead, so the model fetches context rather than padding to a word count.
LENGTH_OK_MIN_WORDS = env_int("LENGTH_OK_MIN_WORDS", 250)
LENGTH_OK_MAX_WORDS = env_int("LENGTH_OK_MAX_WORDS", 2000)
# Stage-1 research FLOOR: after the research pass, if the writer touched fewer
# than this many distinct SOURCES (domains fetched, or a stable per-tool identity
# for calls with no URL — see _distinct_research_calls; EXCLUDING review_draft
# self-checks), it is sent back once to dig deeper before it may write. Enforced
# mechanically, so research depth is no longer a graded dimension. Counting
# sources rather than raw tool-name variety means it can't be satisfied by
# several trivial calls that all skim the same one or two domains.
RESEARCH_MIN_TOOL_CALLS = env_int("RESEARCH_MIN_TOOL_CALLS", 6)
RESEARCH_FLOOR_ENABLED = env_bool("RESEARCH_FLOOR_ENABLED", True)
# Raised 1->2 alongside LLM_MAX_TOOL_ROUNDS (2026-07-15, owner request):
# more headroom for research-hostile stories, still bounded.
RESEARCH_FLOOR_MAX_PASSES = env_int("RESEARCH_FLOOR_MAX_PASSES", 2)
# Digest synthesis (Stage 1b) can flag specific unresolved-but-material gaps
# (e.g. "no real sales data found for this marketplace"). Rather than handing
# those straight to the tool-less writer — which either omits them (fine) or
# invents/recalls something to fill them (the nf.domains fabricated-sales
# incident) — send the model back for ONE bounded extra research pass
# targeting exactly those gaps, then re-synthesize the digest. Off switch for
# cost control; the round budget below keeps a single pass cheap even when on.
DIGEST_GAP_FILL_ENABLED = env_bool("DIGEST_GAP_FILL_ENABLED", True)
# Raised 4->8 (2026-07-15, owner request): a research-hostile story (4
# dead/quiet NFT marketplaces) hit this exact ceiling mid-gap-fill and never
# got a real shot at resolving its flagged gaps.
DIGEST_GAP_FILL_MAX_ROUNDS = env_int("DIGEST_GAP_FILL_MAX_ROUNDS", 8)
# Special-edition-only deepening pass (2026-08-04, owner request): a
# structured Entity Enumeration (people/places/dates/services/numbers, each
# with an explicit "what's missing" call-out) surfaces coverage gaps a
# prose digest's generic 3-gap cap can miss, a SECOND targeted gap-fill round
# closes them, and a Narrative Outline gives Stage 2 a concrete structure to
# write from instead of synthesizing organization from a raw digest cold.
# Three extra Mistral calls per special edition -- acceptable given the
# already-higher special-edition budget (4x research rounds/floor already).
SPECIAL_EDITION_OUTLINE_ENABLED = env_bool("SPECIAL_EDITION_OUTLINE_ENABLED", True)
SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS = env_int(
    "SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS", 8
)
# A compose_session stuck in a non-terminal status (researching/writing) this
# long is dead, not slow — the compose task's own hard time limit
# (CELERY_TASK_TIME_LIMIT, 1860s/31min) means a crash that skips the
# try/except checkpoint finalizers (e.g. a SIGKILL/OOM, or an exception before
# the first checkpoint) is the only way a row gets stuck; reap_stale_compose_sessions
# marks it "stale" so the admin Sessions view stops showing it as in-progress.
COMPOSE_SESSION_STALE_MINUTES = env_int("COMPOSE_SESSION_STALE_MINUTES", 60)
# A translation_sessions row stuck 'running' past this long is either a
# genuinely hung model call or a worker that died mid-language without
# reaching translate_article_batch's on_language_done/on_language_error
# callback -- the whole BATCH task's own hard limit (16h30m, see
# translate_article_batch_task) would eventually catch a fully-wedged
# worker, but that's far too coarse to flag ONE stuck language promptly.
# The worst observed single-language time is 1h41m (SeamlessM4T beam search
# on 'ps', a content-heavy special edition, 2026-08-08); this default gives
# ~1.75x margin above that.
TRANSLATION_SESSION_STALE_MINUTES = env_int("TRANSLATION_SESSION_STALE_MINUTES", 180)
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

# Social auto-post on publish (owner decision 2026-07-12): each channel is
# independently enabled by having credentials set — no separate ENABLED flag,
# an empty handle/token IS "disabled" (see SocialDistributor.enabled). App
# password, not the main account password (Settings > App Passwords in the
# Bluesky app) — can be revoked independently, can't change account settings.
# Reuses the SAME account (and so the same two env vars) as search_bluesky's
# research tool in research_tools.py — owner deliberately repurposed the
# existing agent-research account (2026-07-12), handle changed to the
# algorand.pxke.me custom domain. research_tools.py reads BLUESKY_IDENTIFIER
# via os.getenv() directly rather than this constant — both read the same
# underlying env var, so there's still only one value to keep in sync, just
# two code paths reading it.
BLUESKY_IDENTIFIER = env_str("BLUESKY_IDENTIFIER", "")
BLUESKY_APP_PASSWORD = env_str("BLUESKY_APP_PASSWORD", "")
# Bot token from @BotFather; chat_id is the target channel (e.g. "@channelname"
# for a public channel, or the numeric id for a private one — the bot must be
# added as an admin of the channel either way).
TELEGRAM_BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = env_str("TELEGRAM_CHAT_ID", "")
# Mastodon distribution removed 2026-08-25: the owner's account was banned
# by the instance for publishing AI-generated content (platform-policy
# violation, not a code-quality call). MASTODON_INSTANCE_URL /
# MASTODON_ACCESS_TOKEN env vars are no longer read anywhere.
# Client-side rate limiting (Redis-coordinated across all workers), shared by
# BOTH LLM providers (Mistral and DeepSeek — see llm_rate_limit.py). Spacing
# is a hard floor on time between calls (leaky bucket). Lowered 15.0 -> 5.0
# (2026-08-13): the 15s floor was tuned for mistral-medium-2505 alone back
# when it was the only provider; DeepSeek now serves most compose traffic
# and had never been observed to actually need that much headroom (zero 429s
# from either provider in 7 days of prod logs at the old value).
LLM_MIN_REQUEST_INTERVAL_SECONDS = env_float("LLM_MIN_REQUEST_INTERVAL_SECONDS", 5.0)
LLM_MAX_RETRIES = env_int("MISTRAL_MAX_RETRIES", 4)
# Retry backoff on 429 / 5xx / network errors: base * 2**attempt, capped. Honors
# Retry-After when present. Larger so a rate-limited call waits meaningfully.
LLM_BACKOFF_BASE_SECONDS = env_float("MISTRAL_BACKOFF_BASE_SECONDS", 15.0)
LLM_BACKOFF_MAX_SECONDS = env_float("MISTRAL_BACKOFF_MAX_SECONDS", 300.0)

# Rotating raw-HTTP diagnostic log for every LLM provider call (request summary
# + full raw response body), added 2026-08-21 chasing a reproducible
# "provider returned non-JSON content" compose failure (hay.app) that left
# zero trace anywhere -- neither compose_sessions (the failure path never
# calls record_compose_session) nor application logs (no journal read access
# on prod). The response body is what actually matters here: an empty/
# malformed .content can still carry a finish_reason, moderation flag, or
# error object elsewhere in the same JSON that today's code silently
# discards. Off by default (empty path) so local/dev runs never try to write
# to a prod-only directory; prod sets LLM_HTTP_LOG_PATH via workers.env.
LLM_HTTP_LOG_PATH = env_str("LLM_HTTP_LOG_PATH", "")
LLM_HTTP_LOG_MAX_BYTES = env_int("LLM_HTTP_LOG_MAX_BYTES", 50 * 1024 * 1024)
LLM_HTTP_LOG_BACKUP_COUNT = env_int("LLM_HTTP_LOG_BACKUP_COUNT", 10)


def mistral_configured() -> bool:
    """True when Mistral is enabled and an API key is set."""
    return MISTRAL_ENABLED and bool(MISTRAL_API_KEY.strip())


URL_QUEUE_ENABLED = env_bool("URL_QUEUE_ENABLED", True)
URL_QUEUE_DRAIN_SECONDS = env_int("URL_QUEUE_DRAIN_SECONDS", 60)
# How many URLs the drain crawls per tick. Paced by the beat interval — 10 per
# tick with URL_QUEUE_DRAIN_SECONDS=10 clears a large backlog faster than the
# old 1/tick default while still spacing requests out over the beat window.
URL_QUEUE_DRAIN_BATCH = env_int("URL_QUEUE_DRAIN_BATCH", 10)
# How many top pending rows dequeue_url() picks randomly among, instead of
# always the single front-of-queue row — see dequeue_url() for why. 1 restores
# strict priority/enqueued_at order.
URL_QUEUE_RANDOM_PICK_POOL = env_int("URL_QUEUE_RANDOM_PICK_POOL", 100)
# When the writer's fetch_url tool successfully reads a page during compose,
# enqueue it for a full harvest (crawled_pages + Typesense) — high-signal URLs
# the model explicitly chose to investigate.
WRITER_FETCH_ENQUEUE_ENABLED = env_bool("WRITER_FETCH_ENQUEUE_ENABLED", True)
WRITER_FETCH_ENQUEUE_PRIORITY = env_int("WRITER_FETCH_ENQUEUE_PRIORITY", 45)
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
COMPOSE_DOMAIN_COOLDOWN_HOURS = env_int("COMPOSE_DOMAIN_COOLDOWN_HOURS", 720)
# Same spacing, keyed on the canonical service_id instead of the registrable
# domain — a project spread across domains that don't share an eTLD+1 (e.g. a
# Medium blog + its own site) would otherwise dodge the cooldown above by
# publishing from its OTHER domain. 0 disables.
COMPOSE_SERVICE_COOLDOWN_HOURS = env_int("COMPOSE_SERVICE_COOLDOWN_HOURS", 720)

# After an admin rejects a review, suppress re-enqueueing that exact URL for
# this long (seconds). Stops a rejected page re-entering the candidate queue the
# moment its content hash shifts again. Default 7 days.
URL_REJECT_COOLDOWN_TTL = env_int("URL_REJECT_COOLDOWN_TTL", 604800)
# Delink body urls that neither appeared in the research trace nor resolve
# live (2026-07-16: the writer decorated real project names with invented
# urls — downbad.art, alchemon.com — that the numeric gatekeeper can't see).
LINK_GATE_ENABLED = env_bool("LINK_GATE_ENABLED", True)
# Hold-for-review veto: an article that LINKS any domain which no longer
# resolves to a usable address is diverted to human review instead of
# auto-publishing (2026-07-19: recommended MyAlgo Wallet, defunct since 2023).
# Actively DNS-checks every linked domain at gate time — catching both an entity
# the research fetched-and-failed AND one the writer recommended blind from stale
# training memory (no research signal at all). Unlike the link gate (which only
# delinks untraced dead urls), this holds the whole draft, since a defunct entity
# recommended in prose survives delinking.
DEFUNCT_ENTITY_GATE_ENABLED = env_bool("DEFUNCT_ENTITY_GATE_ENABLED", True)
# De-quote body quotations (4+ words) that aren't verbatim in the research
# trace / compose input (2026-07-16: a draft attributed an invented phrase in
# quotation marks to the Goanna Council). Words survive as paraphrase.
QUOTE_GATE_ENABLED = env_bool("QUOTE_GATE_ENABLED", True)
# Cited ASA ids / addresses / txids must exist on-chain (mainnet or testnet);
# verified ones get auto-linked to an explorer, provably-missing ones are fed
# back to the writer and delinked (2026-07-17: AlgoGlyph published "50.16% of
# supply" for a holding the chain says is 25.08% — a clickable explorer link
# and a deterministic resolve of the same id makes that class visible).
CHAIN_ENTITY_GATE_ENABLED = env_bool("CHAIN_ENTITY_GATE_ENABLED", True)
# Unattributed appeals to authority ("industry-wide research suggests",
# "experts say", "studies show") are unattributable by construction — a real
# claim has a citable source in the writer's own research trace. Findings go
# to the revision loop first; survivors are excised sentence-wise (2026-07-18:
# a pre-release draft asserted a fabricated "10-100x slower to verify" Falcon
# benchmark laundered through "industry-wide research").
AUTHORITY_GATE_ENABLED = env_bool("AUTHORITY_GATE_ENABLED", True)
# Unsourced-specifics gate: hard traction/funding claims (issuer/user/event
# counts, TVL, $ amounts, named partners/backers) must trace to a fetched tool
# result. Two incidents motivate it: MyAlgo (defunct entity recommended) and
# GoPlausible 2026-07-20 (fetched stat-counters read ZERO / partners empty, the
# draft overwrote them with "1,000 issuers / 70+ events / Borderless Capital").
# ENABLED runs the scan and RECORDS findings (payload['_unsourced_specifics']);
# ENFORCE additionally diverts a flagged draft to human review (sets
# _unsourced_hold_reason → gate_enforced_review + fails fresh auto-approve
# closed, mirroring the defunct-entity gate). Enforcement enabled 2026-07-20
# after a read-only tuning pass over 37 real sessions showed clean precision
# (only fabricated counts/partners + one unsourced funding round flagged, no
# clear false positives). A hold (not auto-rewrite) is deliberate: some flagged
# specifics are real-but-unsourced (e.g. a genuine funding round the writer
# didn't fetch), which a human keeps and a machine rewrite would wrongly strip.
UNSOURCED_SPECIFICS_GATE_ENABLED = env_bool("UNSOURCED_SPECIFICS_GATE_ENABLED", True)
UNSOURCED_SPECIFICS_GATE_ENFORCE = env_bool("UNSOURCED_SPECIFICS_GATE_ENFORCE", True)
# Root-caused 2026-08-04 (Meld Gold): a real, accurately-sourced deadline
# ("Holders have until June 29, 2026") published over 5 weeks AFTER that date
# passed, presented as still open. See stale_deadline_gate.py — a revision
# issue, not a hold, since the fix is a tense rewrite the writer can do itself.
STALE_DEADLINE_GATE_ENABLED = env_bool("STALE_DEADLINE_GATE_ENABLED", True)
# Broken-link-claim gate: a body sentence asserting a link/page is broken,
# 404s, or doesn't work must be backed by an actual click_element/
# play_interactive click attempt somewhere in the trace, not just a guessed
# fetch_url. Root-caused 2026-08-10 (lumirogue.com): a draft called the site's
# "About this project"/"Terms of use" footer links broken because /about and
# /terms genuinely 404 — but those are JS buttons with no real href, opening
# working in-page modals when clicked. The prompt-only fix (telling the writer
# to try click_element first) did NOT hold: the identical mistake recurred
# 2026-08-12 on the same links. ENABLED runs the scan and records findings
# (payload['_broken_link_claims']); ENFORCE additionally diverts a flagged
# draft to human review. Ships ENFORCE=False (unlike the two gates above,
# which graduated to enforcing after a tuning pass) since this pattern-match
# is coarser than unsourced_specifics' proximity-window numeric check — it
# only asks "was ANY click attempted this compose", not "was THIS SPECIFIC
# link clicked" — so it needs a real precision read on live traffic first.
BROKEN_LINK_CLAIM_GATE_ENABLED = env_bool("BROKEN_LINK_CLAIM_GATE_ENABLED", True)
BROKEN_LINK_CLAIM_GATE_ENFORCE = env_bool("BROKEN_LINK_CLAIM_GATE_ENFORCE", False)
# Root-caused 2026-08-04 (vestige.fi): build_text_diff's 200-line cap silently
# dropped 1,573 of 1,773 real diff lines (89%) before the writer ever saw
# them -- two new asset-manager pages, mostly repetitive label/value
# boilerplate, but the writer's explicit "this is the story" assignment was
# built from under 11% of the real change. Raised 4x; the honest
# "(N more lines omitted)" marker (2026-07-13/14 fix) stays either way.
# DIFF_PROMPT_MAX_CHARS must stay generous enough that the prompt-side clip
# doesn't silently re-truncate a diff this much bigger and swallow that same
# marker -- the original stale-hunk-header bug in a new shape.
DIFF_MAX_LINES = env_int("DIFF_MAX_LINES", 800)
DIFF_PROMPT_MAX_CHARS = env_int("DIFF_PROMPT_MAX_CHARS", 16_000)
# suggest_glossary_term is only callable during Stage 1 (research), before the
# article's own prose exists, so it was never actually usable for its stated
# purpose -- confirmed 2026-08-03: 0 of 62 real sessions ever called it, and
# glossary_terms had 0 rows total. This runs a small classification call over
# the FINISHED body instead and queues draft rows the same way the tool did
# (admin still reviews before publish) -- pure side effect, never mutates the
# article, so it's safe on by default.
GLOSSARY_SUGGEST_GATE_ENABLED = env_bool("GLOSSARY_SUGGEST_GATE_ENABLED", True)
# Stop composing review-bound candidates once pending_feed_queue already holds
# this many approved articles awaiting paced release (2026-07-16: auto-approve
# → backlog bypassed the 1-slot review throttle, so hourly drains composed 6
# articles overnight — two full days of publish inventory at 3/day, ~1.5M
# tokens — with nothing to stop the loop).
PENDING_FEED_MAX_DEPTH = env_int("PENDING_FEED_MAX_DEPTH", 3)
# Near-duplicate guard, applied AT COMPOSITION (not enqueue — the set of
# published articles can grow between a candidate being queued and composed).
# Skip composing when a recently published headline is at least this Jaccard-
# similar (title+tags tokens). 1.0 disables; ~0.8 = "almost the same headline".
NOVELTY_GATE_ENABLED = env_bool("NOVELTY_GATE_ENABLED", True)
# 0.6 = skip when 60%+ of significant headline tokens overlap a recent article.
# Title-token Jaccard catches near-identical headlines, not loose paraphrases
# (that needs embeddings); the post-compose grader novelty flags the softer cases.
NOVELTY_MAX_SIMILARITY = env_float("NOVELTY_MAX_SIMILARITY", 0.6)
# Stricter bar when the closest recent article covers the SAME service:
# re-covering one's own subject with a slightly reworded headline is the
# common near-duplicate shape (Alpha Arcade pair, 0.455 Jaccard, 2026-07-16).
NOVELTY_SAME_SERVICE_MAX_SIMILARITY = env_float("NOVELTY_SAME_SERVICE_MAX_SIMILARITY", 0.4)
# Content-level novelty: retrieve recently-published articles textually closest to
# a candidate from the Typesense articles index (title+summary+body), then score
# overlap against the candidate's title+summary+body via bag-of-words token
# Jaccard (see article_grader.recent_content_similarity). Catches same-topic /
# different-headline dupes that the title-only Jaccard misses -- including
# CROSS-service duplicates, since the Typesense retrieval isn't scoped to one
# service. Only articles published within this window are considered (0
# disables the content check). Default covers the full decay horizon below so
# age-weighting can taper old matches rather than the window hard-cutting them
# off.
#
# Shingle-set Jaccard was tried here 2026-08-24 and reverted the same day: it
# gives a much cleaner noise floor (0.00-0.04 among 9 random distinct live
# articles vs 1.0 for a confirmed exact duplicate, HesabPay) but scores near 0
# on genuinely paraphrased duplicates -- a reworded sentence rarely shares a
# 5-word run, and catching exactly that case is the reason this function
# exists (recent_content_similarity's own regression test: two differently-
# worded Pera Wallet staking headlines about the same story, token Jaccard
# 0.31, shingle Jaccard 0.0). Token Jaccard's scale matches NOVELTY_MAX_
# SIMILARITY (title-token Jaccard) above, so the same threshold is reused
# directly for the content check rather than a separate constant.
NOVELTY_CONTENT_WINDOW_HOURS = env_int("NOVELTY_CONTENT_WINDOW_HOURS", 24 * 70)
# Age-decay of the similarity penalty: a near-duplicate published within
# NOVELTY_DECAY_FULL_DAYS counts at full weight (novelty can hit 0); the weight
# then eases linearly to 0 by NOVELTY_DECAY_ZERO_DAYS, so re-covering a story is
# penalized hard for a week and freely allowed again after ~10 weeks.
NOVELTY_DECAY_FULL_DAYS = env_int("NOVELTY_DECAY_FULL_DAYS", 7)
NOVELTY_DECAY_ZERO_DAYS = env_int("NOVELTY_DECAY_ZERO_DAYS", 70)
# Post-compose, same-service duplicate check: catches a page that was
# genuinely reworded (so the title/content novelty checks above, which
# compare the SOURCE page against recent articles, see enough lexical
# difference to pass) but reports the same underlying facts as the service's
# own last article (Steak Pool, 2026-08-02: "1.9M STEAK via validator
# commission" vs "1.9M tokens via validator economics" 21 days apart --
# same 1.9M/11.16%/1.69%/#13 figures, 69.8% of the new draft's numbers
# already grounded in the prior article). Independent of Typesense (a direct
# match-key lookup + Cassandra fetch), so it isn't exposed to index-lag gaps.
# total<MIN_CLAIMS is never blocked -- too few numbers to be meaningful
# evidence either way.
ARTICLE_DUPLICATE_NUMERIC_OVERLAP = env_float("ARTICLE_DUPLICATE_NUMERIC_OVERLAP", 0.6)
ARTICLE_DUPLICATE_MIN_CLAIMS = env_int("ARTICLE_DUPLICATE_MIN_CLAIMS", 3)
# Second, independent trigger for the same guard: the NFDomains incident
# (2026-08-02) showed a service can get a full re-explainer article every
# ~3-5 weeks that shares almost no NUMBERS with its own prior coverage (a
# growing mint-count, a fresh headline stat each time) while reusing the same
# pitch/structure/vocabulary almost verbatim -- title-only and numeric-only
# checks both missed it (title Jaccard 0.06; too few shared numeric claims).
# Token-Jaccard (calibrated against that real pair: whole-title+summary+body
# token Jaccard scored ~0.24 for the true-positive NFDomains pair). Shingle-
# set Jaccard was tried in its place 2026-08-24 and reverted the same day --
# it gives cleaner separation from unrelated-article noise (~0.04 ceiling vs
# 1.0 for a confirmed exact duplicate) but scores near 0 on this exact
# NFDomains pair, since a genuine reword rarely shares a 5-word run and this
# check exists specifically to catch that case (see the docstring above).
# Token Jaccard's tradeoff is a noisier floor: 9 random distinct live article
# pairs scored 0.15-0.25, uncomfortably close to the 0.24 true positive --
# a real but modest margin, since shared Algorand/crypto vocabulary dilutes
# token overlap even for unrelated stories. 0.20 sits with some margin above
# the bulk of that noise sample while still catching the true positive.
# Revisit once more real reworded-not-identical pairs exist to calibrate
# against.
ARTICLE_DUPLICATE_BODY_SIMILARITY = env_float("ARTICLE_DUPLICATE_BODY_SIMILARITY", 0.20)
# Below this many unique tokens per side, Jaccard on short text is noisy
# (small vocabularies swing to extremes) -- same "not enough evidence either
# way" floor as ARTICLE_DUPLICATE_MIN_CLAIMS, just for the body-similarity
# side of the check instead of the numeric side.
ARTICLE_DUPLICATE_BODY_MIN_TOKENS = env_int("ARTICLE_DUPLICATE_BODY_MIN_TOKENS", 40)
# Selection ranking: relevance, novelty, and source timeliness. Relevance is the
# spine — it MULTIPLIES, so an off-topic page scores ~0 no matter how "novel" or
# "fresh" it looks. Timeliness uses page metadata and title/lead dates so a
# months-old announcement sinks below comparable fresh coverage.
RELEVANCE_PRIORITY_WEIGHT = env_int("RELEVANCE_PRIORITY_WEIGHT", 100)
NOVELTY_PRIORITY_WEIGHT = env_int("NOVELTY_PRIORITY_WEIGHT", 100)
RECENCY_PRIORITY_WEIGHT = env_int("RECENCY_PRIORITY_WEIGHT", 80)
# Diff-driven selection: for content updates, the size of the change is the
# newsworthiness signal (the diff IS the event) — scaled by relevance and
# normalised at DIFF_SIGNIFICANCE_NORM_LINES added lines for full credit.
# Discoveries fire once per service ever, so precise ordering among them
# matters little: they get a flat relevance-scaled DISCOVERY_PRIORITY_WEIGHT.
# Repetition suppression: novelty MULTIPLIES the whole priority (like relevance
# does inside the components) — a zero-novelty candidate keeps only the floor
# share of its score, so an already-covered story can't outrank fresh ones on
# relevance+diff alone. Kept soft (not a gate): weekly service updates always
# resemble the service's previous article somewhat.
NOVELTY_SUPPRESSION_FLOOR = env_float("NOVELTY_SUPPRESSION_FLOOR", 0.3)
DIFF_PRIORITY_WEIGHT = env_int("DIFF_PRIORITY_WEIGHT", 80)
DIFF_SIGNIFICANCE_NORM_LINES = env_int("DIFF_SIGNIFICANCE_NORM_LINES", 40)
DISCOVERY_PRIORITY_WEIGHT = env_int("DISCOVERY_PRIORITY_WEIGHT", 60)
# Announcement-shaped candidates (event detected, urgency phrasing, or a
# launch/partnership/release title) earn a relevance-gated bonus — evergreen
# SEO pages can't, and detected spam also forfeits its timeliness points.
ANNOUNCE_PRIORITY_BONUS = env_int("ANNOUNCE_PRIORITY_BONUS", 40)
# Learned signal: a confident publish classifier verdict nudges priority
# (bonus scaled by confidence when True, a halving-style malus when False).
# Inert in training mode, where predict_publish defers everything to review.
CLASSIFIER_PRIORITY_WEIGHT = env_int("CLASSIFIER_PRIORITY_WEIGHT", 30)
# Real-world project scale (DeFiLlama TVL, or GitHub org stars as a fallback) —
# a secondary/modifier signal sized like ANNOUNCE_PRIORITY_BONUS, meant to
# decide close calls between otherwise-equal candidates, not approach
# relevance/novelty's weight (those stay the spine: "is this on-topic",
# "is this fresh"). Feeds Lane 2 ("biggest/most significant") of the
# publish-queue lane split — see service_scale.py.
SCALE_PRIORITY_WEIGHT = env_int("SCALE_PRIORITY_WEIGHT", 40)
# How often a service's scale signal is re-resolved (DeFiLlama/GitHub calls
# are cheap but not free) — piggybacked on the existing per-service ingest
# write path, not a separate scheduled job.
SERVICE_SCALE_REFRESH_DAYS = env_int("SERVICE_SCALE_REFRESH_DAYS", 21)

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
# Local pipeline: yt-dlp (proxied — the prod IP is bot-blocked for direct audio
# download, confirmed live) -> ffmpeg audio extract -> Voxtral transcription.
# Tried first when enabled; falls back to the third-party API above on failure.
# One opaque proxy URL, no vendor-specific config — yt-dlp's `proxy` option takes
# the standard scheme://user:pass@host:port form directly for any vendor.
YOUTUBE_LOCAL_TRANSCRIBE_ENABLED = env_bool("YOUTUBE_LOCAL_TRANSCRIBE_ENABLED", False)
YOUTUBE_DOWNLOAD_PROXY_URL = env_str("YOUTUBE_DOWNLOAD_PROXY_URL", "")
YOUTUBE_DOWNLOAD_TIMEOUT = env_int("YOUTUBE_DOWNLOAD_TIMEOUT", 180)
YOUTUBE_TRANSCRIPT_MAX_CHARS = env_int("YOUTUBE_TRANSCRIPT_MAX_CHARS", 20_000)
DISCOVERY_MODE_ENABLED = env_bool("DISCOVERY_MODE_ENABLED", True)
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
# sigma=350 the >0.6 band is roughly 500-1100 words).
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
# classify_pending_domains samples up to this many same-domain pages (landing
# page + same-domain links found on it) and takes the BEST-scoring one as the
# domain's relevance, instead of betting the whole verdict on the landing page
# alone. A chain-silent service's homepage can score 0 while its product/docs
# page scores well (the exact HesabPay/Lofty problem KNOWN_DOMAINS works
# around by hand — this generalizes it without curating every domain by name).
# 1 restores the old landing-page-only behavior.
FRONTIER_CLASSIFY_SAMPLE_PAGES = env_int("FRONTIER_CLASSIFY_SAMPLE_PAGES", 4)
# Full Site / Single Page reviewer suggestion (advisory only — see
# suggest_full_site in domain_tracker.py): a landing page with at least this
# many same-domain links suggests Full Site (a real site has many pages), else
# Single Page. Ignored on the curated chrome-heavy platform list.
FULL_SITE_LINK_THRESHOLD = env_int("FULL_SITE_LINK_THRESHOLD", 5)
# Before permanently rejecting a domain the cheap FRONTIER_CLASSIFY_SAMPLE_PAGES
# pass couldn't clear, escalate to a thorough one-time crawl instead of taking
# the shallow sample's word for it (deep_classify_domain task) — slow, but
# only once per domain, and only for the ones the cheap pass couldn't resolve.
# Stops at the first page that clears relevance; only a genuinely off-topic
# domain pays the full FRONTIER_DEEP_CLASSIFY_MAX_PAGES cost. False restores
# the old behavior of trusting the shallow sample's reject outright.
FRONTIER_DEEP_CLASSIFY_ENABLED = env_bool("FRONTIER_DEEP_CLASSIFY_ENABLED", True)
FRONTIER_DEEP_CLASSIFY_MAX_PAGES = env_int("FRONTIER_DEEP_CLASSIFY_MAX_PAGES", 200)
# CONTENT_UPDATE-specific compose-time quality floor: stricter backstop than
# FRONTIER_CONTENT_REJECT_SCORE (which stays lenient for first-crawl discovery)
# in case relevance drifts between ingest-time scoring and compose time.
CONTENT_UPDATE_QUALITY_FLOOR = env_float("CONTENT_UPDATE_QUALITY_FLOOR", 0.35)
# Score-gated frontier auto-approve: when enabled, a newly discovered unknown
# domain whose landing-page preview score (0-10 keyword+classifier scale, same as
# FRONTIER_PREVIEW_MIN_SCORE) is at least FRONTIER_AUTO_APPROVE_SCORE — or whose
# name carries an Algorand signal — is approved and crawled immediately instead of
# held for admin review. Below the threshold it is STILL held pending (never
# auto-rejected). Default off so review stays the safe baseline; raise the score
# to be stricter, lower it for more reach.
FRONTIER_AUTO_APPROVE_ENABLED = env_bool("FRONTIER_AUTO_APPROVE_ENABLED", False)
FRONTIER_AUTO_APPROVE_SCORE = env_float("FRONTIER_AUTO_APPROVE_SCORE", 3.0)
# A domain approved as "single page" (full_site=False) is composed into a
# one-shot article right after its one-time crawl, via the same shared
# ingest_publish_signal path every other lane uses (see
# scrape_from_queue_item in web_crawler.py). Kill-switch for the 2026-07-26
# rollout — off falls back to the old behavior (crawl once, no article).
SINGLE_PAGE_AUTOCOMPOSE_ENABLED = env_bool("SINGLE_PAGE_AUTOCOMPOSE_ENABLED", True)
# Cap on inline landing-page previews fetched per crawled page. Each preview is a
# blocking HTTP GET + classifier pass, so a link-heavy page could otherwise stall
# one drain task for minutes; unknown domains past the cap are still HELD pending
# (a human / the classify_pending_domains task previews them later), never dropped.
FRONTIER_PREVIEW_MAX_PER_PAGE = env_int("FRONTIER_PREVIEW_MAX_PER_PAGE", 8)
# Auto-flagged (non-admin) irrelevant domains are re-checked after this many days,
# in case they became relevant. Admin rejects are permanent regardless.
FRONTIER_RECRAWL_DAYS_IRRELEVANT = env_int("FRONTIER_RECRAWL_DAYS_IRRELEVANT", 7)
# A writer's abort_article(dead_project) call is a real signal but not a
# certain human judgment (unlike an admin's explicit reject, which is
# permanent) -- suppress the domain for this many days, then let the
# frontier check it again in case the project came back. ~3 months.
DEAD_PROJECT_COOLDOWN_DAYS = env_int("DEAD_PROJECT_COOLDOWN_DAYS", 90)

# Curated ecosystem directories (comma-separated URLs). Each listed domain is
# approved + monitored and gets a relevance anchor in score_page — the fix for
# chain-silent services (HesabPay/Lofty class) that link-following discovery
# and homepage keyword scoring structurally miss. Admin rejects always win.
ECOSYSTEM_SYNC_ENABLED = env_bool("ECOSYSTEM_SYNC_ENABLED", True)
ECOSYSTEM_DIRECTORY_URLS = [
    u.strip()
    for u in env_str(
        "ECOSYSTEM_DIRECTORY_URLS",
        "https://raw.githubusercontent.com/awesome-algorand/awesome-algorand/main/README.md",
    ).split(",")
    if u.strip()
]

# Paginated case-study indexes (comma-separated). Each detail page's external
# links yield the subject org's domain — the discovery path for the
# institutional/impact class (Mercy Corps/UNDP/SEWA style) whose sites are
# huge and chain-silent, so keyword scoring marks them irrelevant even though
# a Foundation case study is the strongest relevance signal there is.
ECOSYSTEM_CASE_STUDY_INDEXES = [
    u.strip()
    for u in env_str(
        "ECOSYSTEM_CASE_STUDY_INDEXES",
        "https://algorand.co/case-studies",
    ).split(",")
    if u.strip()
]

# Machine-readable ecosystem registries (DefiLlama protocols, Pera verified
# assets) ingested by the same daily sync. PERA_VERIFIED_ASSET_CAP bounds the
# per-run algod lookups that resolve each verified ASA's on-chain url param.
ECOSYSTEM_API_SOURCES_ENABLED = env_bool("ECOSYSTEM_API_SOURCES_ENABLED", True)
PERA_VERIFIED_ASSET_CAP = env_int("PERA_VERIFIED_ASSET_CAP", 400)

# Mention-based discovery (GitHub topic:algorand repos' homepage fields, the
# Medium algorand tag feed, Bluesky post links): leads for the crawl frontier,
# NOT curated listings — discovered domains land pending and earn approval
# through the normal preview/content scoring.
MENTION_DISCOVERY_ENABLED = env_bool("MENTION_DISCOVERY_ENABLED", True)
MENTION_GITHUB_REPO_CAP = env_int("MENTION_GITHUB_REPO_CAP", 50)

# Forum hot-topic lane: threads on forum.algorand.co crossing the engagement
# thresholds become publish signals (one per topic, snapshot-deduped). Cheap:
# one JSON GET per poll, so the cadence can be tight — output volume is still
# governed by the publish pipeline's own caps.
FORUM_POLL_ENABLED = env_bool("FORUM_POLL_ENABLED", True)
FORUM_BASE_URL = env_str("FORUM_BASE_URL", "https://forum.algorand.co")
FORUM_MIN_POSTS = env_int("FORUM_MIN_POSTS", 8)
FORUM_MIN_LIKES = env_int("FORUM_MIN_LIKES", 10)
# The forum's own stable service_id (deploy/seeds/prod_services.toml), passed
# as ingest_publish_signal's venue_service_id: every hot topic gets its own
# per-item service_id ("forum-topic:<id>"), but the VENUE is always this one
# forum — reusing its real registry id (rather than inventing a new one) lets
# to_compose_selection._artifact_pool correctly read a hot-topic artifact as
# UPDATE_POOL once the forum itself has ever published, instead of every
# thread permanently occupying the guaranteed NEW_SERVICE_POOL floor.
FORUM_VENUE_SERVICE_ID = env_str("FORUM_VENUE_SERVICE_ID", "algorand-forum")

# xGov proposal watch: proposals are apps created by the registry's escrow
# account (registry id from algorandfoundation/xgov-beta-sc README), one
# signal per (proposal, phase) — submitted / voting / approved / rejected /
# funded / blocked.
XGOV_POLL_ENABLED = env_bool("XGOV_POLL_ENABLED", True)
XGOV_REGISTRY_APP_ID = env_int("XGOV_REGISTRY_APP_ID", 3147789458)
# Phases older than this never signal — prevents the first poll from
# backfilling every historical proposal as "news".
XGOV_MAX_PHASE_AGE_DAYS = env_int("XGOV_MAX_PHASE_AGE_DAYS", 14)
# xGov's own stable venue service_id, same purpose as FORUM_VENUE_SERVICE_ID
# above — every proposal phase gets its own per-item service_id
# ("xgov-proposal:<id>:<phase>"), but the VENUE is always the xGov program
# itself. No pre-existing service_registry row for xgov.algorand.co was found
# (unlike the forum's), so this follows the same domain-based convention
# ensure_monitored_service uses (domain with "." replaced by "-").
XGOV_VENUE_SERVICE_ID = env_str("XGOV_VENUE_SERVICE_ID", "xgov-algorand-co")

# Pending-pool retro-pass: refresh content scores for pending frontier domains
# and PROMOTE those whose crawled-content relevance clears the bar. Promotion
# only — auto-reject stays off (2026-06-22 hard lesson: judge on content, and
# never let the system permanently bury a domain a human hasn't seen).
FRONTIER_RETRO_PROMOTE_ENABLED = env_bool("FRONTIER_RETRO_PROMOTE_ENABLED", True)
FRONTIER_CONTENT_PROMOTE_SCORE = env_float("FRONTIER_CONTENT_PROMOTE_SCORE", 0.5)

# Hard cap on items waiting in the admin classifier queue. Once reached, the
# pipeline stops composing/holding new reviews until the admin clears some.
MAX_PENDING_REVIEWS = env_int("MAX_PENDING_REVIEWS", 1)

# Approved-queue (over-cap) releases share the same pacing clock as the
# primary standard-publish path — see NEWS_STANDARD_INTERVAL_HOURS and
# publish_schedule.is_standard_publish_due(). No separate gap setting here.
#
# Whether a backlog of admin-approved articles still awaiting release should
# PAUSE new-content intake (the diff-check / "pull top topic"). Default
# False: classification intake is upstream of and independent from the
# public-feed drip — a couple of approvals must not starve the review queue
# for hours.
PAUSE_INTAKE_ON_FEED_BACKLOG = env_bool("PAUSE_INTAKE_ON_FEED_BACKLOG", False)

# Single kill-switch for ALL automatic composition (celery-beat AND
# admin-triggered). Before this existed, "pause auto-compose for testing"
# was done by setting MISTRAL_DIFF_POLL_SECONDS / PUBLISH_QUEUE_DRAIN_SECONDS
# / PUBLISH_BREAKING_DRAIN_SECONDS (retired 2026-08-25 with the BREAKING tier)
# / ENSURE_REVIEW_READY_SECONDS (also retired 2026-08-25) to a huge interval
# (2026-08-05) -- that only blocks celery-beat's OWN schedule. It does NOT
# block _trigger_compose_next() in admin/stores/cassandra.py, which fires the
# live compose-trigger task (drain_to_compose, formerly
# drain_standard_publish_queue) directly (by design, so approving a review
# composes the next candidate immediately) -- a leak that let a 107-item
# backlog auto-drain itself while the beat freeze was still active (found
# live 2026-08-09). Check this flag at every entry point instead of each
# task's own scheduling interval.
AUTO_COMPOSE_PAUSED = env_bool("AUTO_COMPOSE_PAUSED", False)

# DeepSeek peak/off-peak billing (effective 2026-08-16 16:00 UTC): peak hours
# cost roughly 2-4x off-peak for the same tokens. Comma-separated "HH-HH"
# UTC ranges (end exclusive) -- default matches DeepSeek's announced
# schedule (01:00-04:00, 06:00-10:00 UTC). Checked at the SAME shared
# funnel point as AUTO_COMPOSE_PAUSED (article_composer.compose_scrape_
# article/compose_weekly_digest, not scattered per-task entries) for
# exactly the reason documented above: a gate checked only at celery-beat
# entries misses on-demand-triggered composes entirely (2026-08-09 leak).
# Owner decision 2026-08-15: confine ALL compose (including breaking news --
# no exception) to off-peak hours, with a start-margin so a long-running
# compose already in flight when off-peak ends is fine, but a NEW one won't
# be started close enough to peak that it could still be running once peak
# begins.
LLM_PEAK_HOURS_UTC = env_str("LLM_PEAK_HOURS_UTC", "1-4,6-10")
# Real DeepSeek benchmark composes ran up to ~60 minutes (2026-08-14/15
# LumiRogue benchmark); 90 gives real buffer above that observed worst case
# rather than cutting it close.
LLM_PEAK_MARGIN_MINUTES = env_int("LLM_PEAK_MARGIN_MINUTES", 90)
# DeepSeek policy change (2026-08-25): weekends have no peak/off-peak split
# at all -- everything is off-peak pricing. Comma-separated Python weekday()
# ints (Monday=0..Sunday=6); default is Saturday+Sunday. These days skip the
# hour-window check in LLM_PEAK_HOURS_UTC entirely, all day.
LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC = env_str("LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "5,6")

# Let the Mistral writer call live tools (price, chain head, platform search)
# on demand while composing. Off = single-shot prompt with pre-gathered context.
WRITER_TOOLS_ENABLED = env_bool("WRITER_TOOLS_ENABLED", True)

# Investigative-journalism tools for the writer agent (Phase 1: free/no-key
# OSINT lookups). Optional API keys: OPENSANCTIONS_API_KEY,
# OPENCORPORATES_API_TOKEN, COURTLISTENER_TOKEN, GITHUB_TOKEN,
# COMPANIES_HOUSE_API_KEY (free UK-specific corporate registry lookup).
INVESTIGATIVE_TOOLS_ENABLED = env_bool("INVESTIGATIVE_TOOLS_ENABLED", True)

# Free public Bluesky post search for community sentiment (no Twitter/X, no key).
BLUESKY_SEARCH_ENABLED = env_bool("BLUESKY_SEARCH_ENABLED", True)

# X (Twitter) recent-search, paid per-resource on X's pay-as-you-go API (no
# subscription/minimum, credits deducted per call -- see
# https://docs.x.com/x-api/getting-started/pricing). Originally a live
# per-compose writer tool call (shipped 2026-08-21, rationed by a daily
# Redis-backed call budget); redesigned 2026-08-25 into a weekly scheduled
# sweep (workers/app/modules/newspaper/x_search_sweep.py, beat-scheduled in
# celery_app.py) that calls X once per TRACKED ecosystem service
# (service_registry -- the same list llm_diff_check.py polls) and stores
# results in the x_search_weekly table (see x_search_store.py), superseding
# the previous week's row each run. The writer's search_x tool
# (research_tools.py) now reads that cache instead of calling X live, so an
# article compose no longer spends money on this at all -- billing is now a
# known, fixed weekly headcount instead of an open-ended per-compose budget.
# X_SEARCH_ENABLED is the master kill switch: it gates the weekly beat task
# (nothing is swept when off) and the tool's own registration (nothing to
# read when the sweep never runs). research_tools.py still fixes
# max_results at 10 (X's own API minimum) for the sweep's own calls, and the
# per-session call cap in llm_openai_compatible.py's _CALL_CAPPED_TOOLS is
# kept on search_x anyway -- cheap to keep, and it now guards against a
# runaway tool-calling loop rather than cost (the cached reads are free).
X_BEARER_TOKEN = env_str("X_BEARER_TOKEN", "")
X_SEARCH_ENABLED = env_bool("X_SEARCH_ENABLED", False)
# Defensive ceiling on how many tracked services one weekly sweep run calls
# X for -- NOT a spend-limiting knob the way the old daily cap was (the
# sweep itself is now the only thing that spends money on this at all: a
# known weekly headcount, one call per tracked service). This just stops
# the sweep from silently ballooning if service_registry grows past a sane
# weekly budget; raise it if the tracked-service count legitimately outgrows
# it.
X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES = env_int("X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES", 200)

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
# Lightweight heuristic-grader floor (article_grader.grade_article_draft, the
# same score review_draft/_review_and_revise already compute during compose).
# Below this, a would-be direct-publish draft is diverted to human review
# instead — this floor was never actually enforced before 2026-07-05 (grade was
# computed and discarded), and 3+ recent auto-published drafts scored right at
# this line, so it defaults ON (unlike GATEKEEPER_ENFORCE above, which is still
# unvalidated ML output).
WRITER_QUALITY_GATE_ENABLED = env_bool("WRITER_QUALITY_GATE_ENABLED", True)
WRITER_QUALITY_FLOOR = env_float("WRITER_QUALITY_FLOOR", 6.0)
# Autonomous mode for archive-refresh recomposes (owner decision, 2026-07-12):
# recompose_published may swap a draft straight onto the LIVE article without
# a human click when the draft clears a HIGHER bar than fresh-article
# auto-publish — this overwrites a page that's already public, so the floor is
# intentionally stricter than WRITER_QUALITY_FLOOR. Backtested against the 5
# Tier-1 archive recomposes (all hand-approved 2026-07-12): 4/5 scored >=8.0
# and would have auto-applied; the 5th (a 93-char headline, 3 over the cap)
# correctly failed the headline check and would have stayed in review — the
# gate catching exactly the one article that needed a human look.
#
# 2026-08-25: this floor (and FRESH_AUTO_APPROVE_GRADE_FLOOR below) now
# compares against the FUSED grade (article_grader.fuse_quality_into_grade:
# 0.75*quality_rubric + 0.15*structure + 0.10*length), same scale as
# WRITER_QUALITY_FLOOR above and the compose-time revision loop. Previously
# both compared against the schema-only grade (structure*0.55 + length*0.45)
# — a well-formatted-but-shallow draft could clear the bar with ZERO weight
# on the LLM rubric's narrative-quality judgment, the exact gap
# fuse_quality_into_grade's 2026-08-06 fix closed everywhere except these two
# publish-time gates (root-caused and fixed 2026-08-25).
#
# Kept at 8.0 on the new scale, not recalibrated blind: for a
# structurally-clean draft (structure=length=1.0, the normal case for
# anything that would reach this gate at all), clearing 8.0 requires
# quality_rubric >= (0.8 - 0.15 - 0.10) / 0.75 = 0.733, i.e. an average rubric
# score of ~3.9/5 across narrative_synthesis/technical_depth/critical_distance/
# repetition — a solid-to-strong bar, and still clearly stricter than
# WRITER_QUALITY_FLOOR=6.0 (~2.9/5 average required at the same structure/
# length), preserving the "stricter AND-gate than the base quality floor"
# relationship both floors were designed around. The 2026-07-12 backtest
# above no longer directly applies (schema-only scale), but the drafts it
# covered all had clean structure, so under the new formula they'd need
# rubric averages in the high-3s/low-4s to still clear it — consistent with
# "all 5 were hand-approved" (approved drafts should read as solid-to-strong,
# not merely well-formatted).
RECOMPOSE_AUTO_APPLY_ENABLED = env_bool("RECOMPOSE_AUTO_APPLY_ENABLED", True)
RECOMPOSE_AUTO_APPLY_GRADE_FLOOR = env_float("RECOMPOSE_AUTO_APPLY_GRADE_FLOOR", 8.0)
# Same strict AND-gate design as recompose, applied to BRAND NEW content the
# classifier wasn't confident about (would otherwise always wait for a human
# review click). Floor matches recompose's, not the looser 6.0 fresh-content
# floor used elsewhere (WRITER_QUALITY_FLOOR) — fresh content has zero prior
# human vetting at all, unlike recompose which only touches content a human
# already approved once, so there's no argument for a looser bar here
# (owner decision 2026-07-12). Also fused-grade scale as of 2026-08-25 — see
# the reasoning above RECOMPOSE_AUTO_APPLY_GRADE_FLOOR, which applies
# identically here (same floor value, same formula).
FRESH_AUTO_APPROVE_ENABLED = env_bool("FRESH_AUTO_APPROVE_ENABLED", True)
FRESH_AUTO_APPROVE_GRADE_FLOOR = env_float("FRESH_AUTO_APPROVE_GRADE_FLOOR", 8.0)

# --------------------------------------------------------------------------- #
# Editorial-room artifacts -- see artifact_store.py / artifact_priority.py /
# to_compose_selection.py. LIVE (2026-08-25): these knobs govern the
# artifacts/to_compose tables that now drive real selection (see
# queue_drain_tasks.drain_to_compose); publish_queue's own compute_priority
# formula still runs (rollback-safety dual-write) but no longer selects
# anything.
# --------------------------------------------------------------------------- #
# Priority sweep: how often the daily beat recomputes every PENDING
# artifact's priority (see tasks/artifact_tasks.py:sweep_artifact_priorities).
ARTIFACT_PRIORITY_SWEEP_SECONDS = env_int("ARTIFACT_PRIORITY_SWEEP_SECONDS", 86400)
# Word-count score component: diminishing returns past this many words (a
# sqrt curve, not a hard cap -- see artifact_priority.word_count_score).
ARTIFACT_WORD_COUNT_CAP = env_int("ARTIFACT_WORD_COUNT_CAP", 1200)
ARTIFACT_WORD_COUNT_MAX_SCORE = env_float("ARTIFACT_WORD_COUNT_MAX_SCORE", 10.0)
# Timeliness score component: exponential half-life decay from event_date
# (falls back to created_at), asymptotically approaching (never reaching)
# ARTIFACT_TIMELINESS_FLOOR -- an explicit owner decision that old-but-real
# content must stay theoretically reachable "except when we have nothing
# else to report". Shape mirrors gatekeeper/fact_align.py's
# source_timeliness_score/_timeliness_from_anchor age-based decay used by
# the LIVE publish_score.py priority (that function's linear decay hard-cuts
# to 0 at PAGE_STALE_MAX_AGE_DAYS -- this one deliberately never does).
ARTIFACT_TIMELINESS_MAX_SCORE = env_float("ARTIFACT_TIMELINESS_MAX_SCORE", 10.0)
ARTIFACT_TIMELINESS_FLOOR = env_float("ARTIFACT_TIMELINESS_FLOOR", 1.0)
ARTIFACT_TIMELINESS_HALF_LIFE_DAYS = env_float("ARTIFACT_TIMELINESS_HALF_LIFE_DAYS", 21.0)
# Known-important-service boost: flat bonus for an artifact whose URL domain
# is in the SAME ecosystem_listed directory registry the crawler-discovery
# scorer already uses for chain-silent-but-important services (see
# ecosystem_sync.ecosystem_listed_domains) -- deliberately not a second
# registry.
ARTIFACT_ECOSYSTEM_LISTED_BOOST = env_float("ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
# Guaranteed-new-service platform lane (2026-08-26, see to_compose_selection.py
# ARTIFACT_NEW_SERVICE_MIN_SHARE / _rank_platform_picks): minimum SHARE of
# platform_n slots reserved for services this platform has never composed/
# published before, so a large, frequently-updating service can't saturate
# every platform slot with routine small updates and crowd out first-ever
# coverage of smaller/newer ones. Explicit owner example: "fifty percent need
# to go to new services composition". A MINIMUM guarantee, not a rigid
# partition -- see that module for the backfill-when-a-pool-is-thin and
# surplus-goes-to-highest-priority-remaining-candidate rules.
ARTIFACT_NEW_SERVICE_MIN_SHARE = env_float("ARTIFACT_NEW_SERVICE_MIN_SHARE", 0.5)
# Per-service artifact concatenation cap (2026-08-26, see
# artifact_store.insert_artifact): a new artifact for a service_id that
# already has a pending artifact CONCATENATES onto it (old content + new
# content) rather than replacing it outright, so a chronically-small-priority
# service's unaddressed changes compound instead of being silently discarded
# each cycle. This bounds how large the ACCUMULATED OLD portion of that
# concatenation can grow before older material starts getting trimmed from
# the front (newest content is never trimmed) -- a defensive ceiling against
# a service updating constantly while never getting composed, not a tuned
# knob: at this platform's volume (~7 articles/day, ~114 articles total as of
# 2026-08-25) a realistic accumulation is a handful of updates long before
# this is ever reached.
ARTIFACT_CONCAT_MAX_OLD_CHARS = env_int("ARTIFACT_CONCAT_MAX_OLD_CHARS", 20000)
# Skip-count score component (2026-08-27, see artifact_priority.skip_count_score):
# a direct "how many times has this service's pending artifact been
# superseded by a newer ignored update" signal, read straight from
# metadata["segments"] (the provenance trail _concatenate_with_pending builds,
# one entry per concatenation cycle) rather than inferred indirectly from
# word_count_score's growth. Needed because word_count_score saturates at
# ARTIFACT_WORD_COUNT_CAP words (1200 -- roughly 7200 chars at a ~6-char
# average word), which a service can reach in just a handful of
# concatenation cycles, well before ARTIFACT_CONCAT_MAX_OLD_CHARS above (the
# concatenation mechanism's own, ~3x larger, ceiling) is ever reached. Past
# that point word_count_score stops moving at all, silently stalling the
# "chronically-ignored services keep climbing" compounding the concatenation
# mechanism was explicitly built to deliver (see that constant's own comment
# and insert_artifact's docstring) for exactly the services ignored longest.
# Linear (not sqrt) and capped independently of word count, so this keeps
# differentiating "ignored 3 times" from "ignored 10 times" even once
# word_count_score has flatlined for both.
ARTIFACT_SKIP_COUNT_CAP = env_int("ARTIFACT_SKIP_COUNT_CAP", 10)
ARTIFACT_SKIP_COUNT_MAX_SCORE = env_float("ARTIFACT_SKIP_COUNT_MAX_SCORE", 6.0)
# Article is flagged when the grounded fraction of its numeric claims falls below
# this (too many figures with no anchor in the tool trace). This deterministic
# check (gatekeeper/live.py) is the only factuality gate that actually runs in
# production; the ModernBERT quality/relevance heads (gatekeeper/model.py) have
# no training or serving wiring left (removed as dead code 2026-08-25 — the
# quality-head checkpoint they used to produce had no reader, see
# docs/modules/gatekeeper.md).
GATEKEEPER_FACT_MIN = env_float("GATEKEEPER_FACT_MIN", 0.80)
