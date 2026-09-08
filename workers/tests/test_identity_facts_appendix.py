"""_extract_identity_facts: a deterministic (non-LLM) safety net for search_nfd_directory identity links, mirroring _extract_asset_facts for on-chain asset names.

Root-caused 2026-09-08 (AlgoChess recompose): search_nfd_directory returned a
domain owner cryptographically verified to an X handle, and that SAME address
was the admin/treasury field on the story's own contracts -- present in the
raw trace, even referenced in the model's own gap-analysis reasoning, but
never reached the published article. Raw-mode digests (provider == deepseek)
skip the synthesized digest's own anti-dropping rules, so a real identity
link had no guarantee of surviving into Stage 2's prompt otherwise.
"""

from __future__ import annotations

from app.modules.ai import llm_compose as mc

_ADMIN_ADDR = "AOVR7OSTVQL7YZTFGVJ3XEIJCQQIM5LAM7YWXHBHBDTOCULPIDD6CFFY3I"


def _nfd_name_result(**overrides: object) -> dict:
    base = {
        "name": "algochess.algo",
        "found": True,
        "owner": _ADMIN_ADDR,
        "deposit_account": _ADMIN_ADDR,
        "url": "https://algochess.org",
        "nfd_profile_url": "https://app.nf.domains/name/algochess.algo",
        "time_created": "2026-09-06T12:43:19Z",
        "verified_discord": None,
        "verified_github": None,
        "verified_x": "@algochess",
        "verified_bluesky_did": None,
        "verified_telegram": None,
        "linked_algorand_addresses": [_ADMIN_ADDR],
    }
    base.update(overrides)
    return base


def test_collects_a_verified_identity_and_formats_it() -> None:
    """A resolved NFD with a verified X handle survives into the appendix, address and handle both present."""
    trace = [
        {
            "tool": "search_nfd_directory",
            "arguments": {"name": "algochess.algo"},
            "result": _nfd_name_result(),
        }
    ]
    facts = mc._collect_identity_facts(trace)
    assert "algochess.algo" in facts
    assert facts["algochess.algo"]["owner"] == _ADMIN_ADDR
    assert facts["algochess.algo"]["verified_x"] == "@algochess"

    rendered = mc._extract_identity_facts(trace)
    assert "algochess.algo" in rendered
    assert _ADMIN_ADDR in rendered
    assert "@algochess" in rendered


def test_a_resolved_nfd_with_nothing_verified_is_left_out() -> None:
    """found: True alone (no verified social, no linked address) isn't independent corroboration -- excluded from the appendix, still visible in the raw trace either way."""
    trace = [
        {
            "tool": "search_nfd_directory",
            "arguments": {"name": "nobody.algo"},
            "result": _nfd_name_result(
                name="nobody.algo",
                verified_x=None,
                linked_algorand_addresses=[],
            ),
        }
    ]
    assert mc._extract_identity_facts(trace) == ""


def test_an_unresolved_lookup_is_left_out() -> None:
    """found: False (the name doesn't exist) contributes nothing."""
    trace = [
        {
            "tool": "search_nfd_directory",
            "arguments": {"name": "doesnotexist.algo"},
            "result": {"name": "doesnotexist.algo", "found": False},
        }
    ]
    assert mc._extract_identity_facts(trace) == ""


def test_a_different_tool_is_ignored() -> None:
    """Only search_nfd_directory results feed this appendix -- an unrelated tool result with similar-looking fields is never mistaken for one."""
    trace = [
        {
            "tool": "lookup_application",
            "arguments": {"app_id": 123},
            "result": {"name": "algochess.algo", "found": True, "verified_x": "@spoofed"},
        }
    ]
    assert mc._extract_identity_facts(trace) == ""


def test_the_appendix_survives_into_the_raw_mode_digest(monkeypatch) -> None:  # noqa: ANN001
    """Regression on the exact path: _synthesize_research_digest (deepseek/raw mode) includes the identity-facts appendix in what Stage 2 actually reads, not just something computable in isolation."""
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False, raising=False)
    trace = [
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://algochess.org"},
            "result": {"text": "..."},
        },
        {
            "tool": "search_nfd_directory",
            "arguments": {"name": "algochess.algo"},
            "result": _nfd_name_result(),
        },
    ]
    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="deepseek"
    )
    assert "### Verified Identity Links" in digest
    assert _ADMIN_ADDR in digest
    assert "@algochess" in digest


def test_the_appendix_is_absent_when_no_identity_was_found(monkeypatch) -> None:  # noqa: ANN001
    """No search_nfd_directory hit at all -- no appendix header at all, not an empty one."""
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False, raising=False)
    trace = [
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://algochess.org"},
            "result": {"text": "..."},
        },
    ]
    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="deepseek"
    )
    assert "### Verified Identity Links" not in digest
