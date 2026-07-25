"""Deterministic completeness rules: mandatory checks must fire when the source mentions a human/company/domain, and pass once the trace shows the tool ran."""

from app.modules.gatekeeper import completeness as c


def test_founder_without_screen_fails() -> None:
    """Fails the human_identity rule when a named founder appears but no screening tool ran."""
    src = "Founder Jane Doe launched the protocol."
    r = c.check_completeness(src, tool_trace='{"search_web": "..."}')
    assert not r.passed
    assert "human_identity" in r.failed_rules


def test_founder_with_screen_passes() -> None:
    """Passes with no failed rules once the trace shows screen_sanctions_and_pep ran."""
    src = "Founder Jane Doe launched the protocol."
    r = c.check_completeness(src, tool_trace='{"screen_sanctions_and_pep": {"hits": 0}}')
    assert r.passed
    assert r.failed_rules == ()


def test_no_trigger_no_requirement() -> None:
    """Passes when the source text triggers no mandatory-check rule at all."""
    src = "A routine mainnet parameter update shipped today."
    assert c.check_completeness(src, tool_trace="{}").passed


def test_domain_provenance_rule() -> None:
    """Fails the domain_provenance rule when a source URL's domain is never verified."""
    src = "The team announced it at https://example.io today."
    failed = c.check_completeness(src, "{}").failed_rules
    assert "domain_provenance" in failed


def test_named_persons_unscreened() -> None:
    """Lists named persons lacking a sanctions/PEP screen, empty once the trace shows one ran."""
    src = "Founder Jane Doe and CEO Mike Smith spoke."
    names = c.named_persons_unscreened(src, tool_trace="{}")
    assert "Jane Doe" in names
    # Once screened, nothing is reported.
    assert c.named_persons_unscreened(src, '{"screen_sanctions_and_pep": {}}') == []
