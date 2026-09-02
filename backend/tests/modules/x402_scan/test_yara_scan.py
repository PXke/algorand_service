"""Unit tests for checks/yara_scan.py's pure parsing/scoring logic.

Deliberately excludes anything that shells out to the real `yara` binary or
needs a compiled signature-base ruleset -- neither is available in the
no-network pytest venv (CLAUDE.md section 6: tests fake the seam, never the
real subprocess/network), same rationale as test_scan_pure_logic.py's
clamscan/file exclusions. `run()` itself is exercised only via a monkeypatch
of subprocess.run so the yara-output-parsing and risk-scoring logic gets
real coverage without a real binary.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.x402_scan.sandbox.checks import yara_scan


def test_parse_meta_handles_quoted_value_with_embedded_comma() -> None:
    """A description containing a comma must not split into two bogus meta entries."""
    meta = yara_scan._parse_meta('description="APT trojan, variant B",score=80')
    assert meta == {"description": "APT trojan, variant B", "score": "80"}


def test_parse_meta_handles_escaped_quote() -> None:
    """An escaped quote inside a quoted meta value is unescaped, not treated as the closing quote."""
    meta = yara_scan._parse_meta(r'description="says \"hello\""')
    assert meta["description"] == 'says "hello"'


def test_parse_meta_handles_bareword_and_empty() -> None:
    """Unquoted (int/bool) values parse as plain strings; an empty meta block yields {}."""
    assert yara_scan._parse_meta("score=70,nodeepdive=1") == {"score": "70", "nodeepdive": "1"}
    assert yara_scan._parse_meta("") == {}


def test_parse_yara_output_single_match_with_meta() -> None:
    """A well-formed `-m` output line parses into rule name + meta dict."""
    stdout = 'Mimikatz_Memory_Rule_1 [score=70,description="Detects mimikatz"] /scan/input\n'
    matches = yara_scan._parse_yara_output(stdout)
    assert len(matches) == 1
    assert matches[0]["rule"] == "Mimikatz_Memory_Rule_1"
    assert matches[0]["meta"]["score"] == "70"
    assert matches[0]["meta"]["description"] == "Detects mimikatz"


def test_parse_yara_output_multiple_matches() -> None:
    """Multiple matched rules each produce their own entry, in order."""
    stdout = (
        'Rule_A [score=60] /scan/input\nRule_B [score=90,description="specific hit"] /scan/input\n'
    )
    matches = yara_scan._parse_yara_output(stdout)
    assert [m["rule"] for m in matches] == ["Rule_A", "Rule_B"]


def test_parse_yara_output_ignores_blank_lines() -> None:
    """Blank lines in stdout are skipped, not turned into a bogus empty-rule match."""
    stdout = "\nRule_A [score=60] /scan/input\n\n"
    matches = yara_scan._parse_yara_output(stdout)
    assert len(matches) == 1


def test_parse_yara_output_keeps_unparseable_line_rather_than_dropping_it() -> None:
    """An unexpected line shape is kept as a low-detail match, never silently discarded."""
    matches = yara_scan._parse_yara_output("something unexpected here")
    assert len(matches) == 1
    assert matches[0]["rule"] == "something unexpected here"
    assert matches[0]["meta"] == {}


def test_parse_yara_output_empty_stdout_is_no_matches() -> None:
    """A clean scan (no matches) parses to an empty list."""
    assert yara_scan._parse_yara_output("") == []


def test_risk_score_uses_score_meta_normalized_from_0_100() -> None:
    """A rule with score=80 normalizes to 0.8 (within the clamp range)."""
    score = yara_scan._risk_score_for_match("SomeRule", {"score": "80"})
    assert score == 0.8


def test_risk_score_clamps_high_score_below_confirmed_clamav_certainty() -> None:
    """Even a max signature-base score never reaches the 1.0 ClamAV "confirmed" ceiling."""
    score = yara_scan._risk_score_for_match("SomeRule", {"score": "100"})
    assert score <= yara_scan._SCORE_META_MAX
    assert score < 1.0


def test_risk_score_clamps_low_score_to_a_floor() -> None:
    """A very low declared score still earns a non-trivial floor -- a real match is a real signal."""
    score = yara_scan._risk_score_for_match("SomeRule", {"score": "5"})
    assert score == yara_scan._SCORE_META_MIN


def test_risk_score_without_score_meta_uses_unscored_default() -> None:
    """A specific (non-generic-prefixed) rule with no score meta gets the unscored default."""
    score = yara_scan._risk_score_for_match("Apt_Chinachopper_Webshell", {})
    assert score == yara_scan._UNSCORED_MATCH_RISK


def test_risk_score_generic_prefixed_rule_without_score_is_lower_confidence() -> None:
    """A gen_-prefixed rule with no score meta is scored lower than a specific unscored rule."""
    generic_score = yara_scan._risk_score_for_match("gen_suspicious_pattern", {})
    specific_score = yara_scan._risk_score_for_match("apt_specific_family", {})
    assert generic_score == yara_scan._GENERIC_UNSCORED_MATCH_RISK
    assert generic_score < specific_score


def test_note_for_match_includes_description_when_present() -> None:
    """A caution note includes the rule's description meta when present."""
    note = yara_scan._note_for_match({"rule": "Rule_A", "meta": {"description": "does bad things"}})
    assert note == "YARA match: Rule_A -- does bad things"


def test_note_for_match_omits_dash_when_no_description() -> None:
    """A caution note falls back to just the rule name when no description meta is present."""
    note = yara_scan._note_for_match({"rule": "Rule_A", "meta": {}})
    assert note == "YARA match: Rule_A"


def test_run_no_matches_has_no_risk_score_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean scan (exit 0, empty stdout) returns a plain dict with no risk_score key at all."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(yara_scan.subprocess, "run", fake_run)
    result = yara_scan.run("/scan/input")
    assert result == {"matched_rules": 0}
    assert "risk_score" not in result
    assert "caution_notes" not in result


def test_run_with_matches_reports_risk_score_and_caution_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real match surfaces matched_rules, risk_score (max across matches), and caution_notes."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        stdout = (
            "gen_suspicious [score=50] /scan/input\n"
            'apt_specific_family [score=90,description="known APT loader"] /scan/input\n'
        )
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(yara_scan.subprocess, "run", fake_run)
    result = yara_scan.run("/scan/input")
    assert result["matched_rules"] == 2
    assert result["risk_score"] == 0.9  # the max of the two match scores
    assert any("known APT loader" in note for note in result["caution_notes"])


def test_run_missing_binary_returns_error_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """`yara` not being installed must surface as {"error": ...}, never raise or false-report clean."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise FileNotFoundError("yara: not found")

    monkeypatch.setattr(yara_scan.subprocess, "run", fake_run)
    result = yara_scan.run("/scan/input")
    assert "error" in result
    assert "risk_score" not in result


def test_run_nonzero_exit_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A yara invocation error (bad/missing compiled ruleset) surfaces as {"error": ...}."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="could not open rules file")

    monkeypatch.setattr(yara_scan.subprocess, "run", fake_run)
    result = yara_scan.run("/scan/input")
    assert "error" in result
    assert "could not open rules file" in result["error"]


def test_run_truncates_matches_at_the_reported_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pathological number of matches is capped, not left unbounded in the report."""
    lines = "\n".join(
        f"Rule_{i} [score=60] /scan/input" for i in range(yara_scan.MAX_REPORTED_MATCHES + 10)
    )

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=lines, stderr="")

    monkeypatch.setattr(yara_scan.subprocess, "run", fake_run)
    result = yara_scan.run("/scan/input")
    assert result["matched_rules"] == yara_scan.MAX_REPORTED_MATCHES + 10
    assert len(result["matches"]) == yara_scan.MAX_REPORTED_MATCHES
    assert len(result["caution_notes"]) == yara_scan.MAX_REPORTED_MATCHES
