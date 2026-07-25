"""The unsourced-specifics gate: flag hard traction/funding numbers and named partners/backers that don't trace to the research corpus, WITHOUT flagging grounded figures or precision traps (years, protocol names, block times, version strings). Read-only by default — records, never mutates."""

from __future__ import annotations

import pytest

from app.modules.newspaper import unsourced_specifics_gate as gate


def _trace(*texts: object) -> list[dict]:
    return [{"role": "tool", "name": "fetch_url", "content": t} for t in texts]


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
    corpus = _trace("A survey found 60% of users completed onboarding.")
    body = "60% of users completed onboarding."
    assert [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "percent"] == []


def test_onchain_share_percentage_left_to_chain_entity_gate() -> None:
    # % of supply/market is on-chain data and chain_entity_gate's job — this gate
    # deliberately does not flag it (it was the dominant false-positive class).
    corpus = _trace("no on-chain figures fetched")
    body = "A single address holds 40% of the supply and 5.5% of the market."
    assert [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "percent"] == []


def test_grounded_partner_passes() -> None:
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
    assert gate.find_unsourced_specifics(body, _trace("")) == []


def test_comma_number_that_looks_like_year_still_checked() -> None:
    # "2,000 users" is a count (written with a separator), not the year 2000.
    body = "It onboarded 2,000 users last quarter."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, _trace("no numbers"))]
    assert "2,000" in claims


def test_date_day_not_a_count() -> None:
    # "June 12, 2027 ... validators" — the day in a written date must not be
    # flagged by grabbing a nearby count noun across the date (real false
    # positive from the birthday-site session).
    body = "A countdown to June 12, 2027 celebrates the network's validators."
    assert gate.find_unsourced_specifics(body, _trace("")) == []


def test_number_grounded_only_near_its_own_noun() -> None:
    # The digit-run "70" IS in the corpus, but only as an unrelated value (a
    # pixel size) — not near "events". A count is grounded only in context, so
    # this must still flag. (This is the exact flaw the prod tuning pass found:
    # bare digit-run matching spuriously grounded GoPlausible's fabricated "70".)
    corpus = _trace("Hero image uses a 70px margin. The site lists 0+ events.")
    body = "The project has run 70 events this year."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, corpus)]
    assert "70" in claims


def test_number_grounded_when_near_noun_in_corpus() -> None:
    corpus = _trace("The platform reports 1,200 issuers onboarded to date.")
    body = "It now serves 1,200 issuers."
    assert gate.find_unsourced_specifics(body, corpus) == []


def test_digit_run_not_partial_matched() -> None:
    # "70" must NOT be considered grounded just because the corpus contains 1970.
    corpus = _trace("Founded reference to the year 1970 somewhere.")
    body = "The project counts 70 validators."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, corpus)]
    assert "70" in claims


# --------------------------------------------------------------------------- #
# gate wrapper: read-only records, enforce sets hold reason, disabled no-ops
# --------------------------------------------------------------------------- #
def test_flag_records_but_does_not_mutate_body(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENFORCE", True, raising=False)
    payload = {"body": "It has over 1,000 issuers."}
    out = gate.flag_unsourced_specifics(payload, _trace("nothing relevant"))
    assert "1,000" in out["_unsourced_hold_reason"]


def test_flag_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", False, raising=False)
    payload = {"body": "It has over 1,000 issuers."}
    assert "_unsourced_specifics" not in gate.flag_unsourced_specifics(payload, _trace(""))


def test_revision_issues_name_each_specific(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    body = "It has 1,200 issuers."
    assert gate.unsourced_specifics_revision_issues(body, _trace("reported 1,200 issuers")) == []


def test_revision_issues_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", False, raising=False)
    body = "It has over 1,000 issuers."
    assert gate.unsourced_specifics_revision_issues(body, _trace("")) == []


def test_clean_body_no_findings() -> None:
    corpus = _trace("Pera and Defly are the leading wallets.")
    payload = {"body": "Pera and Defly are the leading Algorand wallets."}
    out = gate.flag_unsourced_specifics(payload, corpus)
    assert "_unsourced_specifics" not in out
