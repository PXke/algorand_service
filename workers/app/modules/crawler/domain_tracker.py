from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

# Multi-label public suffixes — keep one extra label so the registrable domain
# is right (example.co.uk, not co.uk). Not exhaustive; covers the common ones
# (we don't ship the full Public Suffix List / tldextract).
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk", "me.uk", "ltd.uk", "plc.uk",
    "co.jp", "co.kr", "co.za", "co.nz", "co.in", "co.il", "co.id", "co.th",
    "com.au", "com.br", "com.mx", "com.tr", "com.cn", "com.sg", "com.hk",
    "com.tw", "com.ar", "com.co", "com.ua", "com.pl", "com.ng",
    # India academic/gov public suffixes (one entity per subdomain).
    "ac.in", "edu.in", "gov.in", "res.in", "nic.in", "org.in", "net.in",
})

# Platform / hosting suffixes where the SUBDOMAIN is the real identity
# (foo.medium.com and bar.medium.com are different publishers). Treated like a
# public suffix so we keep the subdomain label instead of collapsing them into
# one "medium.com" source. Keep in sync with the backend admin store copy
# (AdminCassandraStore._domain_from_url); parity guarded by
# test_domain_from_url_parity.py in both services.
_PLATFORM_SUFFIXES = frozenset({
    "medium.com", "substack.com", "blogspot.com", "wordpress.com", "ghost.io",
    "github.io", "gitbook.io", "gitbook.com", "notion.site", "super.site",
    "netlify.app", "vercel.app", "pages.dev", "web.app", "firebaseapp.com",
    "herokuapp.com", "onrender.com", "readthedocs.io", "ipfs.io", "w3s.link",
    "fleek.co", "surge.sh", "webflow.io", "wixsite.com", "replit.app", "repl.co",
})


def _crawl_budget_client():
    import redis

    from app.core.config import REDIS_URL

    return redis.from_url(REDIS_URL, decode_responses=True)


def _crawl_budget_key(domain: str) -> str:
    return f"algorand:crawl:pages:{domain}"


def record_domain_crawl(domain: str) -> int:
    """Count one fetched page for a domain in the rolling window; returns the
    new total. Best-effort — Redis down means no increment (fail-open)."""
    if not domain:
        return 0
    from app.core.config import CRAWL_PAGECOUNT_TTL

    try:
        client = _crawl_budget_client()
        total = int(client.incr(_crawl_budget_key(domain)))
        if total == 1:
            client.expire(_crawl_budget_key(domain), CRAWL_PAGECOUNT_TTL)
        return total
    except Exception:
        return 0


def domain_crawl_count(domain: str) -> int:
    """Pages fetched for a domain in the current rolling window (0 if unknown).
    Fails open (returns 0 = under budget) so a Redis outage never blocks crawl."""
    if not domain:
        return 0
    try:
        value = _crawl_budget_client().get(_crawl_budget_key(domain))
        return int(value) if value else 0
    except Exception:
        return 0


def domain_crawl_budget_exhausted(domain: str) -> bool:
    from app.core.config import CRAWL_MAX_PAGES_PER_DOMAIN

    return domain_crawl_count(domain) >= CRAWL_MAX_PAGES_PER_DOMAIN


def _compose_key(domain: str) -> str:
    return f"algorand:compose:daily:{domain}"


def _compose_cooldown_key(domain: str) -> str:
    return f"algorand:compose:cooldown:{domain}"


def record_domain_compose(domain: str) -> int:
    """Count one NEW article compose for a domain in the rolling day, and stamp a
    cooldown so the same domain isn't composed again until it expires (diversity
    spacing — see domain_in_cooldown)."""
    if not domain:
        return 0
    from app.core.config import COMPOSE_DAILY_TTL, COMPOSE_DOMAIN_COOLDOWN_HOURS

    try:
        client = _crawl_budget_client()
        total = int(client.incr(_compose_key(domain)))
        if total == 1:
            client.expire(_compose_key(domain), COMPOSE_DAILY_TTL)
        if COMPOSE_DOMAIN_COOLDOWN_HOURS > 0:
            client.set(
                _compose_cooldown_key(domain), "1", ex=COMPOSE_DOMAIN_COOLDOWN_HOURS * 3600
            )
        return total
    except Exception:
        return 0


def domain_in_cooldown(domain: str) -> bool:
    """True when this domain published/composed within COMPOSE_DOMAIN_COOLDOWN_HOURS.
    A presence-with-TTL check (the key is set on each compose and self-expires), so
    it spaces successive articles from one registrable domain. Fails open (Redis
    down → not in cooldown) so an outage never blocks publishing."""
    if not domain:
        return False
    from app.core.config import COMPOSE_DOMAIN_COOLDOWN_HOURS

    if COMPOSE_DOMAIN_COOLDOWN_HOURS <= 0:
        return False
    try:
        return _crawl_budget_client().get(_compose_cooldown_key(domain)) is not None
    except Exception:
        return False


def _service_cooldown_key(service_id: str) -> str:
    return f"algorand:compose:service_cooldown:{service_id}"


def record_service_compose(service_id: str) -> None:
    """Stamp a per-service cooldown alongside the per-domain one (see
    record_domain_compose) — this is the key that still catches a repeat when
    the project's two domains don't collapse to one registrable domain."""
    if not service_id:
        return
    from app.core.config import COMPOSE_SERVICE_COOLDOWN_HOURS

    if COMPOSE_SERVICE_COOLDOWN_HOURS <= 0:
        return
    try:
        _crawl_budget_client().set(
            _service_cooldown_key(service_id), "1", ex=COMPOSE_SERVICE_COOLDOWN_HOURS * 3600
        )
    except Exception:
        return


def service_in_cooldown(service_id: str) -> bool:
    """True when this SERVICE (any of its domains) published/composed within
    COMPOSE_SERVICE_COOLDOWN_HOURS. Complements domain_in_cooldown for a project
    whose domains don't share a registrable domain (e.g. a Medium blog plus its
    own site) — the per-domain cooldown can't see across those on its own.
    Fails open (Redis down -> not in cooldown)."""
    if not service_id:
        return False
    from app.core.config import COMPOSE_SERVICE_COOLDOWN_HOURS

    if COMPOSE_SERVICE_COOLDOWN_HOURS <= 0:
        return False
    try:
        return _crawl_budget_client().get(_service_cooldown_key(service_id)) is not None
    except Exception:
        return False


def domain_compose_cap_reached(domain: str) -> bool:
    """True when a domain already composed its daily quota — skip re-composing.
    Fails open (Redis down → not capped) so an outage never blocks publishing."""
    if not domain:
        return False
    from app.core.config import COMPOSE_MAX_PER_DOMAIN_PER_DAY

    try:
        value = _crawl_budget_client().get(_compose_key(domain))
        return int(value) >= COMPOSE_MAX_PER_DOMAIN_PER_DAY if value else False
    except Exception:
        return False


# --- Frontier auto-approve tally -------------------------------------------
def _auto_approved_key(day: str) -> str:
    return f"algorand:frontier:autoapproved:{day}"


def record_domain_auto_approved(domain: str) -> None:
    """Tally a score-gated frontier auto-approve in a per-day Redis SET, so the
    admin can see what the frontier approved without a human. A SET (not a plain
    counter) so the admin also gets the domain list and re-approving the same
    domain isn't double-counted. Best-effort — Redis down is a no-op (the approve
    itself still happened). The backend admin reads the same key for today."""
    if not domain:
        return
    from datetime import UTC, datetime

    try:
        client = _crawl_budget_client()
        key = _auto_approved_key(datetime.now(tz=UTC).strftime("%Y-%m-%d"))
        client.sadd(key, domain)
        client.expire(key, 172800)  # keep ~2 days so a 'today' read always resolves
    except Exception:
        return


# --- Rejected-URL cooldown -------------------------------------------------
# Written by the backend when an admin rejects a review (see the admin store);
# read here so the worker's enqueue path can suppress that URL for a while.
# Both services must point REDIS_URL / settings.redis_url at the same Redis DB
# (they share the default db 0).
def reject_cooldown_key(url: str) -> str:
    import hashlib

    digest = hashlib.sha1((url or "").strip().lower().encode("utf-8")).hexdigest()
    return f"algorand:reject:url:{digest}"


def url_recently_rejected(url: str) -> bool:
    """True when this URL was rejected in review within the cooldown window.
    Fails open (Redis down → not suppressed)."""
    if not url:
        return False
    try:
        return _crawl_budget_client().get(reject_cooldown_key(url)) is not None
    except Exception:
        return False


def domain_from_url(url: str) -> str:
    """Registrable domain (eTLD+1) for a URL — collapses subdomains.

    e.g. xgov.algorand.co / www.algorand.co / algorand.co -> "algorand.co".
    Treating subdomains as one domain stops the frontier from spawning a
    separate source per subdomain (xgov./specs./dev.algorand.co), which would
    otherwise each compose its own near-duplicate article.
    """
    raw = url.strip()
    # SPA sources are registered as browser://https://… — strip the engine prefix
    # so the eTLD+1 resolves to the real host (else urlparse reads it as "https").
    low = raw.lower()
    if low.startswith("browser://http://") or low.startswith("browser://https://"):
        raw = raw[len("browser://") :]
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return ""
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    last_two = ".".join(labels[-2:])
    if last_two in _MULTI_LABEL_SUFFIXES or last_two in _PLATFORM_SUFFIXES:
        return ".".join(labels[-3:]) if len(labels) >= 3 else host
    return last_two


def get_domain_status(domain: str) -> dict[str, Any] | None:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts

    if not domain:
        return None
    session = get_cassandra_session()
    row = session.execute(DomainTrackingStmts.GET_STATUS, (domain,)).one()
    if row is None:
        return None
    return {
        "domain": row.domain,
        "last_crawled_at": row.last_crawled_at,
        "last_online_at": row.last_online_at,
        "relevance_score": float(row.relevance_score) if row.relevance_score is not None else 0.0,
        "category": row.category or "",
        "is_relevant": bool(row.is_relevant) if row.is_relevant is not None else True,
        "metadata": dict(row.metadata or {}),
        "frontier_status": getattr(row, "frontier_status", None) or "",
    }


def should_recrawl_domain(domain: str) -> bool:
    """Whether the frontier may (re)crawl this domain.

    - Unknown domain → allow (it gets classified on first fetch).
    - Relevant → allow.
    - Admin-rejected (is_relevant=False set via the admin reject) → NEVER recrawl;
      an explicit human reject is permanent.
    - Auto-flagged irrelevant → re-check only after the configured window
      (FRONTIER_RECRAWL_DAYS_IRRELEVANT), in case it has since become relevant.
    """
    from app.core.config import FRONTIER_RECRAWL_DAYS_IRRELEVANT

    status = get_domain_status(domain)
    if status is None:
        return True
    if status.get("is_relevant", True):
        return True
    # Irrelevant. An explicit admin reject is a permanent dead end.
    meta = status.get("metadata") or {}
    if meta.get("frontier_set_by_admin") == "true" or meta.get("frontier_status") == "dead_end":
        return False
    last = status.get("last_crawled_at")
    if last is None:
        # Irrelevant and never actually crawled (e.g. rejected while pending) —
        # don't start crawling it now. (Previously returned True, so rejected
        # domains kept getting crawled.)
        return False
    if isinstance(last, datetime) and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    cutoff = datetime.now(tz=UTC) - timedelta(days=FRONTIER_RECRAWL_DAYS_IRRELEVANT)
    return last < cutoff


def update_domain_status(
    domain: str,
    *,
    relevance_score: float,
    category: str = "",
    is_relevant: bool | None = None,
    online: bool = True,
    metadata: dict[str, str] | None = None,
    frontier_status_override: str | None = None,
) -> None:
    """Record a per-crawl SIGNAL (score, online, last_crawled, category).

    A single crawled page is only a SIGNAL — it must NEVER decide a domain's
    relevance. The frontier DECISION (is_relevant + approved / dead_end / pending)
    belongs to the admin or to a deliberate content-relevance task, so both
    ``is_relevant`` and ``frontier_status`` are PRESERVED unless a caller passes
    them explicitly. Likewise ``metadata`` is MERGED into the existing map, never
    replaced, so the admin's ``frontier_set_by_admin`` permanence marker survives
    an incidental recrawl. Brand-new domains default to is_relevant=True /
    frontier_status "pending" (held for review).

    Previously this overwrote is_relevant (and wiped metadata) on every page, so a
    thin / off-topic page silently flipped admin-approved domains off and erased
    the human marker — making reactivation un-sticky.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts

    if not domain:
        return
    now = datetime.now(tz=UTC)
    session = get_cassandra_session()
    existing = session.execute(DomainTrackingStmts.GET_FOR_UPDATE, (domain,)).one()

    # Decision columns: preserve unless the caller overrides them explicitly.
    if is_relevant is not None:
        resolved_relevant = is_relevant
    elif existing is not None and existing.is_relevant is not None:
        resolved_relevant = bool(existing.is_relevant)
    else:
        resolved_relevant = True  # brand-new domain
    status = frontier_status_override
    if status is None:
        status = (existing.frontier_status if existing else None) or "pending"

    # Merge metadata into the existing map (never clobber admin markers).
    merged_meta = dict(existing.metadata or {}) if existing else {}
    if metadata:
        merged_meta.update(metadata)

    # Don't blank an existing category when the caller didn't supply one.
    resolved_category = category or (existing.category if existing else "") or ""
    # Keep the last-seen-online timestamp when this crawl didn't reach the host.
    last_online = now if online else (existing.last_online_at if existing else None)

    session.execute(
        DomainTrackingStmts.INSERT,
        (
            domain,
            now,
            last_online,
            relevance_score,
            resolved_category,
            resolved_relevant,
            merged_meta,
            status,
        ),
    )


# Generic platforms that are never Algorand-news frontiers — visiting them from
# a discovered link is always a dead end. Admin rejects extend this dynamically
# via domain_tracking.is_relevant=False.
_DEAD_END_DOMAINS = frozenset({
    "amazon.com", "google.com", "youtube.com", "facebook.com", "twitter.com",
    "x.com", "instagram.com", "linkedin.com", "tiktok.com", "apple.com",
    "microsoft.com", "netflix.com", "play.google.com", "apps.apple.com",
    "reddit.com", "discord.com", "discord.gg", "t.me", "wikipedia.org",
})

# Core Algorand ecosystem domains that must NEVER be AUTO-rejected. A thin/
# marketing preview can score ~0 even for the foundation's own site, so preview
# score alone is unsafe — these (and any domain whose name itself carries an
# Algorand signal) are always held for human review instead of auto-rejected.
_PROTECTED_DOMAINS = frozenset({
    "algorand.co", "algorand.com", "algorand.foundation", "algorand.org",
    "algorandtechnologies.com", "perawallet.app", "defly.app", "tinyman.org",
    "folks.finance", "vestige.fi", "algokit.io", "nodely.io", "algonode.io",
    "allo.info", "lora.algokit.io", "goalseeker.app",
})


# First labels that look Algorand-ish but aren't. Protection is deliberately
# broad (a domain whose name starts with "algo" is usually an ASA project) because
# wrongly AUTO-rejecting a real project — the pact.fi/perawallet incident — is far
# worse than holding one extra off-topic domain for human review. So we only carve
# out the clearest English false positives rather than narrowing the prefix rule.
_ALGO_PREFIX_FALSE_POSITIVES = frozenset({
    "algorithm", "algorithms", "algorithmic", "algospeak", "algorhythm",
})


def is_protected_domain(domain: str) -> bool:
    """True for domains we must never AUTO-reject (only a human may dead-end them):
    the core allowlist, plus any domain whose registrable name carries an Algorand
    signal — the domain name is the safety net when the preview text scores low."""
    d = (domain or "").lower().strip().strip(".")
    if not d:
        return False
    if d in _PROTECTED_DOMAINS:
        return True
    if "algorand" in d:
        return True
    label = d.split(".")[0]
    return label.startswith("algo") and label not in _ALGO_PREFIX_FALSE_POSITIVES


def _is_blocklisted(domain: str) -> bool:
    """Hard generic-platform blocklist (_DEAD_END_DOMAINS + FRONTIER_BLOCKLIST_EXTRA).
    Pure string check on the domain and its parent suffixes — no DB read."""
    from app.core.config import FRONTIER_BLOCKLIST_EXTRA

    if not domain:
        return True
    blocked = set(_DEAD_END_DOMAINS)
    blocked.update(d.strip().lower() for d in FRONTIER_BLOCKLIST_EXTRA.split(",") if d.strip())
    parts = domain.lower().split(".")
    return any(".".join(parts[i:]) in blocked for i in range(len(parts) - 1))


def is_dead_end_domain(domain: str) -> bool:
    """Frontier gate: True when discovered links to this domain should not be
    followed — blocklisted platform, or a domain the relevance classifier /
    admin feedback already marked irrelevant."""
    if _is_blocklisted(domain):
        return True
    status = get_domain_status(domain)
    return status is not None and not status.get("is_relevant", True)


def evaluate_frontier_link(domain: str) -> tuple[str, bool]:
    """Single status read → (frontier_state, dead_end) for the discovery loop.

    Folds is_dead_end_domain + frontier_status into ONE get_domain_status call
    (they were querying the same row twice per link). state is
    'unknown' | 'pending' | 'approved' | 'dead_end'; dead_end is True when the
    link must NOT be followed (blocklisted, or marked irrelevant)."""
    if _is_blocklisted(domain):
        return "dead_end", True
    status = get_domain_status(domain)
    if status is None:
        return "unknown", False
    if not status.get("is_relevant", True):
        return "dead_end", True
    declared = status.get("frontier_status") or (status.get("metadata") or {}).get(
        "frontier_status", ""
    )
    if declared in ("pending", "approved", "dead_end"):
        return declared, declared == "dead_end"
    return "approved", False


def frontier_status(domain: str) -> str:
    """Frontier state: 'unknown' | 'pending' | 'approved' | 'dead_end'.

    Trust the explicit frontier_status (column first, then metadata) — it is the
    real decision. Only fall back to the is_relevant flag for legacy rows that
    predate the column, so a transient per-page is_relevant signal can no longer
    masquerade as a dead_end verdict.
    """
    status = get_domain_status(domain)
    if status is None:
        return "unknown"
    declared = status.get("frontier_status") or (status.get("metadata") or {}).get(
        "frontier_status", ""
    )
    if declared in ("pending", "approved", "dead_end"):
        return declared
    return "dead_end" if not status.get("is_relevant", True) else "approved"


def ensure_monitored_service(domain: str, *, scrape_url: str = "") -> bool:
    """Approved domain → monitored source in service_registry, so the weekly
    diff beat watches it and its evolution can become update articles. Worker-
    side mirror of the backend admin-approve bridge (admin_set_domain); without
    it, auto-approved domains were crawled for the research corpus but could
    never produce a publish candidate. Never overwrites an existing row (the
    admin may have customised it), and never spawns a service for a domain some
    service already owns (e.g. after an admin merge). Returns True when a new
    service was created."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceRegistryStmts
    from app.modules.newspaper.service_sources import add_web_source, service_for_domain

    if not domain:
        return False
    owner = service_for_domain(domain)
    if owner:
        return False
    service_id = domain.replace(".", "-").lower()
    session = get_cassandra_session()
    url = scrape_url or f"https://{domain}"
    if session.execute(ServiceRegistryStmts.GET_ID, (service_id,)).one() is not None:
        # Legacy service that predates the source tables — claim the mapping.
        add_web_source(service_id, domain=domain, url=url)
        return False
    session.execute(
        ServiceRegistryStmts.UPSERT,
        (
            service_id,
            domain,
            "domain",
            domain,
            url,
            True,
            datetime.now(tz=UTC),
            "domain",
        ),
    )
    add_web_source(service_id, domain=domain, url=url)
    return True


def register_pending_domain(
    domain: str,
    *,
    first_url: str,
    link_text: str = "",
    found_on: str = "",
    preview: dict[str, str] | None = None,
    approved: bool = False,
) -> None:
    """Record a newly met domain. By default HOLD it pending for admin review;
    when ``approved`` (score-gated frontier auto-approve) mark it approved so the
    one-hop frontier explores it immediately. Never used to auto-reject — a
    below-threshold domain is still registered pending, not dead-ended."""
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts

    now = datetime.now(tz=UTC)
    try:
        pscore = float((preview or {}).get("preview_score", "0") or 0)
    except (TypeError, ValueError):
        pscore = 0.0
    state = "approved" if approved else "pending"
    metadata = {
        "frontier_status": state,
        "pending_url": first_url,
        "link_text": link_text[:200],
        "found_on": found_on[:300],
        "preview_title": (preview or {}).get("preview_title", ""),
        "preview_description": (preview or {}).get("preview_description", ""),
        "preview_keywords": (preview or {}).get("preview_keywords", ""),
    }
    if approved:
        # Distinguish auto-approve from an admin verdict so an operator can audit
        # (and so frontier_set_by_admin stays the human-only permanence marker).
        metadata["frontier_status_source"] = "auto_approved"
    get_cassandra_session().execute(
        DomainTrackingStmts.INSERT,
        (
            domain,
            now,
            now,
            pscore,
            "",
            True,
            metadata,
            state,
        ),
    )
