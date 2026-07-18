"""Authority gate: unattributed appeals to authority are unattributable by
construction — a real claim has a citable source in the writer's own trace.

Root incident (2026-07-18, quantum-rebrand draft caught pre-release): the
writer asserted 'industry-wide research suggests that Falcon signatures can
be 10-100x slower to verify than classical ECC signatures' — a fabricated
benchmark, wrong in direction (Falcon verification is fast; signing is the
costly op), laundered through authority no reader can check."""

from __future__ import annotations

from app.modules.newspaper.authority_gate import (
    authority_revision_issues,
    excise_unattributed_authority,
    find_unattributed_authority,
)

INCIDENT_SENTENCE = (
    "While the Foundation has not disclosed specific benchmarks, "
    "industry-wide research suggests that Falcon signatures can be 10-100x "
    "slower to verify than classical ECC signatures."
)


def test_finds_the_real_incident_phrase():
    assert find_unattributed_authority(INCIDENT_SENTENCE) == [
        "industry-wide research"
    ]


def test_finds_common_weasel_constructions():
    body = (
        "Experts say the merge is risky. Analysts believe fees will rise. "
        "Studies show adoption lags. It is widely believed that quantum "
        "computers are near. Sources say a deal closed."
    )
    found = find_unattributed_authority(body)
    assert "experts say" in found
    assert "analysts believe" in found
    assert "studies show" in found
    assert "it is widely believed" in found
    assert "sources say" in found


def test_named_attribution_and_plain_nouns_are_not_flagged():
    body = (
        "According to NIST's 2024 standard, Falcon was selected for "
        "signatures. The Foundation's roadmap states native accounts land in "
        "Q3 2026. Other ecosystems have built on Algorand's published "
        "research on VRF implementations. The research paper presents a "
        "specification."
    )
    assert find_unattributed_authority(body) == []


def test_revision_issue_names_phrase_and_demands_source_or_deletion():
    issues = authority_revision_issues(INCIDENT_SENTENCE)
    assert len(issues) == 1
    assert "'industry-wide research'" in issues[0]
    assert "delete the claim" in issues[0]


def test_excision_removes_only_the_offending_sentence():
    body = (
        "## Risks\n\n"
        "Falcon signatures are large. " + INCIDENT_SENTENCE + " For a "
        "blockchain targeting 10,000+ TPS, size is the constraint.\n\n"
        "| Concept | Implication |\n|---|---|\n| Size | More block space |\n"
    )
    payload = {"body": body}
    out = excise_unattributed_authority(payload)
    assert "industry-wide research" not in out["body"]
    # Neighbors and structure survive.
    assert "Falcon signatures are large." in out["body"]
    assert "size is the constraint" in out["body"]
    assert "## Risks" in out["body"]
    assert "| Size | More block space |" in out["body"]
    assert out["_authority_removed"] == [INCIDENT_SENTENCE]


def test_structure_lines_never_touched_even_if_matching():
    body = "## Experts say\n\n- experts say this bullet stays\n\nClean prose."
    out = excise_unattributed_authority({"body": body})
    # Headings/lists are structural; the gate only edits prose sentences.
    assert out["body"] == body
    assert "_authority_removed" not in out


def test_clean_body_untouched():
    payload = {"body": "The Foundation's own table lists 1,793-byte keys."}
    out = excise_unattributed_authority(payload)
    assert out is payload
    assert "_authority_removed" not in out


def test_gate_respects_disable_flag(monkeypatch):
    monkeypatch.setattr("app.core.config.AUTHORITY_GATE_ENABLED", False)
    payload = {"body": INCIDENT_SENTENCE}
    out = excise_unattributed_authority(payload)
    assert out["body"] == INCIDENT_SENTENCE
