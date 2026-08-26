"""Backend-side proof that `ecosystem_listed_score`/`ecosystem_scoring_available` genuinely compute the ecosystem-directory bonus in backend's OWN process (2026-08-26 gap closure -- see `algorand_shared.artifact_priority`'s module docstring).

Before this fix, `ecosystem_listed_score`'s directory-listed bonus hard-
depended on workers-only modules (`app.modules.crawler.domain_tracker`,
`app.modules.crawler.ecosystem_sync`, `app.modules.search.classifier.score`)
that don't exist in backend's codebase at all -- so it always failed open to
0.0 here, and `ecosystem_scoring_available()` always reported False. This
suite runs in backend's REAL test environment, where those modules are
genuinely absent (see `test_crawler_and_classifier_modules_genuinely_do_not_
exist_in_backend` below) -- no `sys.modules` simulation needed, unlike the
workers-side tests that simulate backend's missing-module reality from
within workers' own suite. A passing `ecosystem_scoring_available() is True`
here, with no mocking of any import, IS the live proof the gap is closed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from algorand_shared import artifact_priority
from conftest import patch_cassandra


@pytest.fixture(autouse=True)
def _reset_ecosystem_directory_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """`algorand_shared.ecosystem_directory` caches its Cassandra read for an hour (process-global, mirroring `ecosystem_sync`'s own cache shape) -- reset it before every test so each one hits its own fake session's rows instead of reusing whatever an earlier test cached."""
    monkeypatch.setattr(
        "algorand_shared.ecosystem_directory._cache", {"at": 0.0, "domains": frozenset()}
    )


def _row(domain: str, *, ecosystem_listed: bool, is_relevant: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        domain=domain,
        metadata={"ecosystem_listed": "true"} if ecosystem_listed else {},
        is_relevant=is_relevant,
    )


def test_crawler_and_classifier_modules_genuinely_do_not_exist_in_backend() -> None:
    """Sanity check for every other test in this file: if this ever starts passing an import instead of raising, backend has gained these modules and the tests below are no longer proving the FALLBACK path at all."""
    with pytest.raises(ModuleNotFoundError):
        import app.modules.crawler.domain_tracker

    with pytest.raises(ModuleNotFoundError):
        import app.modules.crawler.ecosystem_sync

    with pytest.raises(ModuleNotFoundError):
        import app.modules.search.classifier.score  # noqa: F401


def test_ecosystem_scoring_available_is_true_in_backends_own_process() -> None:
    """The whole point of the fix: this must report True here, with zero mocking -- this IS backend's real import context, not a simulation of it."""
    assert artifact_priority.ecosystem_scoring_available() is True


def test_ecosystem_listed_score_boosts_a_directory_listed_domain_with_on_topic_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real read path end to end: a fake Cassandra session stands in for `domain_tracking`, reached through `algorand_shared.ecosystem_directory`'s own query -- not a monkeypatched function (unlike workers' equivalent test, which patches `ecosystem_sync.ecosystem_listed_domains` directly since workers has that module to patch)."""
    monkeypatch.setattr("app.core.config.ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    session = patch_cassandra(monkeypatch)
    session.execute.return_value = [
        _row("hesabpay.com", ecosystem_listed=True),
        _row("unrelated.example.com", ecosystem_listed=False),
    ]

    on_topic = "HesabPay runs its rails on Algorand mainnet for cross-border settlement."
    assert (
        artifact_priority.ecosystem_listed_score("https://hesabpay.com/blog/post", on_topic)
        == 5.0
    )
    assert (
        artifact_priority.ecosystem_listed_score("https://unrelated.example.com/", on_topic)
        == 0.0
    )


def test_ecosystem_listed_score_zero_for_directory_listed_domain_with_off_topic_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same ulam.io-class regression the workers suite pins: a directory listing alone (not KNOWN_DOMAINS) must not survive content that's drifted fully off-topic."""
    monkeypatch.setattr("app.core.config.ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    session = patch_cassandra(monkeypatch)
    session.execute.return_value = [_row("ulam.io", ecosystem_listed=True)]

    off_topic_medtech_copy = (
        "ULAM LABS helps founders, CTOs, and product teams build secure, "
        "scalable, production-ready software for complex healthcare environments."
    )
    assert artifact_priority.ecosystem_listed_score("https://ulam.io/", off_topic_medtech_copy) == 0.0


def test_ecosystem_listed_score_boosts_a_known_domains_entry_with_empty_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KNOWN_DOMAINS (sealed.channel, chain-silent) grants the bonus unconditionally -- no directory-registry hit needed, no content needed -- proving `algorand_shared.keyword_relevance.KNOWN_DOMAINS` is genuinely reachable here too, not just the Cassandra-backed registry."""
    monkeypatch.setattr("app.core.config.ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    session = patch_cassandra(monkeypatch)
    session.execute.return_value = []

    assert artifact_priority.ecosystem_listed_score("https://sealed.channel/") == 5.0


def test_ecosystem_listed_score_uses_backends_own_domain_from_url_to_collapse_subdomains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves the resolved `domain_from_url` is backend's real, parity-tested implementation (`app.modules.registry.sources.domain_from_url`), not a stub -- a subdomain of a directory-listed domain still collapses to the eTLD+1 that's actually listed, same as workers' own domain_tracker.domain_from_url would do."""
    monkeypatch.setattr("app.core.config.ARTIFACT_ECOSYSTEM_LISTED_BOOST", 5.0)
    session = patch_cassandra(monkeypatch)
    session.execute.return_value = [_row("hesabpay.com", ecosystem_listed=True)]

    on_topic = "HesabPay runs its rails on Algorand mainnet."
    assert (
        artifact_priority.ecosystem_listed_score("https://blog.hesabpay.com/post", on_topic)
        == 5.0
    )


def test_ecosystem_listed_score_fails_open_to_zero_on_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra-unreachable registry lookup fails open to 0.0 here too, never raises -- same contract as workers' own equivalent test."""
    session = patch_cassandra(monkeypatch)
    session.execute.side_effect = RuntimeError("cassandra unreachable")

    assert artifact_priority.ecosystem_listed_score("https://hesabpay.com/") == 0.0
