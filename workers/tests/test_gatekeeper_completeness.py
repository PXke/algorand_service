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


def test_domain_provenance_trigger_word_rule_removed() -> None:
    """The old domain_provenance trigger-word rule is gone (removed 2026-08-21): it matched nearly every scraped page's own boilerplate (confirmed live on algorand.foundation, algorand.co, perawallet.app), not just genuinely unverified domains. The real check now lives in gatekeeper/live.py's dead-domain check, which asks a narrower, answerable question against domain_tracking instead of scanning source_text for url-ish words."""
    src = "The team announced it at https://example.io today."
    failed = c.check_completeness(src, "{}").failed_rules
    assert "domain_provenance" not in failed


def test_record_claim_without_record_read_fails() -> None:
    """Root-cause regression (2026-09-09, AlgoChess incident).

    The article claimed a settlement transaction's note field carried player
    ratings, but no record-reading tool was ever called.
    """
    src = "The transaction note field carries each player's rating before and after."
    r = c.check_completeness(src, tool_trace='{"search_web": "..."}')
    assert not r.passed
    assert "record_read" in r.failed_rules


def test_record_claim_with_record_read_passes() -> None:
    """Passes once the trace shows a record-reading tool actually ran."""
    src = "The transaction note field carries each player's rating before and after."
    r = c.check_completeness(src, tool_trace='{"lookup_transaction_note": {"note": "..."}}')
    assert r.passed
    assert "record_read" not in r.failed_rules


def test_memo_trigger_does_not_match_inside_an_unrelated_word() -> None:
    """Root-cause regression (2026-09-09, design review before shipping): the old plain substring check for the "memo" trigger matched inside unrelated words containing that substring ("commemorate", "memory") -- a piece about browser memory or a memorial event must not spuriously require a record-reading tool call."""
    src = "The library warns against persisting a mnemonic in browser memory, and the project will commemorate its anniversary."
    r = c.check_completeness(src, tool_trace='{"search_web": "..."}')
    assert "record_read" not in r.failed_rules


def test_memo_trigger_still_matches_as_a_whole_word() -> None:
    """The word-boundary fix must not break the real trigger it's guarding."""
    src = "The payment's memo explains the transfer's purpose."
    r = c.check_completeness(src, tool_trace='{"search_web": "..."}')
    assert "record_read" in r.failed_rules


def test_record_read_required_any_matches_the_value_check_tool_set() -> None:
    """Root-cause regression (2026-09-09, design review before shipping): required_any used to be a narrower, independently-drifting copy of unsourced_specifics_gate's RECORD_TOOLS -- a claim genuinely grounded via fetch_url (a raw indexer JSON fetch, which the record-attributed-value check already treats as a legitimate record read) could pass that check and still fail this one on the exact same sentence. Both now share RECORD_READ_TOOLS."""
    from app.modules.gatekeeper.fact_align import RECORD_READ_TOOLS

    src = "The transaction note field carries each player's rating before and after."
    r = c.check_completeness(
        src,
        tool_trace='{"fetch_url": {"url": "https://mainnet-idx.algonode.cloud/v2/transactions/X"}}',
    )
    assert "fetch_url" in RECORD_READ_TOOLS
    assert r.passed
    assert "record_read" not in r.failed_rules


def test_named_persons_unscreened() -> None:
    """Lists named persons lacking a sanctions/PEP screen, empty once the trace shows one ran."""
    src = "Founder Jane Doe and CEO Mike Smith spoke."
    names = c.named_persons_unscreened(src, tool_trace="{}")
    assert "Jane Doe" in names
    # Once screened, nothing is reported.
    assert c.named_persons_unscreened(src, '{"screen_sanctions_and_pep": {}}') == []


def test_named_persons_unscreened_ignores_marketing_copy() -> None:
    """Marketing bullet phrases (Capitalized runs that aren't names) must not be reported as candidate persons -- found 2026-08-07 on a Polkagold review row whose gk_reasons listed "Robust Ecosystem Role" and "Digital Trust" alongside a genuine founder mention."""
    src = (
        "Founder Jane Doe launched the protocol. Robust Ecosystem Role. "
        "Innovative Distribution Model. Digital Trust. The Reserve Digital Commodity."
    )
    names = c.named_persons_unscreened(src, tool_trace="{}")
    assert names == ["Jane Doe"]
