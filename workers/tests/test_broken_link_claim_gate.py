"""The broken-link-claim gate: a body claim that a link/page/feature is broken/404/doesn't work must be backed by a real click_element/play_interactive click attempt in the trace, not just a guessed fetch_url. Root-caused 2026-08-10 (lumirogue.com About/Terms footer, JS buttons mistaken for dead links) and recurred 2026-08-12 despite prompt-only guidance. Read-only by default -- records, never mutates."""

from __future__ import annotations

import pytest

from app.modules.newspaper import broken_link_claim_gate as gate


def _click_trace(url: str = "https://example.com") -> list[dict]:
    return [{"tool": "click_element", "arguments": {"url": url, "click_text": "About"}, "result": {}}]


def _play_click_trace() -> list[dict]:
    return [{"tool": "play_interactive", "arguments": {"action": "click", "target": "About"}, "result": {}}]


def _play_open_trace() -> list[dict]:
    """A play_interactive call that only opened a page -- never actually clicked anything."""
    return [{"tool": "play_interactive", "arguments": {"action": "open", "url": "https://example.com"}, "result": {}}]


def _fetch_only_trace() -> list[dict]:
    return [{"tool": "fetch_url", "arguments": {"url": "https://example.com/about"}, "result": {"error": "404"}}]


# --------------------------------------------------------------------------- #
# the real incident must flag
# --------------------------------------------------------------------------- #
def test_flags_broken_claim_with_no_click_attempt() -> None:
    """The exact lumirogue.com shape: a guessed URL 404s, no click_element/play_interactive attempted anywhere."""
    body = "The site's About page returns a 404 and the Terms link is broken."
    findings = gate.find_unverified_broken_link_claims(body, _fetch_only_trace())
    claims = {f["claim"].lower() for f in findings}
    assert any("404" in c for c in claims)
    assert any("broken" in c for c in claims)


def test_flags_broken_claim_with_empty_trace() -> None:
    """No trace at all (None) is treated the same as no click attempt -- fails toward flagging, not silently passing."""
    body = "This page is a dead link."
    assert gate.find_unverified_broken_link_claims(body, None) != []


# --------------------------------------------------------------------------- #
# a real click attempt clears it
# --------------------------------------------------------------------------- #
def test_click_element_attempt_clears_the_claim() -> None:
    """A real click_element call anywhere in the trace is enough verification effort, regardless of what else is in the trace."""
    body = "The About page returns a 404."
    assert gate.find_unverified_broken_link_claims(body, _click_trace()) == []


def test_play_interactive_click_action_clears_the_claim() -> None:
    """play_interactive with action='click' counts the same as click_element."""
    body = "The Terms link is broken."
    assert gate.find_unverified_broken_link_claims(body, _play_click_trace()) == []


def test_play_interactive_open_only_does_not_clear_the_claim() -> None:
    """Merely opening a page via play_interactive (never clicking anything) is not verification -- same gap as a guessed fetch_url."""
    body = "The About page returns a 404."
    assert gate.find_unverified_broken_link_claims(body, _play_open_trace()) != []


def test_a_failed_click_still_counts_as_verification() -> None:
    """Even a click_element call whose RESULT was itself an error still counts -- the point is verification effort was made, not that it succeeded."""
    trace = [{"tool": "click_element", "arguments": {"url": "https://example.com"}, "result": {"error": "not found"}}]
    body = "The About page returns a 404."
    assert gate.find_unverified_broken_link_claims(body, trace) == []


# --------------------------------------------------------------------------- #
# no false positives on ordinary prose
# --------------------------------------------------------------------------- #
def test_no_broken_language_no_findings() -> None:
    """Prose with no broken-link claim at all produces nothing, regardless of trace."""
    body = "The About page describes the developer's background and the project's mission."
    assert gate.find_unverified_broken_link_claims(body, None) == []


def test_empty_body_returns_no_findings() -> None:
    """No body at all is a no-op, not an error."""
    assert gate.find_unverified_broken_link_claims("", None) == []


def test_duplicate_phrases_reported_once() -> None:
    """The same broken-link phrase appearing twice in the body is reported once, not per occurrence."""
    body = "The About page returns a 404. The Terms page also returns a 404."
    findings = gate.find_unverified_broken_link_claims(body, _fetch_only_trace())
    assert len(findings) == 1


# --------------------------------------------------------------------------- #
# revision-issue feedback + the payload-mutating wrapper
# --------------------------------------------------------------------------- #
def test_revision_issues_empty_when_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ENABLED kill switch blocks revision feedback entirely, even on an otherwise-flaggable claim."""
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENABLED", False)
    body = "The About page returns a 404."
    assert gate.broken_link_claim_revision_issues(body, _fetch_only_trace()) == []


def test_revision_issues_nonempty_when_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unverified claim produces revision-loop feedback telling the writer to click_element or soften the claim."""
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENABLED", True)
    body = "The About page returns a 404."
    issues = gate.broken_link_claim_revision_issues(body, _fetch_only_trace())
    assert issues
    assert "click_element" in issues[0]


def test_flag_records_findings_without_holding_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read-only mode records findings on the payload but never sets a hold reason."""
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENABLED", True)
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENFORCE", False)
    payload = {"body": "The About page returns a 404."}
    result = gate.flag_unverified_broken_link_claims(payload, _fetch_only_trace())
    assert result["_broken_link_claims"]
    assert "_broken_link_hold_reason" not in result


def test_flag_sets_hold_reason_when_enforcing(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENFORCE mode additionally sets the hold reason the publish gate diverts on."""
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENABLED", True)
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENFORCE", True)
    payload = {"body": "The About page returns a 404."}
    result = gate.flag_unverified_broken_link_claims(payload, _fetch_only_trace())
    assert result.get("_broken_link_hold_reason")


def test_flag_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ENABLED kill switch leaves the payload completely untouched."""
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENABLED", False)
    payload = {"body": "The About page returns a 404."}
    result = gate.flag_unverified_broken_link_claims(payload, _fetch_only_trace())
    assert "_broken_link_claims" not in result


def test_flag_is_fail_open_on_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate bug must never block a release -- any exception during scanning yields the payload unchanged."""
    monkeypatch.setattr("app.core.config.BROKEN_LINK_CLAIM_GATE_ENABLED", True)
    monkeypatch.setattr(
        gate, "find_unverified_broken_link_claims", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    payload = {"body": "The About page returns a 404."}
    result = gate.flag_unverified_broken_link_claims(payload, _fetch_only_trace())
    assert "_broken_link_claims" not in result
