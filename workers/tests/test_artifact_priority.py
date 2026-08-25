"""Editorial-room artifact priority scoring (2026-08-25, SHADOW MODE): the three v1 SCORE_COMPONENTS and the sweep that applies them.

Uses the shared `fake_artifact_session` fixture (conftest.py) so the sweep
integration test exercises the real Cassandra-shaped round trip, not just
the pure scoring functions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeArtifactSession

# --------------------------------------------------------------------------- #
# word_count_score
# --------------------------------------------------------------------------- #


def test_word_count_score_zero_for_empty_content() -> None:
    """Empty (or whitespace-only) content scores zero."""
    from app.modules.newspaper.artifact_priority import word_count_score

    assert word_count_score("") == 0.0
    assert word_count_score("   ") == 0.0


def test_word_count_score_increases_with_more_words() -> None:
    """More substantial content scores higher."""
    from app.modules.newspaper.artifact_priority import word_count_score

    short = word_count_score(" ".join(["word"] * 50))
    medium = word_count_score(" ".join(["word"] * 300))
    long_ = word_count_score(" ".join(["word"] * 1200))
    assert 0.0 < short < medium < long_


def test_word_count_score_diminishing_returns_not_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doubling word count must not double the score (diminishing returns, per the sqrt curve) -- going from 300 to 600 words gains less than going from 0 to 300 did."""
    from app.modules.newspaper.artifact_priority import word_count_score

    monkeypatch.setattr("app.core.config.ARTIFACT_WORD_COUNT_CAP", 1200)
    s300 = word_count_score(" ".join(["w"] * 300))
    s600 = word_count_score(" ".join(["w"] * 600))
    first_gain = s300 - 0.0
    second_gain = s600 - s300
    assert second_gain < first_gain


def test_word_count_score_caps_past_the_configured_word_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A huge wall of text past the cap must not keep buying more priority purely on size."""
    from app.modules.newspaper.artifact_priority import word_count_score

    monkeypatch.setattr("app.core.config.ARTIFACT_WORD_COUNT_CAP", 500)
    monkeypatch.setattr("app.core.config.ARTIFACT_WORD_COUNT_MAX_SCORE", 10.0)
    at_cap = word_count_score(" ".join(["w"] * 500))
    way_over = word_count_score(" ".join(["w"] * 50000))
    assert at_cap == 10.0
    assert way_over == 10.0


# --------------------------------------------------------------------------- #
# timeliness_score
# --------------------------------------------------------------------------- #


def test_timeliness_score_max_at_zero_age() -> None:
    """An artifact whose event just happened scores the configured max."""
    from app.modules.newspaper.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    assert timeliness_score(now, now, today=now) == pytest.approx(10.0)


def test_timeliness_score_decays_smoothly_as_it_ages() -> None:
    """Strictly decreasing with age -- no cliff, no plateau."""
    from app.modules.newspaper.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    scores = [
        timeliness_score(now - timedelta(days=d), now, today=now) for d in (0, 5, 10, 30, 90, 365)
    ]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)  # every step strictly lower


def test_timeliness_score_never_hits_a_hard_floor_of_zero() -> None:
    """Explicit owner instruction: old-but-real content must stay theoretically reachable ('except when we have nothing else to report') -- even a 100-year-old artifact scores strictly above zero."""
    from app.modules.newspaper.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    ancient = timeliness_score(now - timedelta(days=365 * 100), now, today=now)
    assert ancient > 0.0


def test_timeliness_score_never_drops_below_configured_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """However old the artifact, the score must never fall below the configured floor -- at astronomically large ages the exponential term rounds away to (but never legitimately below) that floor."""
    from app.core import config as cfg
    from app.modules.newspaper.artifact_priority import timeliness_score

    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_FLOOR", 2.0)
    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_MAX_SCORE", 10.0)
    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_HALF_LIFE_DAYS", 10.0)
    now = datetime(2026, 8, 25, tzinfo=UTC)
    very_old = timeliness_score(now - timedelta(days=100000), now, today=now)
    assert very_old >= 2.0
    assert very_old == pytest.approx(2.0, abs=0.01)

    # At a moderate age (well short of "astronomically large"), the decay
    # term is still visibly above the floor -- the curve genuinely approaches
    # it rather than cliff-dropping straight there.
    moderately_old = timeliness_score(now - timedelta(days=50), now, today=now)
    assert moderately_old > 2.0


def test_timeliness_score_half_life_is_the_true_midpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """At exactly one half-life, the score sits exactly halfway between max and floor."""
    from app.core import config as cfg
    from app.modules.newspaper.artifact_priority import timeliness_score

    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_FLOOR", 0.0)
    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_MAX_SCORE", 10.0)
    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_HALF_LIFE_DAYS", 21.0)
    now = datetime(2026, 8, 25, tzinfo=UTC)
    at_half_life = timeliness_score(now - timedelta(days=21), now, today=now)
    assert at_half_life == pytest.approx(5.0, abs=0.01)


def test_timeliness_score_falls_back_to_created_at_when_no_event_date() -> None:
    """No extractable event_date falls back to created_at as the freshness anchor, per the artifacts-table fallback rule."""
    from app.modules.newspaper.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    created_at = now - timedelta(days=10)
    with_none = timeliness_score(None, created_at, today=now)
    with_created_as_anchor = timeliness_score(created_at, created_at, today=now)
    assert with_none == with_created_as_anchor


# --------------------------------------------------------------------------- #
# ecosystem_listed_score
# --------------------------------------------------------------------------- #


def test_ecosystem_listed_score_zero_for_no_url() -> None:
    """No URL (a brief, a mail message) never earns the ecosystem-listed bonus."""
    from app.modules.newspaper.artifact_priority import ecosystem_listed_score

    assert ecosystem_listed_score(None) == 0.0
    assert ecosystem_listed_score("") == 0.0


def test_ecosystem_listed_score_boosts_a_directory_listed_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuses the SAME ecosystem_listed_domains() registry the crawler-discovery scorer uses for the identical chain-silent-but-important-service problem, rather than a second registry."""
    from app.core import config as cfg
    from app.modules.newspaper import artifact_priority

    monkeypatch.setattr(cfg, "ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains",
        lambda: frozenset({"hesabpay.com"}),
    )

    assert artifact_priority.ecosystem_listed_score("https://hesabpay.com/blog/post") == 5.0
    assert artifact_priority.ecosystem_listed_score("https://unrelated.example.com/") == 0.0


def test_ecosystem_listed_score_fails_open_to_zero_on_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra-unreachable registry lookup fails open to 0.0, never raises."""
    from app.modules.newspaper import artifact_priority

    def _boom() -> frozenset[str]:
        raise RuntimeError("cassandra unreachable")

    monkeypatch.setattr("app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", _boom)
    assert artifact_priority.ecosystem_listed_score("https://hesabpay.com/") == 0.0


# --------------------------------------------------------------------------- #
# compute_artifact_priority / SCORE_COMPONENTS architecture
# --------------------------------------------------------------------------- #


def test_compute_artifact_priority_sums_all_score_components(monkeypatch: pytest.MonkeyPatch) -> None:
    """Priority = sum(component(artifact, content) for component in SCORE_COMPONENTS) -- pins the additive architecture so a future 4th signal only needs to append to the tuple."""
    from app.modules.newspaper import artifact_priority

    fake_components = (lambda _a, _c: 1.0, lambda _a, _c: 2.0, lambda _a, _c: 3.0)
    monkeypatch.setattr(artifact_priority, "SCORE_COMPONENTS", fake_components)
    assert artifact_priority.compute_artifact_priority(object(), object()) == 6.0


# --------------------------------------------------------------------------- #
# sweep_artifact_priorities (full round trip through the fake Cassandra store)
# --------------------------------------------------------------------------- #


def test_sweep_updates_priority_for_every_pending_artifact(
    fake_artifact_session: FakeArtifactSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep recomputes and persists a real priority for each pending artifact, and a more substantial one outscores a near-empty one."""
    from app.modules.newspaper.artifact_priority import sweep_artifact_priorities
    from app.modules.newspaper.artifact_store import insert_artifact

    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )

    id_a, _ = insert_artifact(
        service_id="svc-a", url=None, channel="brief", content=" ".join(["word"] * 600)
    )
    id_b, _ = insert_artifact(service_id="svc-b", url=None, channel="brief", content="short")

    result = sweep_artifact_priorities()

    assert result["swept"] == 2
    assert fake_artifact_session.artifacts[id_a]["priority"] > 0.0
    assert fake_artifact_session.artifacts[id_b]["priority"] > 0.0
    # More substantial content must outscore a near-empty artifact once both
    # share the same timeliness/ecosystem terms (both fresh, neither listed).
    assert fake_artifact_session.artifacts[id_a]["priority"] > fake_artifact_session.artifacts[id_b]["priority"]


def test_sweep_never_touches_non_pending_artifacts(
    fake_artifact_session: FakeArtifactSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A composed/selected/discarded artifact's priority is left exactly as-is by the sweep -- only PENDING artifacts are ever touched."""
    from app.modules.newspaper.artifact_priority import sweep_artifact_priorities
    from app.modules.newspaper.artifact_store import COMPOSED, insert_artifact, mark_artifact_status

    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )

    composed_id, _ = insert_artifact(service_id="svc-c", url=None, channel="brief", content="x")
    mark_artifact_status(composed_id, COMPOSED)
    fake_artifact_session.artifacts[composed_id]["priority"] = -1.0  # sentinel, must be untouched

    result = sweep_artifact_priorities()

    assert result["swept"] == 0
    assert fake_artifact_session.artifacts[composed_id]["priority"] == -1.0
