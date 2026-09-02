"""Unit tests for checks/fuzzy_hash.py's ssdeep output parsing.

Deliberately excludes anything that shells out to the real `ssdeep` binary
-- not available in the no-network pytest venv (CLAUDE.md section 6: tests
fake the seam, never the real subprocess/network), same rationale as
test_scan_pure_logic.py's clamscan/file exclusions and test_yara_scan.py's
yara exclusion. `run()` itself is exercised via a monkeypatch of
subprocess.run using stdout captured from a real `ssdeep` CLI invocation
(Alpine 3.20, ssdeep 2.14.1) so the parsing logic gets real coverage without
a real binary in this venv.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.x402_scan.sandbox.checks import fuzzy_hash

# Real `ssdeep -b <path>` stdout, captured against Alpine 3.20's ssdeep 2.14.1.
_REAL_STDOUT = (
    'ssdeep,1.1--blocksize:hash:hash,filename\n3:iKFSMPpvRujQrM2IHYDR:rJPpJaA2HYDR,"a.txt"\n'
)
_REAL_STDOUT_LARGER = (
    "ssdeep,1.1--blocksize:hash:hash,filename\n"
    '192:znnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnb:n,"/tmp/big.bin"\n'
)


def test_parse_ssdeep_output_extracts_bare_signature() -> None:
    """The blocksize:hash:hash signature is split off from the trailing quoted filename."""
    signature = fuzzy_hash._parse_ssdeep_output(_REAL_STDOUT)
    assert signature == "3:iKFSMPpvRujQrM2IHYDR:rJPpJaA2HYDR"


def test_parse_ssdeep_output_handles_larger_file_signature() -> None:
    """A different blocksize/hash length still parses correctly."""
    signature = fuzzy_hash._parse_ssdeep_output(_REAL_STDOUT_LARGER)
    assert signature == ("192:znnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnb:n")


def test_parse_ssdeep_output_returns_none_for_header_only_output() -> None:
    """Only the CSV header line (no signature line at all) parses to None, not a bogus value."""
    header_only = "ssdeep,1.1--blocksize:hash:hash,filename\n"
    assert fuzzy_hash._parse_ssdeep_output(header_only) is None


def test_parse_ssdeep_output_returns_none_for_empty_stdout() -> None:
    """No output at all parses to None."""
    assert fuzzy_hash._parse_ssdeep_output("") is None


def test_run_returns_hash_and_no_risk_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful run reports the ssdeep hash and explicitly never sets risk_score (v0: no corpus)."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=_REAL_STDOUT, stderr="")

    monkeypatch.setattr(fuzzy_hash.subprocess, "run", fake_run)
    result = fuzzy_hash.run("/scan/input")
    assert result["ssdeep_hash"] == "3:iKFSMPpvRujQrM2IHYDR:rJPpJaA2HYDR"
    assert result["comparison_corpus"] is None
    assert "risk_score" not in result
    assert "caution_notes" not in result


def test_run_tiny_file_still_produces_a_signature_despite_stderr_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file too small for a 'meaningful' ssdeep comparison still yields a usable low-confidence hash."""
    tiny_stdout = 'ssdeep,1.1--blocksize:hash:hash,filename\n3:wn:wn,"/tmp/tiny.txt"\n'

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=tiny_stdout,
            stderr="ssdeep: Did not process files large enough to produce meaningful results",
        )

    monkeypatch.setattr(fuzzy_hash.subprocess, "run", fake_run)
    result = fuzzy_hash.run("/scan/input")
    assert result["ssdeep_hash"] == "3:wn:wn"
    assert "error" not in result


def test_run_missing_binary_returns_error_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ssdeep` not being installed must surface as {"error": ...}, never raise or false-report clean."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise FileNotFoundError("ssdeep: not found")

    monkeypatch.setattr(fuzzy_hash.subprocess, "run", fake_run)
    result = fuzzy_hash.run("/scan/input")
    assert "error" in result
    assert "risk_score" not in result


def test_run_unparseable_output_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garbage/unexpected stdout surfaces as {"error": ...} rather than a fabricated hash."""

    def fake_run(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="some unexpected failure")

    monkeypatch.setattr(fuzzy_hash.subprocess, "run", fake_run)
    result = fuzzy_hash.run("/scan/input")
    assert "error" in result
    assert "some unexpected failure" in result["error"]
