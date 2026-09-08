"""Algorand Open Registry tests: submission gates, admin review, liveness, and read isolation.

Fully offline (conftest's no-network guard). Redis is a fake at the shared
incr_with_expiry seam, the store is the module's own in-memory backend, and
the liveness checker is monkeypatched at check_target -- nothing here
reaches a real network or a real Cassandra.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.core import rate_limit as rate_limit_core
from app.core import serialization
from app.core.http import QueryParams, Request
from app.modules.ecosystem.api import routes as ecosystem_routes
from app.modules.ecosystem.models.domain import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    EcosystemError,
)
from app.modules.ecosystem.services import review_service, submission_service
from app.modules.ecosystem.stores.factory import set_project_store
from app.modules.ecosystem.stores.memory import InMemoryProjectStore
from app.modules.x402_uptime.services.checker import UptimeResult
from app.schemas import EcosystemDecisionRequest, EcosystemSubmitRequest


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the shared incr_with_expiry counter."""

    def __init__(self) -> None:
        """Start with an empty counter table."""
        self.counts: dict[str, int] = {}

    def incr(self, key: str) -> int:
        """Increment and return the counter at `key`."""
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, _key: str, _ttl: int) -> None:
        """No-op: this fake never expires a counter, tests run well under any real TTL."""


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryProjectStore:
    """A fresh in-memory registry store plus a fake Redis, wired in for every test."""
    project_store = InMemoryProjectStore()
    set_project_store(project_store)
    fake_redis = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: fake_redis)
    yield project_store
    set_project_store(None)


@pytest.fixture(autouse=True)
def _always_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default every liveness check to reachable/200 -- individual tests override this to exercise the unreachable path."""
    monkeypatch.setattr(
        "app.modules.ecosystem.services.liveness.check_target",
        lambda url, **_kw: UptimeResult(
            final_url=url,
            reachable=True,
            http_status=200,
            response_time_ms=5,
            resolved_ip="1.2.3.4",
            error="",
        ),
    )


@pytest.fixture(autouse=True)
def _no_admin_reject_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the admin-reject-memory check to "never rejected" -- one test below overrides it."""
    monkeypatch.setattr(submission_service, "_admin_previously_rejected", lambda _domain: False)


def _request(
    *,
    method: str = "POST",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
) -> Request:
    """Build a minimal framework-neutral Request for calling a route handler directly."""
    return Request(
        method=method,
        headers=headers or {"X-Real-IP": "203.0.113.9"},
        query_params=QueryParams(query or {}),
        path_params=path_params or {},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path="/"),
    )


def _submit_body(**overrides: Any) -> bytes:  # noqa: ANN401 -- passthrough JSON payload overrides, any JSON-serializable value
    """A valid submit payload, JSON-encoded, with any field overridden."""
    payload = {
        "name": "Test Project",
        "url": "https://example-project.test/",
        "description": "A factual one-sentence description of the project, twenty-plus chars.",
        "category": "devtools",
        **overrides,
    }
    return serialization.dumps(payload).encode()


# --------------------------------------------------------------------------- #
# Submission: success, honeypot, rate limit, duplicate domain
# --------------------------------------------------------------------------- #
def test_submit_success_creates_pending_entry(store: InMemoryProjectStore) -> None:
    """A valid, reachable submission is stored as a pending, submitted-source entry."""
    resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    assert resp["ok"] is True
    assert resp["status"] == "pending"
    slug = resp["submission_id"]
    item = store.get(slug)
    assert item is not None
    assert item.status == STATUS_PENDING
    assert item.domain == "example-project.test"
    assert item.reachable is True


def test_submit_accepts_a_500_char_multiline_description(store: InMemoryProjectStore) -> None:
    """2026-09-08: description was bumped from a 200-char one-liner to 500 chars, multi-line/markdown allowed."""
    long_description = (
        "A multi-paragraph description with a [link](https://example.test).\n\n" * 6
    )[:500]
    resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(description=long_description))
    )
    assert resp["ok"] is True
    item = store.get(resp["submission_id"])
    assert item is not None
    assert item.description == long_description.strip()
    assert "\n" in item.description


def test_submit_rejects_description_over_500_chars() -> None:
    """The msgspec schema's own max_length=500 rejects an over-length body before submission_service even runs."""
    resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body(description="x" * 501)))
    assert resp.status_code == 400


def test_submit_stores_the_category_suggestion(store: InMemoryProjectStore) -> None:
    """2026-09-08: a free-text category suggestion is stored admin-side, never validated against CATEGORIES."""
    resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(category="other", category_suggestion="Prediction markets"))
    )
    assert resp["ok"] is True
    item = store.get(resp["submission_id"])
    assert item is not None
    assert item.category_suggestion == "Prediction markets"
    assert item.category == "other"


def test_submit_without_a_category_suggestion_defaults_to_empty(
    store: InMemoryProjectStore,
) -> None:
    """No category_suggestion in the request body -- stored as empty, not None or missing."""
    resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    item = store.get(resp["submission_id"])
    assert item is not None
    assert item.category_suggestion == ""


def test_category_suggestion_never_reaches_the_public_read() -> None:
    """The admin-only field must never leak into a public serve, only the admin JSON."""
    submit_resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(category_suggestion="Prediction markets"))
    )
    slug = submit_resp["submission_id"]
    review_service.approve(slug, wallet="ADMINWALLET")

    public = ecosystem_routes.ecosystem_detail(_request(method="GET", path_params={"slug": slug}))
    assert isinstance(public, dict)
    assert "category_suggestion" not in public


def test_submit_honeypot_drops_silently(store: InMemoryProjectStore) -> None:
    """A filled honeypot field answers success but stores nothing."""
    resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(website="http://bot.example"))
    )
    assert resp["ok"] is True
    assert resp["submission_id"] == ""
    assert store.list_by_submission_status(STATUS_PENDING, limit=10) == []


def test_submit_rate_limited_after_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hourly per-IP submit budget is enforced with a 429 once exceeded."""
    monkeypatch.setattr("app.core.config.settings.ecosystem_submit_rate_limit_per_hour", 2)
    for n in range(2):
        resp = ecosystem_routes.ecosystem_submit(
            _request(body=_submit_body(url=f"https://distinct-{n}.example/"))
        )
        assert resp["ok"] is True
    denied = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(url="https://distinct-3.example/"))
    )
    assert denied.status_code == 429


def test_submit_duplicate_domain_rejected(store: InMemoryProjectStore) -> None:
    """A second submission for an already-claimed domain is refused, even under a different name."""
    first = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    assert first["ok"] is True
    second = ecosystem_routes.ecosystem_submit(_request(body=_submit_body(name="A Different Name")))
    assert second.status_code == 409
    assert len(store.list_by_submission_status(STATUS_PENDING, limit=10)) == 1


def test_submit_unreachable_url_rejected(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryProjectStore
) -> None:
    """A submission whose url fails the submit-time liveness check is refused and stores nothing."""
    monkeypatch.setattr(
        "app.modules.ecosystem.services.liveness.check_target",
        lambda url, **_kw: UptimeResult(
            final_url=url,
            reachable=False,
            http_status=0,
            response_time_ms=5000,
            resolved_ip="",
            error="timeout",
        ),
    )
    resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    assert resp.status_code == 422
    assert store.list_by_submission_status(STATUS_PENDING, limit=10) == []


def test_submit_5xx_counts_as_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design doc section 3.1: '< 500 counts as alive' -- a 500 response itself does not."""
    monkeypatch.setattr(
        "app.modules.ecosystem.services.liveness.check_target",
        lambda url, **_kw: UptimeResult(
            final_url=url,
            reachable=True,
            http_status=503,
            response_time_ms=10,
            resolved_ip="1.2.3.4",
            error="",
        ),
    )
    resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    assert resp.status_code == 422


def test_submit_previously_rejected_domain_refused(
    monkeypatch: pytest.MonkeyPatch, store: InMemoryProjectStore
) -> None:
    """A domain the crawler admin already dead-ended is refused, mirroring the crawler's own sovereignty rule."""
    monkeypatch.setattr(submission_service, "_admin_previously_rejected", lambda _domain: True)
    resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    assert resp.status_code == 403
    assert store.list_by_submission_status(STATUS_PENDING, limit=10) == []


def test_submit_rejects_embedded_html_in_description() -> None:
    """A description containing embedded HTML is rejected, not stripped."""
    resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(description="Great project <script>alert(1)</script> tool"))
    )
    assert resp.status_code == 400


def test_submit_rejects_social_host_as_url() -> None:
    """A submission whose url points at a social/badge host (not the project's own site) is rejected."""
    resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(url="https://twitter.com/someproject"))
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Admin review: gate enforcement, approve/reject, projections
# --------------------------------------------------------------------------- #
def _admin_body(**overrides: Any) -> bytes:  # noqa: ANN401 -- passthrough JSON payload overrides, any JSON-serializable value
    """A valid approve-decision payload, JSON-encoded, with any field overridden."""
    payload = {"decision": "approve", **overrides}
    return serialization.dumps(payload).encode()


def test_admin_decision_requires_admin_wallet() -> None:
    """The admin decision route is refused before doing anything else when require_admin_wallet denies."""
    with patch.object(ecosystem_routes, "require_admin_wallet") as denied:
        from app.core.http_errors import json_error_response

        denied.return_value = json_error_response(401, "unauthorized", "nope")
        resp = ecosystem_routes.admin_ecosystem_decision(
            _request(body=_admin_body(), path_params={"slug": "does-not-matter"})
        )
    assert resp.status_code == 401


def test_admin_approve_writes_category_projection(store: InMemoryProjectStore) -> None:
    """Approving a pending entry moves it into the category (and "all") projections and makes it publicly readable."""
    submit_resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    slug = submit_resp["submission_id"]

    with (
        patch.object(ecosystem_routes, "require_admin_wallet", return_value=None),
        patch.object(ecosystem_routes, "verified_admin_wallet", return_value="ADMINWALLET"),
    ):
        resp = ecosystem_routes.admin_ecosystem_decision(
            _request(body=_admin_body(), path_params={"slug": slug})
        )
    assert resp["status"] == STATUS_APPROVED
    assert resp["reviewed_by"] == "ADMINWALLET"

    approved = store.get(slug)
    assert approved is not None
    assert approved.status == STATUS_APPROVED
    assert store.list_by_category("devtools", limit=10)[0].slug == slug
    from algorand_shared.ecosystem_statements import ALL_CATEGORY_PARTITION

    assert store.list_by_category(ALL_CATEGORY_PARTITION, limit=10)[0].slug == slug

    # A public detail read now serves it; a pending one wouldn't have.
    detail = ecosystem_routes.ecosystem_detail(_request(method="GET", path_params={"slug": slug}))
    assert detail["slug"] == slug
    assert "contact" not in detail  # never rendered publicly


def test_admin_reject_removes_from_pending_and_never_public(store: InMemoryProjectStore) -> None:
    """Rejecting a pending entry moves it out of the pending queue and keeps it out of public reads."""
    submit_resp = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))
    slug = submit_resp["submission_id"]

    with (
        patch.object(ecosystem_routes, "require_admin_wallet", return_value=None),
        patch.object(ecosystem_routes, "verified_admin_wallet", return_value="ADMINWALLET"),
    ):
        resp = ecosystem_routes.admin_ecosystem_decision(
            _request(
                body=_admin_body(decision="reject", reason="low_quality"),
                path_params={"slug": slug},
            )
        )
    assert resp["status"] == STATUS_REJECTED
    assert resp["reject_reason"] == "low_quality"
    assert store.list_by_submission_status(STATUS_PENDING, limit=10) == []

    detail = ecosystem_routes.ecosystem_detail(_request(method="GET", path_params={"slug": slug}))
    assert detail.status_code == 404


def test_admin_reject_invalid_reason_rejected() -> None:
    """A reject reason outside the closed list is refused."""
    with pytest.raises(EcosystemError):
        review_service.reject("whatever", wallet="ADMIN", reason="not_a_real_reason")


def test_admin_edit_then_reject_then_reapprove_moves_projection(
    store: InMemoryProjectStore,
) -> None:
    """An entry approved under one category, edited into another while still approved, keeps exactly one live projection row."""
    submit_resp = ecosystem_routes.ecosystem_submit(
        _request(body=_submit_body(category="devtools"))
    )
    slug = submit_resp["submission_id"]
    review_service.approve(slug, wallet="ADMIN")
    assert len(store.list_by_category("devtools", limit=10)) == 1

    review_service.update_entry(slug, category="analytics")
    assert store.list_by_category("devtools", limit=10) == []
    assert len(store.list_by_category("analytics", limit=10)) == 1


# --------------------------------------------------------------------------- #
# Public reads never leak pending/rejected entries
# --------------------------------------------------------------------------- #
def test_list_only_serves_approved() -> None:
    """A rejected entry never appears in the public unfiltered list."""
    pending = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))["submission_id"]
    review_service.reject(pending, wallet="ADMIN", reason="other")

    resp = ecosystem_routes.ecosystem_list(_request(method="GET"))
    assert resp["items"] == []


def test_submission_status_lookup_reports_rejection_reason() -> None:
    """The submitter's own status lookup reports the reject reason (there is no outbound email)."""
    slug = ecosystem_routes.ecosystem_submit(_request(body=_submit_body()))["submission_id"]
    review_service.reject(slug, wallet="ADMIN", reason="spam_or_scam")

    resp = ecosystem_routes.ecosystem_submission_status(
        _request(method="GET", path_params={"id": slug})
    )
    assert resp["status"] == "rejected"
    assert resp["reason"] == "spam_or_scam"


# --------------------------------------------------------------------------- #
# Field validators (unit-level)
# --------------------------------------------------------------------------- #
def test_domain_key_github_repo_keys_on_org_and_repo() -> None:
    """A github.com repo url keys on host+org+repo so distinct repos never collide as one domain."""
    assert submission_service.domain_key("https://github.com/foo/bar") == "github.com/foo/bar"
    assert (
        submission_service.domain_key("https://github.com/foo/bar/tree/main")
        == "github.com/foo/bar"
    )


def test_domain_key_github_io_project_page_keys_on_first_segment() -> None:
    """A *.github.io project page keys on host+first path segment."""
    assert (
        submission_service.domain_key("https://someuser.github.io/someproject/")
        == "someuser.github.io/someproject"
    )


def test_validate_tags_bounds_count_and_length() -> None:
    """More than MAX_TAGS tags is refused."""
    with pytest.raises(EcosystemError):
        submission_service.validate_tags(["a", "b", "c", "d", "e", "f"])


def test_decode_ecosystem_submit_request_defaults() -> None:
    """Optional submit fields (website honeypot, contact) default to empty."""
    payload = serialization.decode(_submit_body(), EcosystemSubmitRequest)
    assert payload.website == ""
    assert payload.contact == ""


def test_decode_ecosystem_decision_request_literal() -> None:
    """An unrecognized `decision` value fails to decode."""
    with pytest.raises(serialization.DecodeError):
        serialization.decode(b'{"decision": "maybe"}', EcosystemDecisionRequest)
