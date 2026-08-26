"""Editorial-room artifact priority scoring (2026-08-25, SHADOW MODE): the three v1 SCORE_COMPONENTS and the sweep that applies them.

Uses the shared `fake_artifact_session` fixture (conftest.py) so the sweep
integration test exercises the real Cassandra-shaped round trip, not just
the pure scoring functions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest
from conftest import FakeArtifactSession

# --------------------------------------------------------------------------- #
# word_count_score
# --------------------------------------------------------------------------- #


def test_word_count_score_zero_for_empty_content() -> None:
    """Empty (or whitespace-only) content scores zero."""
    from algorand_shared.artifact_priority import word_count_score

    assert word_count_score("") == 0.0
    assert word_count_score("   ") == 0.0


def test_word_count_score_increases_with_more_words() -> None:
    """More substantial content scores higher."""
    from algorand_shared.artifact_priority import word_count_score

    short = word_count_score(" ".join(["word"] * 50))
    medium = word_count_score(" ".join(["word"] * 300))
    long_ = word_count_score(" ".join(["word"] * 1200))
    assert 0.0 < short < medium < long_


def test_word_count_score_diminishing_returns_not_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doubling word count must not double the score (diminishing returns, per the sqrt curve) -- going from 300 to 600 words gains less than going from 0 to 300 did."""
    from algorand_shared.artifact_priority import word_count_score

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
    from algorand_shared.artifact_priority import word_count_score

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
    from algorand_shared.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    assert timeliness_score(now, now, today=now) == pytest.approx(10.0)


def test_timeliness_score_decays_smoothly_as_it_ages() -> None:
    """Strictly decreasing with age -- no cliff, no plateau."""
    from algorand_shared.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    scores = [
        timeliness_score(now - timedelta(days=d), now, today=now) for d in (0, 5, 10, 30, 90, 365)
    ]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)  # every step strictly lower


def test_timeliness_score_never_hits_a_hard_floor_of_zero() -> None:
    """Explicit owner instruction: old-but-real content must stay theoretically reachable ('except when we have nothing else to report') -- even a 100-year-old artifact scores strictly above zero."""
    from algorand_shared.artifact_priority import timeliness_score

    now = datetime(2026, 8, 25, tzinfo=UTC)
    ancient = timeliness_score(now - timedelta(days=365 * 100), now, today=now)
    assert ancient > 0.0


def test_timeliness_score_never_drops_below_configured_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """However old the artifact, the score must never fall below the configured floor -- at astronomically large ages the exponential term rounds away to (but never legitimately below) that floor."""
    from algorand_shared.artifact_priority import timeliness_score

    from app.core import config as cfg

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
    from algorand_shared.artifact_priority import timeliness_score

    from app.core import config as cfg

    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_FLOOR", 0.0)
    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_MAX_SCORE", 10.0)
    monkeypatch.setattr(cfg, "ARTIFACT_TIMELINESS_HALF_LIFE_DAYS", 21.0)
    now = datetime(2026, 8, 25, tzinfo=UTC)
    at_half_life = timeliness_score(now - timedelta(days=21), now, today=now)
    assert at_half_life == pytest.approx(5.0, abs=0.01)


def test_timeliness_score_falls_back_to_created_at_when_no_event_date() -> None:
    """No extractable event_date falls back to created_at as the freshness anchor, per the artifacts-table fallback rule."""
    from algorand_shared.artifact_priority import timeliness_score

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
    from algorand_shared.artifact_priority import ecosystem_listed_score

    assert ecosystem_listed_score(None) == 0.0
    assert ecosystem_listed_score("") == 0.0


def test_ecosystem_listed_score_boosts_a_directory_listed_domain_with_on_topic_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reuses the SAME ecosystem_listed_domains() registry the crawler-discovery scorer uses for the identical chain-silent-but-important-service problem, rather than a second registry -- but (2026-08-26) only when the artifact's OWN content actually clears keyword_hits() > 0, since this registry (unlike KNOWN_DOMAINS) isn't curated per-entry for chain-silence."""
    from algorand_shared import artifact_priority

    from app.core import config as cfg

    monkeypatch.setattr(cfg, "ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains",
        lambda: frozenset({"hesabpay.com"}),
    )

    on_topic = "HesabPay runs its rails on Algorand mainnet for cross-border settlement."
    assert (
        artifact_priority.ecosystem_listed_score("https://hesabpay.com/blog/post", on_topic) == 5.0
    )
    assert (
        artifact_priority.ecosystem_listed_score("https://unrelated.example.com/", on_topic) == 0.0
    )


def test_ecosystem_listed_score_zero_for_directory_listed_domain_with_off_topic_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test (2026-08-26, ulam.io): a domain that is ONLY in ecosystem_listed_domains() (not the curated chain-silent KNOWN_DOMAINS list) must NOT get the bonus once the actual fetched content has drifted completely off-topic -- ulam.io is a real historical ecosystem-directory listing (Ulam Labs built Pact), but the artifact scored today was its plain homepage, 858 words of generic MedTech marketing copy with zero Algorand/blockchain/crypto keyword hits. Trusting the domain's directory membership forever let a stale listing keep inflating priority long after the specific content being scored had nothing to do with Algorand."""
    from algorand_shared import artifact_priority

    from app.core import config as cfg

    monkeypatch.setattr(cfg, "ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains",
        lambda: frozenset({"ulam.io"}),
    )

    off_topic_medtech_copy = (
        "ULAM LABS helps founders, CTOs, and product teams build secure, "
        "scalable, production-ready software for complex healthcare "
        "environments, meeting NHS, ISO 27001, and HIPAA/GDPR requirements."
    )
    assert (
        artifact_priority.ecosystem_listed_score("https://ulam.io/", off_topic_medtech_copy) == 0.0
    )
    # No content at all (never fetched, or empty) is the same "no evidence" case.
    assert artifact_priority.ecosystem_listed_score("https://ulam.io/") == 0.0
    assert artifact_priority.ecosystem_listed_score("https://ulam.io/", "") == 0.0


def test_ecosystem_listed_score_boosts_a_known_domains_entry_regardless_of_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checking only ecosystem_listed_domains() (2026-08-26 root-caused bug) missed every chain-silent service hardcoded in score_page's own KNOWN_DOMAINS list -- exactly the class this bonus exists to protect. sealed.channel is in KNOWN_DOMAINS but deliberately NOT in ecosystem_listed_domains() here, to prove the union, not just the first registry, is what earns the bonus.

    Also proves the 2026-08-26 content-signal requirement does NOT apply to
    KNOWN_DOMAINS: a chain-silent service's own pages may legitimately never
    say "algorand" (that's the entire reason it's hardcoded here), so the
    bonus must survive even when content has zero keyword hits -- the exact
    case the ulam.io fix must not regress.
    """
    from algorand_shared import artifact_priority

    from app.core import config as cfg

    monkeypatch.setattr(cfg, "ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains",
        lambda: frozenset(),
    )

    chain_silent_content = "Sealed is a private, end-to-end encrypted messenger for teams."
    assert artifact_priority.ecosystem_listed_score("https://sealed.channel/") == 5.0
    assert (
        artifact_priority.ecosystem_listed_score("https://sealed.channel/", chain_silent_content)
        == 5.0
    )
    assert artifact_priority.ecosystem_listed_score("https://sealed.channel/", "") == 5.0


def test_ecosystem_listed_score_fails_open_to_zero_on_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra-unreachable registry lookup fails open to 0.0, never raises."""
    from algorand_shared import artifact_priority

    def _boom() -> frozenset[str]:
        raise RuntimeError("cassandra unreachable")

    monkeypatch.setattr("app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", _boom)
    assert artifact_priority.ecosystem_listed_score("https://hesabpay.com/") == 0.0


# --------------------------------------------------------------------------- #
# skip_count_score
# --------------------------------------------------------------------------- #


def test_skip_count_score_zero_with_no_segments() -> None:
    """A fresh artifact (never concatenated) has no metadata["segments"] entry at all -- scores zero, same as an explicit empty list or None metadata."""
    from algorand_shared.artifact_priority import skip_count_score

    assert skip_count_score(None) == 0.0
    assert skip_count_score({}) == 0.0
    assert skip_count_score({"segments": []}) == 0.0


def test_skip_count_score_increases_linearly_with_segment_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike word_count_score's sqrt curve, this component is linear -- each additional ignored cycle buys the SAME increment, not a shrinking one, so it keeps differentiating "ignored a lot" from "ignored a whole lot" instead of flattening out."""
    from algorand_shared.artifact_priority import skip_count_score

    from app.core import config as cfg

    monkeypatch.setattr(cfg, "ARTIFACT_SKIP_COUNT_CAP", 10)
    monkeypatch.setattr(cfg, "ARTIFACT_SKIP_COUNT_MAX_SCORE", 10.0)

    scores = [skip_count_score({"segments": [{}] * n}) for n in (0, 2, 4, 6, 8, 10)]
    gains = [round(b - a, 4) for a, b in pairwise(scores)]
    assert scores == sorted(scores)
    # Every 2-segment step buys the identical amount -- no diminishing returns.
    assert len(set(gains)) == 1


def test_skip_count_score_caps_past_configured_segment_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service concatenated far more times than the cap doesn't keep buying unbounded priority purely on neglect count."""
    from algorand_shared.artifact_priority import skip_count_score

    from app.core import config as cfg

    monkeypatch.setattr(cfg, "ARTIFACT_SKIP_COUNT_CAP", 5)
    monkeypatch.setattr(cfg, "ARTIFACT_SKIP_COUNT_MAX_SCORE", 6.0)

    at_cap = skip_count_score({"segments": [{}] * 5})
    way_over = skip_count_score({"segments": [{}] * 500})
    assert at_cap == 6.0
    assert way_over == 6.0


# --------------------------------------------------------------------------- #
# compute_artifact_priority / SCORE_COMPONENTS architecture
# --------------------------------------------------------------------------- #


def test_compute_artifact_priority_sums_all_score_components(monkeypatch: pytest.MonkeyPatch) -> None:
    """Priority = sum(component(artifact, content) for component in SCORE_COMPONENTS) -- pins the additive architecture so a future 4th signal only needs to append to the tuple."""
    from algorand_shared import artifact_priority

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
    from algorand_shared.artifact_priority import sweep_artifact_priorities
    from algorand_shared.artifact_store import insert_artifact

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


def test_priority_keeps_rising_after_word_count_score_plateaus_via_skip_count(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Demonstrates the bug this session fixed and proves the fix.

    word_count_score saturates at ARTIFACT_WORD_COUNT_CAP words, which a
    chronically-ignored service (repeated concatenation via insert_artifact,
    see artifact_store.py) reaches after only a handful of cycles -- well
    before ARTIFACT_CONCAT_MAX_OLD_CHARS, concatenation's own much larger
    ceiling, is reached (this mirrors the real proportions: default
    ARTIFACT_WORD_COUNT_CAP=1200 words vs ARTIFACT_CONCAT_MAX_OLD_CHARS=20000
    chars, roughly 3300 words at a 6-char average). Once word_count_score
    saturates, it stops moving entirely -- BEFORE this fix, an artifact
    ignored 4 times and one ignored 14 times scored identically on the sum of
    word_count_score + timeliness_score + ecosystem_listed_score, even though
    the second is the far stronger case for finally getting composed. This
    silently broke the explicit "chronically-ignored services keep climbing"
    design intent behind the concatenation mechanism (see
    ARTIFACT_CONCAT_MAX_OLD_CHARS's own config comment and
    insert_artifact's docstring).

    AFTER the fix (skip_count_score reading metadata["segments"] directly),
    total priority keeps climbing for every additional ignored cycle even
    once word_count_score alone has gone flat.
    """
    from algorand_shared.artifact_priority import (
        compute_artifact_priority,
        word_count_score,
    )
    from algorand_shared.artifact_store import (
        get_artifact,
        get_artifact_content,
        insert_artifact,
    )

    from app.core import config as cfg

    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )
    # Small word-count cap, reachable in a handful of ~100-word cycles --
    # mirrors the real-world proportion where word count saturates well
    # before the concatenation mechanism's own (much larger) char cap.
    monkeypatch.setattr(cfg, "ARTIFACT_WORD_COUNT_CAP", 500)
    monkeypatch.setattr(cfg, "ARTIFACT_WORD_COUNT_MAX_SCORE", 10.0)
    monkeypatch.setattr(cfg, "ARTIFACT_SKIP_COUNT_CAP", 12)
    monkeypatch.setattr(cfg, "ARTIFACT_SKIP_COUNT_MAX_SCORE", 6.0)
    # Concatenation's own cap stays comfortably above anything this test
    # accumulates, so it never interferes.
    monkeypatch.setattr(cfg, "ARTIFACT_CONCAT_MAX_OLD_CHARS", 200_000)

    total_priorities: list[float] = []
    word_count_only: list[float] = []
    artifact_id = ""
    for cycle in range(12):
        update_text = " ".join([f"word{cycle}-{i}" for i in range(100)])  # ~100 words/cycle
        artifact_id, _ = insert_artifact(
            service_id="svc-neglected", url=None, channel="crawler", content=update_text
        )
        artifact = get_artifact(artifact_id)
        content = get_artifact_content(artifact_id)
        assert artifact is not None
        assert content is not None
        total_priorities.append(compute_artifact_priority(artifact, content))
        word_count_only.append(word_count_score(content.content))

    # Sanity: word_count_score genuinely saturates well before the 12th
    # cycle (~1200 accumulated words against a 500-word cap).
    assert word_count_only[-1] == 10.0
    plateau_start = next(i for i, s in enumerate(word_count_only) if s == 10.0)
    assert plateau_start < len(word_count_only) - 1, "word_count_score never actually plateaus in this test"
    assert word_count_only[plateau_start:] == [10.0] * (len(word_count_only) - plateau_start)

    # The bug being fixed: without skip_count_score, total priority would be
    # flat across that same plateau window (word_count_score contributes
    # nothing further, and timeliness/ecosystem are ~constant across cycles
    # run back-to-back). With the fix, total priority keeps rising because
    # skip_count_score is still climbing (segments keeps growing every cycle,
    # cap is 12).
    plateau_priorities = total_priorities[plateau_start:]
    assert plateau_priorities == sorted(plateau_priorities), (
        "priority should keep climbing through the word_count_score plateau"
    )
    assert plateau_priorities[-1] > plateau_priorities[0], (
        "skip_count_score must keep differentiating a chronically-ignored "
        "service even after word_count_score has flatlined"
    )


def test_sweep_never_touches_non_pending_artifacts(
    fake_artifact_session: FakeArtifactSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A composed/selected/discarded artifact's priority is left exactly as-is by the sweep -- only PENDING artifacts are ever touched."""
    from algorand_shared.artifact_priority import sweep_artifact_priorities
    from algorand_shared.artifact_store import COMPOSED, insert_artifact, mark_artifact_status

    monkeypatch.setattr(
        "app.modules.crawler.ecosystem_sync.ecosystem_listed_domains", lambda: frozenset()
    )

    composed_id, _ = insert_artifact(service_id="svc-c", url=None, channel="brief", content="x")
    mark_artifact_status(composed_id, COMPOSED)
    fake_artifact_session.artifacts[composed_id]["priority"] = -1.0  # sentinel, must be untouched

    result = sweep_artifact_priorities()

    assert result["swept"] == 0
    assert fake_artifact_session.artifacts[composed_id]["priority"] == -1.0
