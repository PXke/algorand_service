"""translation_backfill.py: the fast-path DeepSeek-only backfill for the 2026-08-26 88-of-115-articles missing-translation gap. find_deepseek_translation_gaps is pure reporting; dispatch_deepseek_translation_backfill is the one function that dispatches, and every test here is about its dry-run-by-default, small-limit-per-call, off-peak-gated-on-real-dispatch boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.newspaper.translation_backfill import (
    dispatch_deepseek_translation_backfill,
    find_deepseek_translation_gaps,
)


def _row(article_id: str, *, translations: dict[str, str] | None = None) -> SimpleNamespace:
    """A fake list_feed_articles row -- carries `translated_titles`, the lightweight companion the feed listing now selects instead of the full `translations` map (migration 087); `translations` here is only the KEY SET a caller cares about (what languages exist), so reusing it as the fake's translated_titles value is faithful to what find_deepseek_translation_gaps actually reads."""
    return SimpleNamespace(article_id=article_id, translated_titles=translations or {})


# --------------------------------------------------------------------------- #
# find_deepseek_translation_gaps
# --------------------------------------------------------------------------- #


def test_find_gaps_reports_only_missing_deepseek_routed_langs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only languages in DEEPSEEK_TRANSLATE_LANGS are ever reported missing -- a language NOT routed to DeepSeek (e.g. narrowed back to local via env) is never this module's problem to report."""
    monkeypatch.setattr("app.core.config.DEEPSEEK_TRANSLATE_LANGS", frozenset({"ar", "fa", "ru"}))
    rows = [
        _row("art-1", translations={"ar": "{}"}),  # missing fa, ru
        _row("art-2", translations={"ar": "{}", "fa": "{}", "ru": "{}"}),  # fully covered
        _row("art-3", translations={}),  # missing all three
    ]
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles",
        lambda *, limit: rows,  # noqa: ARG005 -- fixture stub, limit intentionally ignored
    )

    findings = {f["article_id"]: f["missing_langs"] for f in find_deepseek_translation_gaps()}

    assert findings["art-1"] == ["fa", "ru"]
    assert "art-2" not in findings
    assert findings["art-3"] == ["ar", "fa", "ru"]


def test_find_gaps_limit_trims_after_the_full_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Limit caps the RETURNED sample, not the underlying scan."""
    monkeypatch.setattr("app.core.config.DEEPSEEK_TRANSLATE_LANGS", frozenset({"ar"}))
    rows = [_row(f"art-{i:03d}") for i in range(20)]
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles",
        lambda *, limit: rows,  # noqa: ARG005 -- fixture stub, limit intentionally ignored
    )

    assert len(find_deepseek_translation_gaps()) == 20
    assert len(find_deepseek_translation_gaps(limit=5)) == 5


def test_find_gaps_makes_no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Purely read-only -- send_task must never be reachable from this function."""
    monkeypatch.setattr("app.core.config.DEEPSEEK_TRANSLATE_LANGS", frozenset({"ar"}))
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles",
        lambda *, limit: [_row("art-1")],  # noqa: ARG005 -- fixture stub, limit intentionally ignored
    )
    sent = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task", lambda *a, **kw: sent.append((a, kw))
    )

    find_deepseek_translation_gaps()

    assert sent == []


# --------------------------------------------------------------------------- #
# dispatch_deepseek_translation_backfill
# --------------------------------------------------------------------------- #


def _patch_dispatch(
    monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace], *, off_peak: bool = True
) -> list[tuple]:
    monkeypatch.setattr("app.core.config.DEEPSEEK_TRANSLATE_LANGS", frozenset({"ar", "fa"}))
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.list_feed_articles",
        lambda *, limit: rows,  # noqa: ARG005 -- fixture stub, limit intentionally ignored
    )
    monkeypatch.setattr("app.modules.newspaper.peak_hours.is_off_peak_now", lambda: off_peak)
    monkeypatch.setattr("app.modules.newspaper.peak_hours.next_off_peak_at", lambda: None)
    sent: list[tuple] = []
    monkeypatch.setattr(
        "app.celery_app.celery_app.send_task",
        lambda name, args=None, queue=None: sent.append((name, args, queue)),
    )
    return sent


def test_dispatch_dry_run_default_makes_no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """dry_run defaults True -- reports the would-be batch without ever calling send_task, mirroring dispatch_gray_zone_deep_classify's own safe-by-default convention. A dry-run must work even during peak hours (preview is never blocked)."""
    rows = [_row(f"art-{i}") for i in range(3)]
    sent = _patch_dispatch(monkeypatch, rows, off_peak=False)

    result = dispatch_deepseek_translation_backfill(limit=5)

    assert result["dry_run"] is True
    assert result["status"] == "ok"
    assert result["dispatched_count"] == 3
    assert sent == []


def test_dispatch_real_run_routes_to_pipeline_queue_off_peak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real dispatch, off-peak, calls send_task against the EXISTING translate_article_batch_task, explicitly overriding the queue to "pipeline" (the concurrency=4 worker) rather than letting it fall through to the dedicated single-language-at-a-time "translate" queue."""
    rows = [_row("art-1", translations={"ar": "{}"})]  # missing "fa" only
    sent = _patch_dispatch(monkeypatch, rows, off_peak=True)

    result = dispatch_deepseek_translation_backfill(limit=5, dry_run=False)

    assert result["dry_run"] is False
    assert result["status"] == "ok"
    assert result["dispatched_count"] == 1
    assert len(sent) == 1
    name, args, queue = sent[0]
    assert name == "app.tasks.newspaper.translate_article_batch"
    assert args == ["art-1", ["fa"]]
    assert queue == "pipeline"


def test_dispatch_real_run_skips_entirely_during_peak_hours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real dispatch during peak hours fires NOTHING (not even a doomed-to-defer task) and reports skipped_peak_hours -- each dispatched translate_article_batch_task would just self-defer anyway, so firing them is pure churn."""
    rows = [_row(f"art-{i}") for i in range(3)]
    sent = _patch_dispatch(monkeypatch, rows, off_peak=False)

    result = dispatch_deepseek_translation_backfill(limit=5, dry_run=False)

    assert result["status"] == "skipped_peak_hours"
    assert result["dispatched_count"] == 0
    assert result["remaining_candidates"] == 3
    assert sent == []


def test_dispatch_never_exceeds_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backlog far larger than `limit` still only ever dispatches `limit` articles per call."""
    rows = [_row(f"art-{i:03d}") for i in range(50)]
    sent = _patch_dispatch(monkeypatch, rows, off_peak=True)

    result = dispatch_deepseek_translation_backfill(limit=5, dry_run=False)

    assert result["dispatched_count"] == 5
    assert len(sent) == 5
    assert result["remaining_candidates"] == 45
    # Sorted by article_id, so the batch is deterministic.
    assert [a[0] for _n, a, _q in sent] == [f"art-{i:03d}" for i in range(5)]


def test_dispatch_is_a_noop_when_the_backlog_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No gaps at all -- nothing dispatched."""
    sent = _patch_dispatch(monkeypatch, [], off_peak=True)

    result = dispatch_deepseek_translation_backfill(limit=5, dry_run=False)

    assert result["dispatched_count"] == 0
    assert result["remaining_candidates"] == 0
    assert sent == []
