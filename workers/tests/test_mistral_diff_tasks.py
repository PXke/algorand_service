"""The diff-check sweep skips cleanly when Mistral is off or a review/backlog gate is active."""

from __future__ import annotations

import pytest

from app.core.config import mistral_configured
from app.modules.chain_tail.registry_cache import ServiceEntry
from app.modules.newspaper import mistral_diff_check


def test_run_skips_when_mistral_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the whole sweep with 0 checked when Mistral isn't enabled/configured."""
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    result = mistral_diff_check.run_mistral_diff_check()
    assert result["status"] == "skipped"
    assert result["checked"] == 0


def test_run_skips_when_classifier_review_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the sweep when a classifier review is already pending."""
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "test-key")

    result = mistral_diff_check.run_mistral_diff_check(
        has_pending_classifier_review=lambda: True,
        has_pending_feed_release=lambda: False,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "classifier_review_pending"
    assert result["checked"] == 0


def test_run_skips_on_feed_backlog_only_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the sweep on a pending feed-release backlog only when pause_on_feed_backlog is opted in."""
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "test-key")

    result = mistral_diff_check.run_mistral_diff_check(
        load_services=lambda: (),
        clear_cache=lambda: None,
        has_pending_classifier_review=lambda: False,
        has_pending_feed_release=lambda: True,
        pause_on_feed_backlog=True,
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "approved_feed_pending_release"
    assert result["checked"] == 0


def test_feed_backlog_does_not_pause_intake_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """By default a pending feed-release backlog does not pause intake."""
    # Default behaviour: a pending feed-release backlog must NOT stop intake.
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "test-key")
    monkeypatch.setattr(config, "PAUSE_INTAKE_ON_FEED_BACKLOG", False)

    result = mistral_diff_check.run_mistral_diff_check(
        load_services=lambda: (),
        clear_cache=lambda: None,
        has_pending_classifier_review=lambda: False,
        has_pending_feed_release=lambda: True,  # backlog present, but ignored
    )
    assert result["status"] == "ok"
    assert result["checked"] == 0


def test_run_polls_all_scrape_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    """Polls every registered scrape source and publishes an unchanged outcome for each."""
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "test-key")
    assert mistral_configured()

    entries = (
        ServiceEntry(
            service_id="web-1",
            display_name="Web",
            match_kind="address",
            match_value="X",
            scrape_url="https://example.com",
            enabled=True,
        ),
    )

    outcomes: list[dict] = []

    def fake_publish(**kwargs: object) -> dict:
        outcomes.append(kwargs)
        return {"status": "unchanged", "txid": kwargs["txid"]}

    result = mistral_diff_check.run_mistral_diff_check(
        publish=fake_publish,
        load_services=lambda: entries,
        clear_cache=lambda: None,
        has_pending_classifier_review=lambda: False,
        has_pending_feed_release=lambda: False,
        is_throttled=lambda _sid: False,
        record_scrape=lambda _sid, ok=True: None,  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    assert result["status"] == "ok"
    assert result["checked"] == 1
    assert result["throttled"] == 0
    assert result["unchanged"] == 1
    assert outcomes[0]["mistral_only"] is True
    assert outcomes[0]["service_id"] == "web-1"
