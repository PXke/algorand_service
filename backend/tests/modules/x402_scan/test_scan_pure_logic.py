"""Unit tests for the sandbox's pure check logic (entropy, indicator extraction, archive-bomb guard, risk aggregation).

Deliberately excludes anything that shells out (clamscan, file) or touches
Docker -- those are exercised manually against the built sandbox image
(EICAR test string), not in the no-network pytest suite (CLAUDE.md section
6: tests fake the seam, never the real subprocess/network).
"""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from app.modules.x402_scan.sandbox import scan
from app.modules.x402_scan.sandbox.checks import archive, basic_analysis, clamav, risk_score


def _fake_proc(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_shannon_entropy_of_empty_bytes_is_zero() -> None:
    """No bytes carries no information."""
    assert basic_analysis._shannon_entropy(b"") == 0.0


def test_shannon_entropy_of_uniform_bytes_is_low() -> None:
    """One repeated byte value has near-zero entropy."""
    assert basic_analysis._shannon_entropy(b"aaaaaaaaaaaaaaaa") < 1.0


def test_shannon_entropy_of_random_bytes_is_high() -> None:
    """A full byte-value spread has entropy near the 8-bit ceiling (packed/encrypted signal)."""
    high_entropy = bytes(range(256)) * 4
    assert basic_analysis._shannon_entropy(high_entropy) > 7.5


def test_extract_indicators_finds_url_and_ip() -> None:
    """A URL and an IPv4 address embedded in the bytes are both surfaced."""
    data = b"reaching out to http://evil.example.com/exfil and 10.0.0.5 for c2"
    indicators = basic_analysis._extract_indicators(data)
    assert "http://evil.example.com/exfil" in indicators["urls"]
    assert "10.0.0.5" in indicators["ipv4_addresses"]


def test_extract_indicators_empty_on_clean_data() -> None:
    """Plain text with no URL/IP shape yields no indicators."""
    indicators = basic_analysis._extract_indicators(b"just some plain benign text, nothing here")
    assert indicators == {"urls": [], "ipv4_addresses": []}


def test_basic_analysis_flags_high_entropy_with_caution_note(tmp_path: Path) -> None:
    """A high-entropy file surfaces a non-zero risk_score and an explanatory caution note."""
    p = tmp_path / "packed.bin"
    p.write_bytes(bytes(range(256)) * 100)
    result = basic_analysis.run(str(p))
    assert result["risk_score"] > 0
    assert any("entropy" in note for note in result["caution_notes"])


def test_basic_analysis_benign_file_has_no_risk_score(tmp_path: Path) -> None:
    """A plain benign file carries no risk_score/caution_notes keys at all."""
    p = tmp_path / "benign.txt"
    p.write_text("just a normal file, nothing to see here")
    result = basic_analysis.run(str(p))
    assert "risk_score" not in result
    assert "caution_notes" not in result


def test_archive_member_plan_detects_zip(tmp_path: Path) -> None:
    """A real zip is recognized and its member sizes read from the header alone."""
    zpath = tmp_path / "a.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("hello.txt", "hi")
    kind, members = archive._member_plan(str(zpath))
    assert kind == "zip"
    assert members == [("hello.txt", 2)]


def test_archive_member_plan_detects_tar(tmp_path: Path) -> None:
    """A real tar is recognized and its member sizes read from the header alone."""
    tpath = tmp_path / "a.tar"
    inner = tmp_path / "hello.txt"
    inner.write_text("hi there")
    with tarfile.open(tpath, "w") as tf:
        tf.add(inner, arcname="hello.txt")
    kind, members = archive._member_plan(str(tpath))
    assert kind == "tar"
    assert members == [("hello.txt", 8)]


def test_archive_member_plan_none_for_plain_file(tmp_path: Path) -> None:
    """A non-archive file is not misidentified as zip or tar."""
    plain = tmp_path / "plain.txt"
    plain.write_text("not an archive")
    assert archive._member_plan(str(plain)) is None


def test_archive_run_returns_empty_dict_for_non_archive(tmp_path: Path) -> None:
    """run() short-circuits to {} (omitted from the report) for a non-archive target."""
    plain = tmp_path / "plain.txt"
    plain.write_text("not an archive")
    assert archive.run(str(plain)) == {}


def test_scan_archive_refuses_when_declared_size_exceeds_cap(tmp_path: Path) -> None:
    """The zip/tar-bomb guard trips on the archive's own declared size, before ever extracting."""
    huge_plan = [("payload.bin", archive.MAX_EXTRACT_BYTES + 1)]
    zpath = tmp_path / "does_not_need_to_exist.zip"
    report = archive._scan_archive_internal(str(zpath), "zip", huge_plan)
    assert report["extraction_skipped_reason"] is not None
    assert "zip/tar-bomb guard" in report["extraction_skipped_reason"]
    assert "clamav" not in report  # never reached extraction/scan


def test_scan_archive_refuses_when_member_count_exceeds_cap(tmp_path: Path) -> None:
    """The zip/tar-bomb guard also trips on an oversized member count alone."""
    huge_plan = [(f"f{i}.bin", 1) for i in range(archive.MAX_EXTRACT_MEMBERS + 1)]
    zpath = tmp_path / "does_not_need_to_exist.zip"
    report = archive._scan_archive_internal(str(zpath), "zip", huge_plan)
    assert report["extraction_skipped_reason"] is not None
    assert "clamav" not in report


def test_risk_score_takes_the_maximum_not_the_average() -> None:
    """One confirmed hit buried under clean checks must not be diluted into a low score."""
    report = {
        "clamav": {"risk_score": 1.0, "caution_notes": ["ClamAV signature match: X FOUND"]},
        "target": {"risk_score": 0.1, "caution_notes": ["minor note"]},
    }
    summary = risk_score.summarize(report)
    assert summary["score"] == 1.0
    assert summary["verdict"] == "likely malicious"
    assert summary["malicious"] is True
    assert "ClamAV signature match: X FOUND" in summary["caution_notes"]


def test_risk_score_no_concerns_when_nothing_flagged() -> None:
    """A report with no risk_score anywhere summarizes to a clean verdict."""
    summary = risk_score.summarize({"clamav": {"clean": True}, "target": {"size_bytes": 10}})
    assert summary["score"] == 0.0
    assert summary["verdict"] == "no concerns found"
    assert summary["malicious"] is False
    assert summary["caution_notes"] == []


def test_risk_score_malicious_boolean_matches_the_verdict_threshold() -> None:
    """Malicious is True iff score crosses the same 0.9 cutoff that produces "likely malicious" -- one source of truth, not two numbers that could drift apart."""
    just_under = risk_score.summarize({"x": {"risk_score": 0.89}})
    at_threshold = risk_score.summarize({"x": {"risk_score": 0.9}})

    assert just_under["verdict"] != "likely malicious"
    assert just_under["malicious"] is False
    assert at_threshold["verdict"] == "likely malicious"
    assert at_threshold["malicious"] is True


# --- checks/clamav.py: clamd fast-path / clamscan-fallback logic --------
#
# Never shells out to a real clamdscan/clamscan/docker here -- the seam
# (clamav._socket_present and subprocess.run) is monkeypatched instead, per
# CLAUDE.md section 6 (fake the seam, never mock the network) and this
# module's own real-daemon verification (EICAR against a live sidecar, and
# the no-sidecar/stale-socket fallback) done manually against the built
# image, documented in the accompanying report, not in this no-network suite.


def test_clamav_uses_clamdscan_when_socket_present_and_clean(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fast path: socket present, clamdscan reports clean -- clamscan is never invoked."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: True)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _fake_proc(0, stdout="")

    monkeypatch.setattr(clamav.subprocess, "run", fake_run)
    result = clamav.run("/scan/input")
    assert result == {"engine": "clamdscan", "exit_code": 0, "infected_files": [], "clean": True}
    assert len(calls) == 1
    assert calls[0][0] == "clamdscan"


def test_clamav_clamdscan_detects_infected_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fast path: a signature match yields risk_score 1.0 and a caution note."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: True)
    monkeypatch.setattr(
        clamav.subprocess,
        "run",
        lambda *_args, **_kwargs: _fake_proc(1, stdout="/scan/input: Eicar-Test-Signature FOUND\n"),
    )
    result = clamav.run("/scan/input")
    assert result["engine"] == "clamdscan"
    assert result["clean"] is False
    assert result["risk_score"] == 1.0
    assert (
        "ClamAV signature match: /scan/input: Eicar-Test-Signature FOUND" in result["caution_notes"]
    )


def test_clamav_falls_back_to_clamscan_when_socket_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """No socket file (sidecar not started/not mounted): degrades straight to clamscan, no error."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: False)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _fake_proc(0, stdout="")

    monkeypatch.setattr(clamav.subprocess, "run", fake_run)
    result = clamav.run("/scan/input")
    assert result["engine"] == "clamscan"
    assert result["clean"] is True
    assert len(calls) == 1  # clamdscan was never attempted
    assert calls[0][0] == "clamscan"


def test_clamav_falls_back_to_clamscan_when_daemon_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Socket present but clamd isn't answering: clamdscan's connection-error exit (2) must still degrade to clamscan, not error out or false-clean."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: True)
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[0] == "clamdscan":
            return _fake_proc(2, stderr="ERROR: Could not connect to clamd")
        return _fake_proc(1, stdout="/scan/input: Eicar-Test-Signature FOUND\n")

    monkeypatch.setattr(clamav.subprocess, "run", fake_run)
    result = clamav.run("/scan/input")
    assert result["engine"] == "clamscan"
    assert result["risk_score"] == 1.0
    assert [c[0] for c in calls] == ["clamdscan", "clamscan"]


def test_clamav_subprocess_exception_on_clamdscan_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clamdscan itself blowing up (e.g. timeout) must fall back to clamscan, never raise."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: True)

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "clamdscan":
            raise subprocess.TimeoutExpired(cmd, 30)
        return _fake_proc(0, stdout="")

    monkeypatch.setattr(clamav.subprocess, "run", fake_run)
    result = clamav.run("/scan/input")
    assert result["engine"] == "clamscan"
    assert result["clean"] is True


def test_clamav_returns_error_dict_not_false_clean_when_scanner_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scanner killed by a signal (e.g. OOM, returncode -9) must surface as an error, never a silent "clean" verdict."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: False)
    monkeypatch.setattr(
        clamav.subprocess, "run", lambda *_args, **_kwargs: _fake_proc(-9, stderr="killed")
    )
    result = clamav.run("/scan/input")
    assert "error" in result
    assert "clean" not in result
    assert result["risk_score"] == 0.0


def test_clamav_clamdscan_exception_bubbling_from_final_clamscan_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback clamscan call itself raising (e.g. binary missing) is caught, never raised."""
    monkeypatch.setattr(clamav, "_socket_present", lambda: False)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("clamscan: not found")

    monkeypatch.setattr(clamav.subprocess, "run", fake_run)
    result = clamav.run("/scan/input")
    assert "error" in result


# --- scan.py: orchestrator-level summary fields (2026-09-01 review feedback) ---


def test_scan_status_ok_when_no_check_carries_an_error() -> None:
    """A fully clean run (no check's own dict has an "error" key) reports status "ok"."""
    result = {"clamav": {"clean": True}, "yara": {"matched_rules": 0}}
    assert scan._scan_status(result) == "ok"


def test_scan_status_degraded_when_a_registered_check_failed() -> None:
    """One check reporting {"error": ...} at its own top level flips overall status to "degraded"."""
    result = {"clamav": {"clean": True}, "yara": {"error": "yara binary not found"}}
    assert scan._scan_status(result) == "degraded"


def test_scan_status_ignores_a_nested_sub_check_error() -> None:
    """An error nested inside an archive MEMBER (not a top-level check) does not flip the overall status."""
    result = {
        "clamav": {"clean": True},
        "archive": {"members": [{"label": "inner.bin", "error": "could not read"}]},
    }
    assert scan._scan_status(result) == "ok"


def test_one_line_summary_names_the_contributing_checks() -> None:
    """Only checks whose OWN dict carries a risk_score are named as indicators, using the friendly display name."""
    result = {
        "clamav": {"risk_score": 1.0, "clean": False},
        "yara": {"risk_score": 0.75, "matched_rules": 1},
        "target": {"type": "EICAR virus test files"},
    }
    risk = risk_score.summarize(result)

    summary = scan._one_line_summary(result, risk)

    assert "ClamAV" in summary
    assert "YARA" in summary
    assert "2 indicator" in summary
    assert "EICAR virus test files" in summary


def test_one_line_summary_says_no_indicators_when_clean() -> None:
    """A clean scan's one-liner says so plainly rather than an empty parenthetical."""
    result = {"clamav": {"clean": True}, "target": {"type": "ASCII text"}}
    risk = risk_score.summarize(result)

    summary = scan._one_line_summary(result, risk)

    assert "no indicators" in summary
    assert "No concerns found" in summary
