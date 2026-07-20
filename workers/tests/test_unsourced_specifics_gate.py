"""The unsourced-specifics gate: flag hard traction/funding numbers and named
partners/backers that don't trace to the research corpus, WITHOUT flagging
grounded figures or precision traps (years, protocol names, block times, version
strings). Read-only by default — records, never mutates."""

from __future__ import annotations

import pytest

from app.modules.newspaper import unsourced_specifics_gate as gate


def _trace(*texts):
    return [{"role": "tool", "name": "fetch_url", "content": t} for t in texts]


# --------------------------------------------------------------------------- #
# the two real incidents must flag
# --------------------------------------------------------------------------- #
def test_flags_goplausible_fabricated_numbers():
    # corpus is what was actually fetched: counters at zero, no partners.
    corpus = _trace("0K+ Credentials issued 0+ Agentic wallets 0+ Events & hackathons "
                    "Partners & affiliations")
    body = "The platform has issued credentials to over 1,000 issuers. It has run 70+ events and hackathons."
    claims = {f["claim"] for f in gate.find_unsourced_specifics(body, corpus)}
    assert "1,000" in claims
    assert any(c.startswith("70") for c in claims)


def test_flags_fabricated_named_partner():
    corpus = _trace("GoPlausible ships MCP tooling. Integrations: Tinyman, Ultrade.")
    body = "GoPlausible maintains partnerships with Borderless Capital and the Algorand Foundation."
    named = {f["claim"] for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "named"}
    assert "Borderless Capital" in named


# --------------------------------------------------------------------------- #
# grounded specifics must NOT flag
# --------------------------------------------------------------------------- #
def test_grounded_count_passes():
    corpus = _trace("The DAO now counts 5,000 members after its latest drive.")
    body = "The DAO has grown to 5,000 members."
    assert gate.find_unsourced_specifics(body, corpus) == []


def test_currency_out_of_scope():
    # $ figures are live market/TVL data (reformatted → false positives) and
    # neither incident involved currency: v1 ignores them entirely.
    corpus = _trace("nothing about money here")
    body = "DorkFi reports $206K TVL and a token price of $0.0838."
    assert [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "numeric"] == []


def test_grounded_partner_passes():
    corpus = _trace("GoPlausible partners with Tinyman for AMM swaps.")
    body = "GoPlausible partners with Tinyman on decentralized trading."
    named = [f for f in gate.find_unsourced_specifics(body, corpus) if f["kind"] == "named"]
    assert named == []


# --------------------------------------------------------------------------- #
# precision traps: these numbers are NOT traction claims
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", [
    "Algorand rebrands its homepage around a 2027 quantum-resilience push.",   # year
    "AlgoVoi ships a self-hosted x402 vault for agent payments.",              # protocol name
    "Algorand's ~2.8-second block times ensure deterministic confirmation.",   # block time
    "Agents authenticate via OAuth 2.2 and OIDC integration.",                 # version string
    "The Codex plugin adds 122 Algorand-specific tools to the environment.",   # 'tools' not a traction noun
])
def test_non_traction_numbers_ignored(body):
    # empty corpus: if any of these flagged, it would flag here.
    assert gate.find_unsourced_specifics(body, _trace("")) == []


def test_number_grounded_only_near_its_own_noun():
    # The digit-run "70" IS in the corpus, but only as an unrelated value (a
    # pixel size) — not near "events". A count is grounded only in context, so
    # this must still flag. (This is the exact flaw the prod tuning pass found:
    # bare digit-run matching spuriously grounded GoPlausible's fabricated "70".)
    corpus = _trace("Hero image uses a 70px margin. The site lists 0+ events.")
    body = "The project has run 70 events this year."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, corpus)]
    assert "70" in claims


def test_number_grounded_when_near_noun_in_corpus():
    corpus = _trace("The platform reports 1,200 issuers onboarded to date.")
    body = "It now serves 1,200 issuers."
    assert gate.find_unsourced_specifics(body, corpus) == []


def test_digit_run_not_partial_matched():
    # "70" must NOT be considered grounded just because the corpus contains 1970.
    corpus = _trace("Founded reference to the year 1970 somewhere.")
    body = "The project counts 70 validators."
    claims = [f["claim"] for f in gate.find_unsourced_specifics(body, corpus)]
    assert "70" in claims


# --------------------------------------------------------------------------- #
# gate wrapper: read-only records, enforce sets hold reason, disabled no-ops
# --------------------------------------------------------------------------- #
def test_flag_records_but_does_not_mutate_body(monkeypatch):
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENFORCE", False, raising=False)
    body = "It has over 1,000 issuers."
    payload = {"body": body}
    out = gate.flag_unsourced_specifics(payload, _trace("nothing relevant"))
    assert out["body"] == body  # unchanged
    assert out["_unsourced_specifics"] and out["_unsourced_specifics"][0]["claim"] == "1,000"
    assert "_unsourced_hold_reason" not in out  # read-only


def test_flag_enforce_sets_hold_reason(monkeypatch):
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENFORCE", True, raising=False)
    payload = {"body": "It has over 1,000 issuers."}
    out = gate.flag_unsourced_specifics(payload, _trace("nothing relevant"))
    assert "1,000" in out["_unsourced_hold_reason"]


def test_flag_noop_when_disabled(monkeypatch):
    monkeypatch.setattr("app.core.config.UNSOURCED_SPECIFICS_GATE_ENABLED", False, raising=False)
    payload = {"body": "It has over 1,000 issuers."}
    assert "_unsourced_specifics" not in gate.flag_unsourced_specifics(payload, _trace(""))


def test_clean_body_no_findings():
    corpus = _trace("Pera and Defly are the leading wallets.")
    payload = {"body": "Pera and Defly are the leading Algorand wallets."}
    out = gate.flag_unsourced_specifics(payload, corpus)
    assert "_unsourced_specifics" not in out
