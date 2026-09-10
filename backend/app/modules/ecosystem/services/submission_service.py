"""Public submission flow (design doc section 3): validate, dedupe, liveness-check, store as pending.

Gates run cheapest-first (design doc section 3.2): honeypot and rate limit
are checked by the route before this module is even called (no DB/network
cost for a bot); this module does field validation (free), then the
admin-reject-memory check (one Cassandra read), then the liveness check
(one outbound HTTP call, the most expensive gate), then the atomic LWT
domain claim.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from algorand_shared.slugs import unique_slug

from app.modules.ecosystem.models.domain import (
    CATEGORIES,
    DEFAULT_CATEGORY,
    DEFAULT_STAGE,
    MAX_TAG_LENGTH,
    MAX_TAGS,
    SOURCE_SUBMITTED,
    STAGES,
    STATUS_APPROVED,
    STATUS_PENDING,
    EcosystemError,
    StoredProject,
)
from app.modules.ecosystem.services.liveness import check_liveness
from app.modules.ecosystem.services.markdown_guard import reject_embedded_html
from app.modules.ecosystem.stores.factory import get_project_store

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_MAX_URL_LENGTH = 2048
_MAX_NAME_LENGTH = 60
_MIN_NAME_LENGTH = 2
_MAX_DESCRIPTION_LENGTH = 500
_MIN_DESCRIPTION_LENGTH = 20
_MAX_CATEGORY_SUGGESTION_LENGTH = 60
_GITHUB_PAGES_SUFFIX = ".github.io"

# Social/badge/forge hosts a submission must not point at as its own url
# (design doc section 3.1) -- NOT the crawler's full `_SKIP_HOSTS` set
# (workers/app/modules/crawler/ecosystem_sync.py, a different service/venv
# backend cannot import), just the ones a submitter could plausibly paste
# as "my project's url". GitHub
# repo urls are deliberately NOT here -- "a dev tool's home is its repo" --
# see _domain_key's own docstring for how a repo url is deduped instead.
_BLOCKED_SUBMISSION_HOSTS = frozenset(
    {
        "twitter.com",
        "x.com",
        "discord.gg",
        "discord.com",
        "t.me",
        "facebook.com",
        "linkedin.com",
        "medium.com",
        "youtube.com",
        "reddit.com",
        "npmjs.com",
        "pypi.org",
        "vercel.app",
        "netlify.app",
        "pages.dev",
        "itch.io",
        "shields.io",
        "img.shields.io",
        "awesome.re",
        "gist.github.com",
        "raw.githubusercontent.com",
    }
)


def normalize_name(raw: str) -> str:
    """Trim, length-bound, HTML-reject a submission's display name."""
    name = (raw or "").strip()
    if not (_MIN_NAME_LENGTH <= len(name) <= _MAX_NAME_LENGTH):
        raise EcosystemError(
            "invalid_request", f"name must be {_MIN_NAME_LENGTH}-{_MAX_NAME_LENGTH} characters"
        )
    reject_embedded_html(name, field_name="name")
    return name


def normalize_description(raw: str) -> str:
    """Trim, length-bound, HTML-reject a submission's description.

    Markdown (multi-line, links) is allowed -- rendered client-side through
    the same `{@html}` + DOMPurify allowlist convention every other
    markdown surface in this codebase uses (CLAUDE.md section 5); this is
    the write-time half of that contract: raw HTML is rejected at the door
    (services/markdown_guard.py), never stripped.
    """
    description = (raw or "").strip()
    if not (_MIN_DESCRIPTION_LENGTH <= len(description) <= _MAX_DESCRIPTION_LENGTH):
        raise EcosystemError(
            "invalid_request",
            f"description must be {_MIN_DESCRIPTION_LENGTH}-{_MAX_DESCRIPTION_LENGTH} characters",
        )
    reject_embedded_html(description, field_name="description")
    return description


def normalize_category_suggestion(raw: str) -> str:
    """Trim, length-bound, HTML-reject an optional free-text category suggestion. Never validated against CATEGORIES -- that's the point (see StoredProject.category_suggestion's own docstring)."""
    suggestion = (raw or "").strip()
    if not suggestion:
        return ""
    if len(suggestion) > _MAX_CATEGORY_SUGGESTION_LENGTH:
        raise EcosystemError(
            "invalid_request",
            f"category_suggestion must be at most {_MAX_CATEGORY_SUGGESTION_LENGTH} characters",
        )
    reject_embedded_html(suggestion, field_name="category_suggestion")
    return suggestion


def normalize_url(raw: str, *, field_name: str = "url") -> str:
    """Normalize a submitted url to its canonical form; blank input for an optional field returns "".

    Lowercase scheme and host, keep path and query, so two spellings of the
    same page key on the same normalized string.
    """
    trimmed = (raw or "").strip()
    if not trimmed:
        return ""
    if len(trimmed) > _MAX_URL_LENGTH:
        raise EcosystemError(
            "invalid_request", f"{field_name} must be at most {_MAX_URL_LENGTH} characters"
        )
    parts = urlsplit(trimmed)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise EcosystemError("invalid_request", f"{field_name} must be http or https")
    if not parts.hostname:
        raise EcosystemError("invalid_request", f"{field_name} must include a host")
    host = parts.hostname.lower()
    if host.removeprefix("www.") in _BLOCKED_SUBMISSION_HOSTS or host in _BLOCKED_SUBMISSION_HOSTS:
        raise EcosystemError(
            "invalid_request",
            f"{field_name} must be the project's own site, not a social/badge/forge host ({host})",
        )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, ""))


def domain_key(url: str) -> str:
    """The one-entry-per-domain dedupe key (design doc section 3.2 gate 3).

    Plain eTLD+1-ish host (www-stripped, lowercased) for an ordinary site.
    Shared hosts key on more of the path so distinct projects hosted there
    don't collide: a github.com repo keys on host+org+repo (its first two
    path segments), a *.github.io project page keys on host+first path
    segment (the project name) -- a bare github.io user page (no path) has
    no distinguishing segment and falls back to the bare host.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if host == "github.com" and len(segments) >= 2:
        return f"{host}/{segments[0]}/{segments[1]}".lower()
    if host.endswith(_GITHUB_PAGES_SUFFIX) and segments:
        return f"{host}/{segments[0]}".lower()
    return host


def validate_category(raw: str) -> str:
    """Canonical form of a submission's category; blank means DEFAULT_CATEGORY."""
    category = (raw or "").strip().lower() or DEFAULT_CATEGORY
    if category not in CATEGORIES:
        raise EcosystemError("invalid_request", f"category must be one of: {', '.join(CATEGORIES)}")
    return category


def validate_stage(raw: str) -> str:
    """Canonical form of a submission's stage; blank means DEFAULT_STAGE."""
    stage = (raw or "").strip().lower() or DEFAULT_STAGE
    if stage not in STAGES:
        raise EcosystemError("invalid_request", f"stage must be one of: {', '.join(STAGES)}")
    return stage


def normalize_tag(raw: str) -> str:
    """Canonical stored/searched form of one tag: trimmed and lowercased (lowercase, trimmed)."""
    return (raw or "").strip().lower()


def validate_tags(tags: list[str]) -> list[str]:
    """Normalize a submission's free tags: blank dropped, duplicates folded, bounded to MAX_TAGS."""
    normalized = sorted({normalize_tag(t) for t in tags if normalize_tag(t)})
    if len(normalized) > MAX_TAGS:
        raise EcosystemError("invalid_request", f"at most {MAX_TAGS} tags")
    for tag in normalized:
        if len(tag) > MAX_TAG_LENGTH:
            raise EcosystemError(
                "invalid_request", f"tag `{tag}` is too long (max {MAX_TAG_LENGTH} chars)"
            )
    return normalized


def _admin_previously_rejected(domain: str) -> bool:
    """True when the crawler admin has already dead-ended this domain (domain_tracking.is_relevant == False).

    Sovereignty rule mirrored from workers' _ingest_domain (design doc
    section 3.2 gate 5): a curated listing (or here, a public submission)
    never resurrects an admin reject. Fails OPEN on a read error (CLAUDE.md
    invariant 9): a Cassandra hiccup must not block a legitimate submission.
    """
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DomainTrackingStmts

        row = (
            get_cassandra_session().execute(DomainTrackingStmts.GET_FOR_CORRECTION, (domain,)).one()
        )
        return row is not None and row.is_relevant is False
    except Exception:
        logger.warning(
            "ecosystem submit: admin-reject-memory check failed for %s", domain, exc_info=True
        )
        return False


def existing_domain_message(domain: str) -> str | None:
    """Return an already-listed/already-pending message for a domain that already has an entry, or None if free."""
    found = get_project_store().domain_status(domain)
    if found is None:
        return None
    slug, status = found
    if status == STATUS_APPROVED:
        return f"already listed: /registry/{slug}"
    return f"already pending review: /registry/submissions/{slug}"


def submit_project(
    *,
    name: str,
    url: str,
    description: str,
    category: str,
    repo_url: str = "",
    x402_url: str = "",
    tags: list[str] | None = None,
    contact: str = "",
    category_suggestion: str = "",
) -> StoredProject:
    """Validate, liveness-check, and atomically create one pending submission.

    Raises EcosystemError("duplicate", ...) when the domain is already
    claimed (LWT lost the race, or the pre-check above already caught it --
    the route calls existing_domain_message() first for a friendlier
    message, this is the last-resort atomic guard). Raises
    EcosystemError("unreachable", ...) when the submit-time liveness check
    fails (design doc section 3.2 gate 4) and EcosystemError("rejected",
    ...) when the domain is admin-reject-memory'd (gate 5).
    """
    clean_name = normalize_name(name)
    clean_description = normalize_description(description)
    clean_category = validate_category(category)
    clean_url = normalize_url(url)
    if not clean_url:
        raise EcosystemError("invalid_request", "url is required")
    clean_repo_url = normalize_url(repo_url, field_name="repo_url")
    clean_x402_url = normalize_url(x402_url, field_name="x402_url")
    clean_tags = validate_tags(tags or [])
    clean_category_suggestion = normalize_category_suggestion(category_suggestion)
    domain = domain_key(clean_url)
    if not domain:
        raise EcosystemError("invalid_request", "url must include a host")

    if _admin_previously_rejected(domain):
        raise EcosystemError(
            "rejected", "This domain was previously rejected by an admin review", http_status=403
        )

    liveness = check_liveness(clean_url)
    if not liveness.reachable:
        raise EcosystemError(
            "unreachable",
            "We could not reach this url just now (or it returned a server error). "
            "Fix it and resubmit.",
            http_status=422,
        )

    store = get_project_store()
    now_epoch = int(datetime.now(tz=UTC).timestamp())
    slug = unique_slug(clean_name, fallback=domain, is_taken=lambda s: store.get(s) is not None)
    item = StoredProject(
        slug=slug,
        name=clean_name,
        domain=domain,
        url=clean_url,
        description=clean_description,
        category=clean_category,
        tags=clean_tags,
        repo_url=clean_repo_url,
        x402_url=clean_x402_url,
        source=SOURCE_SUBMITTED,
        status=STATUS_PENDING,
        contact=(contact or "").strip()[:254],
        category_suggestion=clean_category_suggestion,
        submitted_at_epoch=now_epoch,
        last_probed_at_epoch=now_epoch,
        reachable=liveness.reachable,
        last_http_status=liveness.http_status,
    )
    if not store.insert_if_domain_absent(item):
        raise EcosystemError("duplicate", "This domain already has an entry", http_status=409)
    return item


__all__ = [
    "domain_key",
    "existing_domain_message",
    "normalize_category_suggestion",
    "normalize_description",
    "normalize_name",
    "normalize_tag",
    "normalize_url",
    "submit_project",
    "validate_category",
    "validate_stage",
    "validate_tags",
]
