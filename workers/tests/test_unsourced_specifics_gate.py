"""The unsourced-specifics gate: flag hard traction/funding numbers and named partners/backers that don't trace to the research corpus, WITHOUT flagging grounded figures or precision traps (years, protocol names, block times, version strings). Read-only by default — records, never mutates."""

from __future__ import annotations

import pytest

from app.modules.newspaper import unsourced_specifics_gate as gate


def _trace(*texts: object) -> list[dict]:
    return [{"tool": "fetch_url", "arguments": {}, "result": t} for t in texts]


def _record_trace(tool: str, result: object) -> list[dict]:
    return [{"tool": tool, "arguments": {}, "result": result}]


# --------------------------------------------------------------------------- #
# the two real incidents must flag
# --------------------------------------------------------------------------- #
def test_flags_goplausible_fabricated_numbers() -> None:
    """A traction count fabricated over a corpus showing zero counters is flagged."""
    # corpus is what was actually fetched: counters at zero, no partners.
    corpus = _trace(
        "0K+ Credentials issued 0+ Agentic wallets 0+ Events & hackathons Partners & affiliations"
    )
    body = "The platform has issued credentials to over 1,000 issuers. It has run 70+ events and hackathons."
    claims = {f["claim"] for f in gate.find_unsourced_specifics(body, corpus)}
    assert "1,000" in claims
    assert any(c.startswith("70") for c in claims)


def test_flags_fabricated_named_partner() -> None:
    """A named partner/backer absent from the research corpus is flagged as an unsourced named claim."""
    corpus = _trace("GoPlausible ships MCP tooling. Integrations: Tinyman, Ultrade.")
    body = "GoPlausible maintains partnerships with Borderless Capital and the Algorand Foundation."
    named = {
        f["claim"] for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "named"
    }
    assert "Borderless Capital" in named


# --------------------------------------------------------------------------- #
# grounded specifics must NOT flag
# --------------------------------------------------------------------------- #
def test_grounded_count_passes() -> None:
    """A count figure that matches the research corpus is not flagged."""
    corpus = _trace("The DAO now counts 5,000 members after its latest drive.")
    body = "The DAO has grown to 5,000 members."
    assert gate.find_unsourced_specifics(body, corpus) == []


def test_price_and_tvl_currency_ignored() -> None:
    """Live market figures like price and TVL are ignored, not treated as checkable funding claims."""
    # Live market figures (price, TVL) come from live tools and are reformatted;
    # only a FUNDING event makes a $ figure a checkable claim, so these are out.
    corpus = _trace("nothing about money here")
    body = "DorkFi reports $206K TVL and a token price of $0.0838."
    assert gate.find_unsourced_specifics(body, corpus) == []


def test_flags_fabricated_funding() -> None:
    """A funding-round dollar figure absent from the corpus is flagged."""
    corpus = _trace("The team shipped a testnet vault.")  # no funding figure
    body = "The project raised $5M in a seed round led by unnamed backers."
    funding = {
        f["claim"] for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "funding"
    }
    assert "$5M" in funding


def test_grounded_funding_passes() -> None:
    """A funding figure that matches the research corpus is not flagged."""
    corpus = _trace("Announcement: the project raised $5M in seed funding this week.")
    body = "The project raised $5M in seed funding."
    assert [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "funding"] == []


def test_flags_fabricated_percentage_on_traction_noun() -> None:
    """A percentage attached to a traction noun and absent from the corpus is flagged."""
    corpus = _trace("The wallet launched a new onboarding flow.")
    body = "Fully 60% of users completed the new onboarding flow."
    pct = {
        f["claim"] for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "percent"
    }
    assert "60%" in pct


def test_grounded_percentage_passes() -> None:
    """A traction count fabricated over a corpus showing zero counters is flagged."""
    corpus = _trace("A survey found 60% of users completed onboarding.")
    body = "60% of users completed onboarding."
    assert [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "percent"] == []


def test_onchain_share_percentage_left_to_chain_entity_gate() -> None:
    # % of supply/market is on-chain data and chain_entity_gate's job — this gate
    # deliberately does not flag it (it was the dominant false-positive class).
    """A named partner/backer absent from the research corpus is flagged as an unsourced named claim."""
    corpus = _trace("no on-chain figures fetched")
    body = "A single address holds 40% of the supply and 5.5% of the market."
    assert [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "percent"] == []


def test_grounded_partner_passes() -> None:
    """A count figure that matches the research corpus is not flagged."""
    corpus = _trace("GoPlausible partners with Tinyman for AMM swaps.")
    body = "GoPlausible partners with Tinyman on decentralized trading."
    named = [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "named"]
    assert named == []


# --------------------------------------------------------------------------- #
# precision traps: these numbers are NOT traction claims
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "body",
    [
        "Algorand rebrands its homepage around a 2027 quantum-resilience push.",  # year
        "AlgoVoi ships a self-hosted x402 vault for agent payments.",  # protocol name
        "Algorand's ~2.8-second block times ensure deterministic confirmation.",  # block time
        "Agents authenticate via OAuth 2.2 and OIDC integration.",  # version string
        "The Codex plugin adds 122 Algorand-specific tools to the environment.",  # 'tools' not a traction noun
    ],
)
def test_non_traction_numbers_ignored(body: str) -> None:
    # empty corpus: if any of these flagged, it would flag here.
    """Live market figures like price and TVL are ignored, not treated as checkable funding claims."""
    assert gate.find_unsourced_specifics(body, _trace("")) == []


@pytest.mark.parametrize(
    "body",
    [
        "The 2019 validators secured the launch.",  # year beside a count noun
        "MyAlgo was sunset in 2023, and wallet users moved on.",
        "By 2026, the project had many contributors.",
    ],
)
def test_bare_year_not_a_count(body: str) -> None:
    """A funding-round dollar figure absent from the corpus is flagged."""
    assert gate.find_unsourced_specifics(body, _trace("")) == []


def test_comma_number_that_looks_like_year_still_checked() -> None:
    # "2,000 users" is a count (written with a separator), not the year 2000.
    """A funding figure that matches the research corpus is not flagged."""
    body = "It onboarded 2,000 users last quarter."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, _trace("no numbers"))]
    assert "2,000" in claims


def test_date_day_not_a_count() -> None:
    # "June 12, 2027 ... validators" — the day in a written date must not be
    # flagged by grabbing a nearby count noun across the date (real false
    # positive from the birthday-site session).
    """A percentage attached to a traction noun and absent from the corpus is flagged."""
    body = "A countdown to June 12, 2027 celebrates the network's validators."
    assert gate.find_unsourced_specifics(body, _trace("")) == []


def test_number_grounded_only_near_its_own_noun() -> None:
    # The digit-run "70" IS in the corpus, but only as an unrelated value (a
    # pixel size) — not near "events". A count is grounded only in context, so
    # this must still flag. (This is the exact flaw the prod tuning pass found:
    # bare digit-run matching spuriously grounded GoPlausible's fabricated "70".)
    """A percentage figure that matches the research corpus is not flagged."""
    corpus = _trace("Hero image uses a 70px margin. The site lists 0+ events.")
    body = "The project has run 70 events this year."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, corpus)]
    assert "70" in claims


def test_number_grounded_when_near_noun_in_corpus() -> None:
    """An on-chain supply/market-share percentage is deliberately left ungated here, for chain_entity_gate to handle."""
    corpus = _trace("The platform reports 1,200 issuers onboarded to date.")
    body = "It now serves 1,200 issuers."
    assert gate.find_unsourced_specifics(body, corpus) == []


def test_digit_run_not_partial_matched() -> None:
    # "70" must NOT be considered grounded just because the corpus contains 1970.
    """A named partner already present in the research corpus is not flagged."""
    corpus = _trace("Founded reference to the year 1970 somewhere.")
    body = "The project counts 70 validators."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, corpus)]
    assert "70" in claims


# --------------------------------------------------------------------------- #
# gate wrapper: read-only records, enforce sets hold reason, disabled no-ops
# --------------------------------------------------------------------------- #
def test_flag_records_but_does_not_mutate_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-traction numbers (years, protocol names, block times, version strings) are never flagged."""
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENFORCE", False, raising=False)
    body = "It has over 1,000 issuers."
    payload = {"body": body}
    out = gate.flag_unsourced_specifics(payload, _trace("nothing relevant"))
    assert out["body"] == body  # unchanged
    assert out["_unsourced_specifics"]
    assert out["_unsourced_specifics"][0]["claim"] == "1,000"
    assert "_unsourced_hold_reason" not in out  # read-only


def test_flag_enforce_sets_hold_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare year beside a count noun is not mistaken for a count claim."""
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENFORCE", True, raising=False)
    payload = {"body": "It has over 1,000 issuers."}
    out = gate.flag_unsourced_specifics(payload, _trace("nothing relevant"))
    assert "1,000" in out["_unsourced_hold_reason"]


def test_flag_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A comma-separated number that resembles a year is still checked as a count."""
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", False, raising=False)
    payload = {"body": "It has over 1,000 issuers."}
    assert "_unsourced_specifics" not in gate.flag_unsourced_specifics(payload, _trace(""))


def test_revision_issues_name_each_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    """A day-of-month inside a written date is not mistaken for a count near a nearby noun."""
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    body = "It has over 1,000 issuers and partners with Borderless Capital."
    issues = gate.unsourced_specifics_revision_issues(body, _trace("nothing relevant"))
    joined = " ".join(issues)
    assert "1,000" in joined
    assert "Borderless Capital" in joined
    # instruction tells the writer to remove/correct, not to fetch (reviser has no tools)
    assert "remove it" in joined
    assert "counter reads 0" in joined


def test_revision_issues_empty_when_grounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A digit run is grounded only when it appears near its own noun in the corpus, not any nearby number."""
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    body = "It has 1,200 issuers."
    assert gate.unsourced_specifics_revision_issues(body, _trace("reported 1,200 issuers")) == []


def test_revision_issues_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A number matching the corpus only in an unrelated context is still grounded by proximity, so this case still flags."""
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", False, raising=False)
    body = "It has over 1,000 issuers."
    assert gate.unsourced_specifics_revision_issues(body, _trace("")) == []


# --------------------------------------------------------------------------- #
# proximity-window false positives: a number must ground against the noun it
# ACTUALLY modifies, not just any traction word floating nearby
# --------------------------------------------------------------------------- #
def test_number_not_misattributed_to_unrelated_farther_noun() -> None:
    """Root-caused production false positive: '42' modifies 'regions' (not a tracked traction noun), not the unrelated 'users' three words later in the same sentence. The old proximity window treated ±3 tokens as one flat bag and grabbed 'users' anyway, walking straight past 'regions' -- its real head noun -- to report a fabricated '42 users' claim. '42' must not be treated as a traction claim on 'users' at all here."""
    corpus = _trace("The service runs in 42 regions worldwide.")
    body = "Available in 42 regions for users worldwide."
    claims = [f for f in gate.find_unsourced_specifics(body, corpus) if f["claim"].startswith("42")]
    assert claims == []


def test_number_not_misattributed_across_a_preposition_into_a_different_clause() -> None:
    """Root-caused production false positive: '15' (correctly sourced from a tool call reporting 15 online) sits right after the preposition 'with', which starts its own clause -- the old backward scan crossed 'with' anyway to grab 'members' from the UNRELATED '320 members' earlier in the sentence, and then flagged '15' as an unsourced '15 members' claim (the real, grounded fact was 15 online, not 15 members). '15' must not be attributed to 'members' across that clause boundary."""
    corpus = _trace('{"member_count": 320, "online_count": 15}')
    body = "The Discord server has 320 members, with 15 online right now."
    claims = [f for f in gate.find_unsourced_specifics(body, corpus) if f["claim"].startswith("15")]
    assert claims == []


def test_percent_of_noun_still_crosses_the_determiner() -> None:
    """The fix must not break the standard, unambiguous 'N% of NOUN' construction -- 'of' is a safe forward crossing, unlike a clause-starting preposition like 'with'."""
    corpus = _trace("no survey data fetched")
    pct = {
        f["claim"]
        for f in gate.find_unsourced_specifics("Fully 60% of users completed onboarding.", corpus)
        if f["kind"] == "percent"
    }
    assert "60%" in pct


# --------------------------------------------------------------------------- #
# record-attributed values (Step B, 2026-09-09 AlgoChess incident)
# --------------------------------------------------------------------------- #
def test_flags_fabricated_record_attributed_rating() -> None:
    """The exact AlgoChess incident shape.

    A settlement transaction's note field is claimed to carry player ratings
    it never actually contained.
    """
    trace = _record_trace(
        "lookup_transaction_note", {"note": "AC1|duel|0-1|timeout|<addr1>|<addr2>|<moves>"}
    )
    body = (
        "The note field carries each player's rating before and after the match — "
        "one falling from 1748 to 1643, the other rising from 1666 to 1727."
    )
    claims = {
        f["claim"] for f in gate.find_unsourced_specifics(body, trace) if f["kind"] == "record"
    }
    assert {"1748", "1643", "1666", "1727"} <= claims


def test_grounded_record_value_passes() -> None:
    """A record-attributed figure that genuinely appears in the record tool's own result is not flagged."""
    trace = _record_trace("lookup_arc69_metadata", {"attributes": {"power_level": 4200}})
    body = "The ARC-69 metadata shows the item is at power level 4200."
    assert [f for f in gate.find_unsourced_specifics(body, trace) if f["kind"] == "record"] == []


def test_record_check_ignores_page_copy_and_admin_text() -> None:
    """A record-attributed claim must be checkable against the record tool's OWN result only -- page copy or admin-supplied text must never "confirm" what a specific record actually contains."""
    trace = _record_trace("lookup_transaction_note", {"note": "no rating field present"})
    body = "The transaction note shows a rating of 1748."
    findings = gate.find_unsourced_specifics(
        body, trace, extra_texts=["Some unrelated page mentions the number 1748 elsewhere."]
    )
    assert any(f["kind"] == "record" and f["claim"] == "1748" for f in findings)


def test_record_check_ignores_non_attribution_sentences() -> None:
    """A sentence with no record-attribution phrase is out of scope for this check, even with an ungrounded number."""
    trace = _record_trace("lookup_transaction_note", {"note": "nothing numeric here"})
    body = "The project has grown a lot this year, reaching new heights."
    assert [f for f in gate.find_unsourced_specifics(body, trace) if f["kind"] == "record"] == []


def test_record_check_skips_bare_years() -> None:
    """A bare 4-digit year inside a record-attribution sentence is a date, not a record value."""
    trace = _record_trace("lookup_transaction_note", {"note": "no year mentioned"})
    body = "The transaction memo references the 2019 protocol upgrade."
    assert [f for f in gate.find_unsourced_specifics(body, trace) if f["kind"] == "record"] == []


def test_flagged_specific_does_not_ground_itself_on_a_later_pass() -> None:
    r"""Root-cause regression (2026-09-09, AlgoChess incident).

    llm_compose._record_grade appends the pass-1 review_draft verdict to the
    SAME trace list a pass-2 check reads -- that verdict's issues list
    quotes the exact figure it just flagged (e.g. 'the figure "1,748" does
    not appear...'). Before the fix, this quoted citation text grounded the
    claim on pass 2, so a hard-enforcement hold could never actually fire on
    anything flagged even once earlier. It must still flag on pass 2.
    """
    trace: list = [{"tool": "fetch_url", "arguments": {}, "result": {"body": "no user data"}}]
    body = "It has grown to 1,748 users."

    pass1 = gate.find_unsourced_specifics(body, trace)
    claims = {f["claim"] for f in pass1}
    assert "1,748" in claims

    # Simulate _record_grade: the pass-1 verdict is appended to the SAME trace,
    # by reference, quoting the exact flagged figure in its issues list.
    trace.append(
        {
            "tool": "review_draft",
            "arguments": {},
            "result": {
                "issues": [
                    f'unsourced specific: the figure "{f["claim"]}" does not appear '
                    "in your research"
                    for f in pass1
                ]
            },
        }
    )

    pass2 = gate.find_unsourced_specifics(body, trace)
    pass2_claims = {f["claim"] for f in pass2}
    assert "1,748" in pass2_claims


def test_clean_body_no_findings() -> None:
    """A digit run is not considered grounded just because it partially matches a longer number in the corpus."""
    corpus = _trace("Pera and Defly are the leading wallets.")
    payload = {"body": "Pera and Defly are the leading Algorand wallets."}
    out = gate.flag_unsourced_specifics(payload, corpus)
    assert "_unsourced_specifics" not in out
