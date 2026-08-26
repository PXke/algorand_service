"""gray_zone_reconciliation.py: the one-time reconciliation pass for the 2026-08-26 audit (665 domains sitting at frontier_status="approved" with a content_relevance verdict inside the genuine [FRONTIER_CONTENT_REJECT_SCORE, FRONTIER_CONTENT_PROMOTE_SCORE) gray zone). find_gray_zone_domains is pure reporting; dispatch_gray_zone_deep_classify is the one function that writes/dispatches, and every test here is about its conservative small-batch, dry-run-by-default, dedup-on-repeat-call boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.crawler.gray_zone_reconciliation import (
    dispatch_gray_zone_deep_classify,
    find_gray_zone_domains,
    find_stale_gray_zone_dispatches,
)


def _row(
    domain: str,
    *,
    frontier_status: str = "approved",
    is_relevant: bool | None = True,
    metadata: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        domain=domain,
        frontier_status=frontier_status,
        is_relevant=is_relevant,
        metadata=metadata or {},
    )


def _fake_session(rows: list[SimpleNamespace], executed: list | None = None) -> SimpleNamespace:
    def _execute(_stmt: object, params: tuple | None = None) -> list[SimpleNamespace]:
        if executed is not None and params is not None:
            executed.append(params)
        return rows

    return SimpleNamespace(execute=_execute, prepare=lambda cql: cql)


# --------------------------------------------------------------------------- #
# find_gray_zone_domains
# --------------------------------------------------------------------------- #


def test_find_gray_zone_domains_matches_the_exact_audit_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only frontier_status="approved" domains with content_relevance inside [REJECT, PROMOTE) count -- everything else (pending, dead_end, out-of-range score, unscored, disabled-by-is_relevant) must be excluded."""
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2)
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_PROMOTE_SCORE", 0.5)
    rows = [
        _row("grayzone-low.example", metadata={"content_relevance": "0.2"}),  # inclusive low bound
        _row("grayzone-mid.example", metadata={"content_relevance": "0.35"}),
        _row("grayzone-high.example", metadata={"content_relevance": "0.499"}),
        _row("at-promote.example", metadata={"content_relevance": "0.5"}),  # exclusive upper bound
        _row("below-reject.example", metadata={"content_relevance": "0.1"}),
        _row(
            "still-pending.example",
            frontier_status="pending",
            metadata={"content_relevance": "0.3"},
        ),
        _row("dead-end.example", frontier_status="dead_end", metadata={"content_relevance": "0.3"}),
        _row("unscored.example", metadata={}),
        _row("bad-score.example", metadata={"content_relevance": "not-a-number"}),
        _row("marked-irrelevant.example", is_relevant=False, metadata={"content_relevance": "0.3"}),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    findings = find_gray_zone_domains()

    domains = {f["domain"] for f in findings}
    assert domains == {"grayzone-low.example", "grayzone-mid.example", "grayzone-high.example"}


def test_find_gray_zone_domains_reports_seed_url_fallback_and_excludes_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pending_url wins over content_relevance_url when both are present; an already-`deep_classify_queued` domain is excluded entirely (2026-08-26 fix), not just flagged, since frontier_status is a real column this module never actually updates -- the queued flag is the only thing that reliably means "already in flight"."""
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2)
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_PROMOTE_SCORE", 0.5)
    rows = [
        _row(
            "has-pending-url.example",
            metadata={
                "content_relevance": "0.3",
                "pending_url": "https://has-pending-url.example/landing",
                "content_relevance_url": "https://has-pending-url.example/other",
            },
        ),
        _row(
            "only-relevance-url.example",
            metadata={
                "content_relevance": "0.3",
                "content_relevance_url": "https://only-relevance-url.example/page",
            },
        ),
        _row(
            "no-url-at-all.example",
            metadata={"content_relevance": "0.3"},
        ),
        _row(
            "already-queued.example",
            metadata={"content_relevance": "0.3", "deep_classify_queued": "true"},
        ),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    findings = {f["domain"]: f for f in find_gray_zone_domains()}

    assert (
        findings["has-pending-url.example"]["pending_url"]
        == "https://has-pending-url.example/landing"
    )
    assert (
        findings["only-relevance-url.example"]["pending_url"]
        == "https://only-relevance-url.example/page"
    )
    assert findings["no-url-at-all.example"]["pending_url"] == ""
    assert "already-queued.example" not in findings
    assert "deep_classify_queued" not in findings["has-pending-url.example"]


def test_find_gray_zone_domains_limit_trims_after_the_full_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Limit caps the RETURNED sample, not the underlying scan -- a small `limit` is exactly the "give me 10 examples" use case."""
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2)
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_PROMOTE_SCORE", 0.5)
    rows = [_row(f"gz-{i}.example", metadata={"content_relevance": "0.3"}) for i in range(20)]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    assert len(find_gray_zone_domains()) == 20
    assert len(find_gray_zone_domains(limit=10)) == 10


def test_find_gray_zone_domains_makes_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Purely read-only -- no execute call carries a write-shaped (metadata, domain) tuple, and no send_task must ever be reachable from this function."""
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2)
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_PROMOTE_SCORE", 0.5)
    rows = [_row("gz.example", metadata={"content_relevance": "0.3"})]
    executed: list = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows, executed)
    )
    sent = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task", lambda *a, **kw: sent.append((a, kw))
    )

    find_gray_zone_domains()

    # The read-only LIST scan passes a single-element (scan_limit,) params
    # tuple; a write would pass the 2-element (metadata, domain) shape used
    # by UPDATE_METADATA everywhere else in this module. None of that shape
    # may appear here.
    assert executed == [(5000,)]
    assert sent == []


# --------------------------------------------------------------------------- #
# dispatch_gray_zone_deep_classify
# --------------------------------------------------------------------------- #


def _patch_dispatch(
    monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace]
) -> tuple[list[tuple], list[tuple]]:
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2)
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_PROMOTE_SCORE", 0.5)
    monkeypatch.setattr("app.core.config.FRONTIER_DEEP_CLASSIFY_MAX_PAGES", 200)
    executed: list[tuple] = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: _fake_session(rows, executed),
    )
    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, kwargs=None, queue=None: sent.append((name, kwargs, queue)),
    )
    return executed, sent


def test_dispatch_dry_run_default_makes_no_writes_and_no_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run defaults True -- reports the would-be batch without ever writing to Cassandra or calling send_task, mirroring classify_pending_domains' own safe-by-default convention."""
    rows = [_row(f"gz-{i}.example", metadata={"content_relevance": "0.3"}) for i in range(3)]
    executed, sent = _patch_dispatch(monkeypatch, rows)

    result = dispatch_gray_zone_deep_classify(limit=5)

    assert result["dry_run"] is True
    assert result["dispatched_count"] == 3
    assert {d["domain"] for d in result["dispatched"]} == {f"gz-{i}.example" for i in range(3)}
    # Only the read-only LIST scan (a 1-element params tuple) ran -- no
    # 2-element (metadata, domain) write shape anywhere.
    assert all(len(p) == 1 for p in executed)
    assert sent == []


def test_dispatch_real_run_writes_metadata_and_sends_the_real_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=False writes deep_classify_queued=true (merged with existing metadata, nothing dropped) BEFORE calling send_task against the real deep_classify_domain task on the scrape queue. Does NOT write frontier_status into metadata (2026-08-26 fix: that column is never read from there, so the write was inert -- see _gray_zone_rows' docstring)."""
    rows = [
        _row(
            "gz.example",
            metadata={
                "content_relevance": "0.35",
                "pending_url": "https://gz.example/landing",
                "some_other_field": "preserved",
            },
        )
    ]
    executed, sent = _patch_dispatch(monkeypatch, rows)

    result = dispatch_gray_zone_deep_classify(limit=5, dry_run=False)

    assert result["dry_run"] is False
    assert result["dispatched_count"] == 1
    writes = [p for p in executed if len(p) == 2]
    assert len(writes) == 1
    new_meta, domain = writes[0]
    assert domain == "gz.example"
    assert new_meta["deep_classify_queued"] == "true"
    assert "frontier_status" not in new_meta
    assert new_meta["some_other_field"] == "preserved"  # merge, not overwrite
    # 2026-08-26 staleness-detection fix: a timestamp is stamped alongside
    # the flag so find_stale_gray_zone_dispatches has something to judge
    # staleness by later -- must parse as a real ISO8601 datetime.
    from datetime import datetime

    datetime.fromisoformat(new_meta["deep_classify_queued_at"])

    assert len(sent) == 1
    name, kwargs, queue = sent[0]
    assert name == "app.tasks.crawler.deep_classify_domain"
    assert kwargs == {
        "domain": "gz.example",
        "seed_url": "https://gz.example/landing",
        "max_pages": 200,
    }
    assert queue == "scrape"


def test_dispatch_never_exceeds_the_small_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backlog far larger than `limit` still only ever dispatches `limit` domains per call -- the entire point of this function, given every dispatch is a real crawl."""
    rows = [_row(f"gz-{i:03d}.example", metadata={"content_relevance": "0.3"}) for i in range(50)]
    executed, sent = _patch_dispatch(monkeypatch, rows)

    result = dispatch_gray_zone_deep_classify(limit=5, dry_run=False)

    assert result["dispatched_count"] == 5
    assert len(sent) == 5
    writes = [p for p in executed if len(p) == 2]
    assert len(writes) == 5
    assert result["remaining_candidates"] == 45
    # Domain-sorted, so the batch is deterministic and it's always the FIRST
    # (alphabetically) 5 that go out on this call.
    assert [d["domain"] for d in result["dispatched"]] == [f"gz-{i:03d}.example" for i in range(5)]


def test_dispatch_skips_domains_already_queued_for_deep_classify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A domain already mid-escalation (deep_classify_queued=="true", whether queued by this function or by an ordinary classify_pending_domains run) must never be double-dispatched."""
    rows = [
        _row(
            "already-queued.example",
            metadata={"content_relevance": "0.3", "deep_classify_queued": "true"},
        ),
        _row("fresh.example", metadata={"content_relevance": "0.3"}),
    ]
    _executed, sent = _patch_dispatch(monkeypatch, rows)

    result = dispatch_gray_zone_deep_classify(limit=5, dry_run=False)

    assert result["dispatched_count"] == 1
    assert result["dispatched"][0]["domain"] == "fresh.example"
    assert len(sent) == 1
    assert sent[0][1]["domain"] == "fresh.example"


def test_dispatch_then_rescan_no_longer_shows_the_dispatched_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end regression for the 2026-08-26 fix: applying the REAL metadata write dispatch produces (not a synthetic one) to the row, then calling find_gray_zone_domains again against that updated row excludes it -- proving the backlog actually shrinks across repeat calls, not just that the write happens."""
    row = _row("gz.example", metadata={"content_relevance": "0.35"})
    executed, _sent = _patch_dispatch(monkeypatch, [row])

    # Confirm it starts out visible to the read-only report (same session
    # _patch_dispatch already wired -- re-monkeypatching here would swap in a
    # session that doesn't track `executed`, silently breaking the write
    # capture below).
    assert [f["domain"] for f in find_gray_zone_domains()] == ["gz.example"]

    result = dispatch_gray_zone_deep_classify(limit=5, dry_run=False)
    assert result["dispatched_count"] == 1

    # Apply the actual write dispatch produced (not a hand-rolled one) to the
    # row, the way a real Cassandra UPDATE would -- this is the crux of the
    # regression: the write must be one that _gray_zone_rows' own read path
    # actually reacts to.
    new_meta, domain = next(p for p in executed if len(p) == 2)
    assert domain == "gz.example"
    row.metadata = new_meta

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session([row]))
    assert find_gray_zone_domains() == []


def test_dispatch_falls_back_through_content_relevance_url_then_bare_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """seed_url tries pending_url, then content_relevance_url, then a bare https guess -- same fallback order classify_pending_domains itself uses for `url`."""
    rows = [
        _row(
            "only-relevance-url.example",
            metadata={
                "content_relevance": "0.3",
                "content_relevance_url": "https://only-relevance-url.example/found",
            },
        ),
        _row("no-url-at-all.example", metadata={"content_relevance": "0.3"}),
    ]
    _executed, sent = _patch_dispatch(monkeypatch, rows)

    dispatch_gray_zone_deep_classify(limit=5, dry_run=False)

    seed_urls = {kwargs["domain"]: kwargs["seed_url"] for _name, kwargs, _q in sent}
    assert seed_urls["only-relevance-url.example"] == "https://only-relevance-url.example/found"
    assert seed_urls["no-url-at-all.example"] == "https://no-url-at-all.example"


def test_dispatch_is_a_noop_when_the_backlog_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No gray-zone domains at all -- nothing dispatched, nothing written."""
    executed, sent = _patch_dispatch(monkeypatch, [])

    result = dispatch_gray_zone_deep_classify(limit=5, dry_run=False)

    assert result == {
        "dry_run": False,
        "dispatched": [],
        "dispatched_count": 0,
        "remaining_candidates": 0,
    }
    assert all(len(p) == 1 for p in executed)  # only the read-only scan ran
    assert sent == []


# --------------------------------------------------------------------------- #
# find_stale_gray_zone_dispatches
# --------------------------------------------------------------------------- #


def _iso_ago(seconds: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(tz=UTC) - timedelta(seconds=seconds)).isoformat()


def test_find_stale_gray_zone_dispatches_flags_an_old_still_approved_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued long ago, still frontier_status="approved", flag still set -- exactly the "task never ran to completion" gap this function exists to surface."""
    rows = [
        _row(
            "dropped.example",
            metadata={
                "content_relevance": "0.35",
                "deep_classify_queued": "true",
                "deep_classify_queued_at": _iso_ago(10_000),
            },
        )
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    findings = find_stale_gray_zone_dispatches(staleness_seconds=7200)

    assert [f["domain"] for f in findings] == ["dropped.example"]
    assert findings[0]["age_seconds"] >= 10_000


def test_find_stale_gray_zone_dispatches_ignores_a_recent_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued recently, under the staleness threshold -- still plausibly in flight, not a finding yet."""
    rows = [
        _row(
            "just-dispatched.example",
            metadata={
                "content_relevance": "0.35",
                "deep_classify_queued": "true",
                "deep_classify_queued_at": _iso_ago(30),
            },
        )
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    assert find_stale_gray_zone_dispatches(staleness_seconds=7200) == []


def test_find_stale_gray_zone_dispatches_ignores_a_domain_that_already_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """frontier_status has moved off "approved" (e.g. deep_classify_domain rejected it to dead_end) even though the flag and an old timestamp are both still sitting in metadata -- update_domain_status merges metadata and never clears the flag on completion, so a stuck flag alone must NOT be treated as stale; only frontier_status staying "approved" means the task never actually finished."""
    rows = [
        _row(
            "resolved-dead-end.example",
            frontier_status="dead_end",
            metadata={
                "deep_classify_queued": "true",
                "deep_classify_queued_at": _iso_ago(10_000),
                "deep_classified": "true",
            },
        ),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    assert find_stale_gray_zone_dispatches(staleness_seconds=7200) == []


def test_find_stale_gray_zone_dispatches_ignores_unqueued_or_unflagged_domains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still approved and old is not enough on its own -- the flag must actually be set, or this isn't a gray-zone dispatch at all."""
    rows = [
        _row("never-dispatched.example", metadata={"content_relevance": "0.35"}),
        _row(
            "flag-cleared-somehow.example",
            metadata={
                "deep_classify_queued": "false",
                "deep_classify_queued_at": _iso_ago(10_000),
            },
        ),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    assert find_stale_gray_zone_dispatches(staleness_seconds=7200) == []


def test_find_stale_gray_zone_dispatches_skips_missing_or_malformed_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows that predate the deep_classify_queued_at field (or carry a corrupt value) must be skipped, not crash the whole scan -- there is no way to judge staleness without a real timestamp."""
    rows = [
        _row(
            "no-timestamp.example",
            metadata={"deep_classify_queued": "true"},
        ),
        _row(
            "garbage-timestamp.example",
            metadata={"deep_classify_queued": "true", "deep_classify_queued_at": "not-a-date"},
        ),
        _row(
            "genuinely-stale.example",
            metadata={
                "deep_classify_queued": "true",
                "deep_classify_queued_at": _iso_ago(10_000),
            },
        ),
    ]
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows))

    findings = find_stale_gray_zone_dispatches(staleness_seconds=7200)

    assert [f["domain"] for f in findings] == ["genuinely-stale.example"]


def test_find_stale_gray_zone_dispatches_makes_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Purely read-only, mirroring find_gray_zone_domains -- no write-shaped execute call, no Celery dispatch."""
    rows = [
        _row(
            "stale.example",
            metadata={
                "deep_classify_queued": "true",
                "deep_classify_queued_at": _iso_ago(10_000),
            },
        )
    ]
    executed: list = []
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: _fake_session(rows, executed)
    )
    sent = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task", lambda *a, **kw: sent.append((a, kw))
    )

    find_stale_gray_zone_dispatches(staleness_seconds=7200)

    assert executed == [(5000,)]
    assert sent == []
