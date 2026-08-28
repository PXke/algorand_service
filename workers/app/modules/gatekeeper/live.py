"""Live deterministic pre-publish gate.

Runs the two model-free signals — completeness rules + trace<->article numeric
entailment — so the gatekeeper protects the pipeline *before* the ModernBERT
model is trained. Designed to ship in shadow mode: it always computes and
returns the signals (for the review metadata), and the caller decides whether to
enforce based on ``GATEKEEPER_ENFORCE``.

Everything here is failure-tolerant: a malformed input or missing trace yields a
permissive result, never an exception into the publish path.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.gatekeeper.completeness import check_completeness, named_persons_unscreened
from app.modules.gatekeeper.fact_align import numeric_entailment_score


@dataclass(frozen=True)
class DeterministicGate:
    """The deterministic factuality/completeness gate's verdict."""

    factuality_score: float  # grounded fraction of numeric claims
    completeness_passed: bool
    passed: bool  # overall, given the configured threshold
    reasons: tuple[str, ...] = ()  # human-readable failure reasons
    ungrounded: tuple[str, ...] = ()
    failed_rules: tuple[str, ...] = ()
    dead_domains: tuple[str, ...] = ()  # domains named in the article that are confirmed dead

    def as_metadata(self) -> dict[str, str]:
        """Compact strings for the review/grade metadata map."""
        return {
            "gk_factuality": f"{self.factuality_score:.2f}",
            "gk_completeness": "pass" if self.completeness_passed else "fail",
            "gk_passed": "1" if self.passed else "0",
            "gk_reasons": "; ".join(self.reasons)[:400],
            "gk_dead_domains": ",".join(self.dead_domains)[:200],
        }


@dataclass(frozen=True)
class GateConfig:
    """Thresholds for the deterministic gate."""

    fact_min: float = 0.80
    enforce: bool = False


def run_deterministic_gate(
    source_text: str,
    tool_trace: str,
    article_text: str,
    cfg: GateConfig | None = None,
) -> DeterministicGate:
    """Compute completeness + numeric entailment for a draft. Pure (no I/O)."""
    cfg = cfg or GateConfig()
    comp = check_completeness(source_text, tool_trace)
    fact = numeric_entailment_score(tool_trace, article_text)

    reasons: list[str] = []
    if not comp.passed:
        # named_persons_unscreened is the human_identity rule's own actionable
        # detail (candidate names lacking a sanctions/PEP screen) -- attaching
        # it whenever ANY rule fails, including domain_provenance or
        # company_backing, misleadingly implies those unrelated failures are
        # about people too. Worse, on a marketing-heavy source page the
        # extractor's "two Capitalized words" proxy for a person name matches
        # plenty of non-name phrases ("Robust Ecosystem Role"), which reads
        # as noise when it's surfaced next to a failure it has nothing to do
        # with (found 2026-08-07 auditing a held Polkagold review row).
        who = ""
        if "human_identity" in comp.failed_rules:
            unscreened = named_persons_unscreened(source_text, tool_trace)
            who = f" ({', '.join(unscreened)})" if unscreened else ""
        reasons.append(f"missing mandatory checks: {', '.join(comp.failed_rules)}{who}")
    if fact.score < cfg.fact_min:
        reasons.append(
            f"{len(fact.ungrounded)} ungrounded figure(s) "
            f"[{', '.join(fact.ungrounded[:5])}] — entailment {fact.score:.2f}"
            f" < {cfg.fact_min:.2f}"
        )

    passed = comp.passed and fact.score >= cfg.fact_min
    return DeterministicGate(
        factuality_score=fact.score,
        completeness_passed=comp.passed,
        passed=passed,
        reasons=tuple(reasons),
        ungrounded=fact.ungrounded,
        failed_rules=comp.failed_rules,
    )


def _suppressed_as_dead(status: dict[str, object]) -> bool:
    """Whether a domain_tracking row's dead_project_until suppression is still active."""
    from datetime import UTC, datetime

    until_raw = (status.get("metadata") or {}).get("dead_project_until")  # type: ignore[union-attr]
    if not until_raw:
        return False
    try:
        until = datetime.fromisoformat(until_raw)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    return until > datetime.now(tz=UTC)


def _dead_domains_referenced(article_text: str, *, source_domain: str = "") -> list[str]:
    """Domains named ANYWHERE in the FINAL article body (prose, not just markdown links) that are confirmed dead.

    Complements defunct_entity_gate's defunct_linked_domains, which already
    catches this at compose time for hyperlinked hosts only -- a domain
    recommended in prose without a link ("compare to old wallet X") slips
    past it. Two sources of "confirmed dead", both already durable platform
    state so this never has to guess: domain_tracking already has the domain
    flagged via ``suppress_dead_project_domain`` (a prior writer's own
    ``abort_article(dead_project)`` call, whose suppression window hasn't
    lapsed yet) -- or, for a domain we've never crawled at all, a live DNS
    check using defunct_entity_gate's own hardened resolver (fail-open on
    any non-definitive error, so a resolver hiccup never holds an article).
    Skips ``source_domain`` (the article's own subject, just scraped
    successfully, so its liveness is self-evident) and only ever resolves
    the handful of distinct domains the body actually names -- never a
    whole-page scan.
    """
    from app.modules.crawler.domain_tracker import get_domain_status
    from app.modules.newspaper.defunct_entity_gate import _resolves
    from app.modules.newspaper.scam_enrichment import extract_domains_and_urls

    _, raw_domains = extract_domains_and_urls(article_text)
    dead: list[str] = []
    live_checks = 0
    seen: set[str] = set()
    for raw_domain in raw_domains:
        # extract_domains_and_urls keeps trailing sentence punctuation glued
        # to a bare URL match ("...at https://deadwallet.io." -> the "." is
        # part of the netloc) -- strip it so lookups and the reported name
        # are the real domain, not "deadwallet.io.". It can also list the
        # same real domain twice (once via the url netloc, once via the bare
        # regex, which naturally stops before punctuation), so dedup here too.
        domain = raw_domain.rstrip(".,;:!?)\"'")
        if not domain or domain == source_domain or domain in seen:
            continue
        seen.add(domain)
        status = get_domain_status(domain)
        if status:
            if _suppressed_as_dead(status):
                dead.append(domain)
            continue  # tracked (and not currently suppressed as dead)
        # Never tracked by the crawler -- the only case worth a live check,
        # bounded like defunct_entity_gate's own linked-domain scan so a
        # link-farm body can't stall this on dozens of live lookups.
        if live_checks >= 20:
            continue
        live_checks += 1
        if not _resolves(domain):
            dead.append(domain)
    return dead


def gate_draft(*, source_text: str, article_text: str, source_url: str) -> DeterministicGate | None:
    """Convenience wrapper for the publish task: loads the trace by source_url, reads config, runs the gate, then folds in the dead-domain check (needs I/O -- domain_tracking lookups and, for never-seen domains, a live DNS resolution -- so it lives here rather than in the pure ``run_deterministic_gate`` core). Returns None when disabled or on any error (shadow-safe). The caller enforces only when ``GATEKEEPER_ENFORCE`` and ``not result.passed``.

    ``source_url`` is used for both lookups: it's the compose-time source_url
    that ``load_investigation_trace`` keys the stored trace by (see that
    function's own docstring), and it's also what the dead-domain check
    derives the article's own domain from, so it's never excluded from its
    own "references a dead domain" scan. This used to be two separate
    parameters (``service_id`` for the trace lookup, ``source_url`` for the
    domain check) even though every caller passed the identical URL to both
    -- misleading, since the trace is never actually keyed by a service_id,
    and a latent footgun, since nothing stopped a caller from passing two
    different values and silently keying the trace lookup off one URL while
    excluding a different domain from the dead-domain scan.
    """
    try:
        from app.core.config import (
            GATEKEEPER_ENABLED,
            GATEKEEPER_ENFORCE,
            GATEKEEPER_FACT_MIN,
        )

        if not GATEKEEPER_ENABLED:
            return None
        from app.modules.newspaper.investigation_store import load_investigation_trace

        trace = load_investigation_trace(source_url)
        gate = run_deterministic_gate(
            source_text,
            trace,
            article_text,
            GateConfig(fact_min=GATEKEEPER_FACT_MIN, enforce=GATEKEEPER_ENFORCE),
        )
        from app.modules.crawler.domain_tracker import domain_from_url

        source_domain = domain_from_url(source_url) if source_url else ""
        dead = _dead_domains_referenced(article_text, source_domain=source_domain)
        if not dead:
            return gate
        from dataclasses import replace

        return replace(
            gate,
            passed=False,
            dead_domains=tuple(dead),
            reasons=(*gate.reasons, f"references confirmed-dead domain(s): {', '.join(dead)}"),
        )
    except Exception:
        return None
