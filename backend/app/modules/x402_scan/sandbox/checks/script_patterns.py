"""Static shell/script malicious-pattern detection -- see package docstring.

Contract: see checks/__init__.py. Detects known-dangerous shell/script idioms
via TEXT/REGEX matching ONLY (fork bombs, curl-pipe-shell / wget-pipe-shell
remote-execute, `rm -rf /`, base64-decode-then-exec chains, reverse-shell
one-liners, crontab/`.bashrc` persistence) -- this module NEVER sources,
`eval`-s, imports, or otherwise runs any part of the target; every match is a
plain string/regex search over decoded bytes.

Only runs its pattern set when the file plausibly IS a script (a `#!` shebang
on the first line, or a `.sh`/`.bash`/`.py`/`.ps1`/`.js` extension) -- an
arbitrary binary that happens to contain a matching byte sequence by chance
must not misfire. Two extra guards narrow that further: (1) only the first
MAX_SCAN_BYTES of the file are decoded/scanned, and (2) every regex match is
re-checked against the source line(s) it came from -- if that line is mostly
non-printable bytes (the `errors="ignore"` UTF-8 decode having chewed through
binary noise), the match is discarded as a coincidental byte-sequence hit
rather than a real script idiom.

Unlike archive.py (which returns `{}` for a non-archive target so scan.py
omits the key) and image_bomb.py, this check always reports a decision:
`{"scanned": False, "reason": "..."}` when the heuristic says "not a script"
so the report shows this check ran and made a deliberate call, not that it
silently no-opped. This does not conflict with checks/__init__.py's contract
-- that contract only constrains the optional risk_score/caution_notes keys
and says nothing about the rest of the dict's shape.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

# Only decode/scan the first slice of a file -- scripts are small by nature;
# bounding this keeps worst-case regex cost bounded even against a huge
# upload, and keeps the "resembles a text line" check meaningful (see
# _looks_like_text below) rather than drowning in megabytes of noise.
MAX_SCAN_BYTES = 1 * 1024 * 1024

# Cap how many match locations we name per pattern so one pathological file
# (e.g. a script with the same bad idiom repeated 10,000 times) can't blow up
# caution_notes; we still count the total so the note says "+N more".
MAX_REPORTED_LINES_PER_PATTERN = 5

SCRIPT_EXTENSIONS = {".sh", ".bash", ".py", ".ps1", ".js"}

# A regex match is only trusted if the line it landed on is mostly printable
# text -- guards against an accidental byte-sequence match inside binary
# content that happens to satisfy a pattern by chance.
_TEXT_LINE_PRINTABLE_RATIO = 0.85

# --- risk_score reasoning -----------------------------------------------
# Highest confidence, near-zero legitimate use for an untrusted upload:
# these are exact, well-known malicious idioms with essentially no benign
# reading (a fork bomb function, a literal reverse-shell one-liner, an
# irreversible wipe of the filesystem root).
RISK_FORK_BOMB = 0.95
RISK_REVERSE_SHELL = 0.95
RISK_DESTRUCTIVE_FS = 0.9
# Fetch-then-execute of remote/obfuscated content: very suspicious, but
# curl-pipe-bash and base64-wrapped payloads do have (bad-practice) benign
# uses -- e.g. installer one-liners -- so these sit a notch below the
# deterministic idioms above.
RISK_REMOTE_PIPE_EXEC = 0.8
RISK_BASE64_EXEC = 0.7
RISK_EVAL_FETCH = 0.65
# Heuristic, not an exact idiom -- meaningfully more false-positive prone
# than the literal fork-bomb function pattern (legitimate polling/retry
# loops can look similar), so it scores below the deterministic patterns.
RISK_BG_LOOP_BOMB = 0.5
# Lowest-confidence category per the task spec: persistence writes
# (crontab, ~/.bashrc) are common in ordinary setup scripts too, so this
# check only fires when a write is paired with something payload-shaped,
# and even then scores lowest of everything here.
RISK_PERSISTENCE = 0.3


def _looks_like_script(path: Path, head: bytes) -> tuple[bool, str]:
    """(is_script, reason) from the file extension or a first-line shebang."""
    suffix = path.suffix.lower()
    if suffix in SCRIPT_EXTENSIONS:
        return True, f"file extension {suffix!r}"
    first_line = head.split(b"\n", 1)[0]
    if first_line.startswith(b"#!"):
        return True, "shebang line"
    return False, ""


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_span(text: str, start: int, end: int) -> str:
    """The full source line(s) containing text[start:end], for the printable-ratio check."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    return text[line_start:line_end]


def _looks_like_text(snippet: str) -> bool:
    if not snippet:
        return True
    printable = sum(1 for c in snippet if c.isprintable() or c in "\t\r")
    return (printable / len(snippet)) >= _TEXT_LINE_PRINTABLE_RATIO


def _regex_finder(rx: re.Pattern[str]) -> Callable[[str], Iterable[tuple[int, int]]]:
    def _finder(text: str) -> Iterable[tuple[int, int]]:
        return [m.span() for m in rx.finditer(text)]

    return _finder


def _is_destructive_rm_args(args: str) -> bool:
    """True if an `rm <args>` argument string is recursive+forced against `/` or `/*`."""
    has_recursive = False
    has_force = False
    targets = []
    for tok in args.split():
        if tok.startswith("--"):
            if tok == "--recursive":
                has_recursive = True
            elif tok == "--force":
                has_force = True
        elif tok.startswith("-") and len(tok) > 1:
            if "r" in tok or "R" in tok:
                has_recursive = True
            if "f" in tok:
                has_force = True
        else:
            targets.append(tok)
    if not (has_recursive and has_force):
        return False
    return any(t in ("/", "/*") for t in targets)


def _find_destructive_rm(text: str) -> Iterable[tuple[int, int]]:
    return [
        m.span()
        for m in re.finditer(r"\brm\s+([^\n;&|]+)", text)
        if _is_destructive_rm_args(m.group(1))
    ]


def _find_python_reverse_shell(text: str) -> Iterable[tuple[int, int]]:
    """`socket.socket(...)` followed within a short window by `.connect(` + subprocess/dup2."""
    spans = []
    for m in re.finditer(r"\bsocket\.socket\(", text):
        window = text[m.start() : m.start() + 400]
        if ".connect(" in window and (
            "subprocess" in window or "os.dup2" in window or "/bin/sh" in window
        ):
            spans.append(m.span())
    return spans


def _find_perl_reverse_shell(text: str) -> Iterable[tuple[int, int]]:
    """`perl` invocation whose nearby text carries the classic Socket/INET/connect idiom."""
    spans = []
    for m in re.finditer(r"\bperl\b", text):
        window = text[m.start() : m.start() + 300]
        if "Socket" in window and "connect" in window and ("INET" in window or "PF_INET" in window):
            spans.append(m.span())
    return spans


_BASHRC_APPEND_RE = re.compile(r">>\s*(?:~|\$HOME|/root|/home/\w+)?/?\.bashrc\b")
_BASHRC_PAYLOAD_KEYWORDS = ("curl", "wget", "nc ", "ncat", "/dev/tcp", "base64", "bash -i", "sh -i")


def _find_bashrc_persistence(text: str) -> Iterable[tuple[int, int]]:
    """A `.bashrc` append is only flagged when the same line also looks payload-shaped."""
    spans = []
    for m in _BASHRC_APPEND_RE.finditer(text):
        line = _line_span(text, m.start(), m.end())
        if any(keyword in line for keyword in _BASHRC_PAYLOAD_KEYWORDS):
            spans.append(m.span())
    return spans


@dataclass(frozen=True)
class _Pattern:
    label: str
    risk_score: float
    finder: Callable[[str], Iterable[tuple[int, int]]]


_PATTERNS: list[_Pattern] = [
    _Pattern(
        "fork bomb (self-replicating background function)",
        RISK_FORK_BOMB,
        _regex_finder(
            re.compile(
                r"(?P<fn>[:\w]+)\s*\(\)\s*\{\s*(?P=fn)\s*\|\s*(?P=fn)\s*&?\s*;?\s*\}\s*;\s*(?P=fn)"
            )
        ),
    ),
    _Pattern(
        "unbounded background-spawn loop (while true/1 ... & ... done)",
        RISK_BG_LOOP_BOMB,
        _regex_finder(
            re.compile(
                r"\bwhile\s+(?:true|:|1)\b.{0,50}?\bdo\b.{0,400}?&.{0,100}?\bdone\b",
                re.DOTALL,
            )
        ),
    ),
    _Pattern(
        "remote-pipe-execute (curl/wget piped into a shell interpreter)",
        RISK_REMOTE_PIPE_EXEC,
        _regex_finder(
            re.compile(
                r"\b(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:sudo\s+)?(?:bash|sh|zsh|python3?|perl)\b"
            )
        ),
    ),
    _Pattern(
        "destructive rm -rf against filesystem root",
        RISK_DESTRUCTIVE_FS,
        _find_destructive_rm,
    ),
    _Pattern(
        "mkfs targeting a device node",
        RISK_DESTRUCTIVE_FS,
        _regex_finder(re.compile(r"\bmkfs(?:\.\w+)?\s+(?:-\S+\s+)*/dev/\S+")),
    ),
    _Pattern(
        "dd writing directly onto a raw disk device",
        RISK_DESTRUCTIVE_FS,
        _regex_finder(re.compile(r"\bdd\s+[^\n]*\bof=/dev/(?:sd|hd|nvme|xvd|vd)\w*")),
    ),
    _Pattern(
        "base64-decode piped into a shell interpreter",
        RISK_BASE64_EXEC,
        _regex_finder(
            re.compile(r"\bbase64\s+(?:-d|--decode)\b[^\n]{0,200}\|\s*(?:sudo\s+)?(?:bash|sh)\b")
        ),
    ),
    _Pattern(
        "eval wrapping a fetched/decoded command substitution",
        RISK_EVAL_FETCH,
        _regex_finder(re.compile(r"\beval\s+\$\([^\n)]{0,50}(?:curl|wget|base64)[^\n)]{0,300}\)")),
    ),
    _Pattern(
        "bash /dev/tcp reverse shell",
        RISK_REVERSE_SHELL,
        _regex_finder(
            re.compile(r"\b(?:bash|sh|/bin/(?:ba)?sh)\s+-i\s*>&\s*/dev/tcp/[^\s/]+/\d+\s*0?>&1")
        ),
    ),
    _Pattern(
        "netcat -e reverse shell",
        RISK_REVERSE_SHELL,
        _regex_finder(re.compile(r"\bnc(?:\.\w+)?\s+(?:-\S+\s+)*-e\s+/bin/(?:ba)?sh\b")),
    ),
    _Pattern(
        "python socket/connect reverse-shell idiom",
        RISK_REVERSE_SHELL,
        _find_python_reverse_shell,
    ),
    _Pattern(
        "perl Socket/connect reverse-shell idiom",
        RISK_REVERSE_SHELL,
        _find_perl_reverse_shell,
    ),
    _Pattern(
        "crontab installed from piped stdin",
        RISK_PERSISTENCE,
        _regex_finder(re.compile(r"\|\s*crontab\s+-(?:\s|$)")),
    ),
    _Pattern(
        "append to /etc/crontab or cron.d",
        RISK_PERSISTENCE,
        _regex_finder(re.compile(r">>\s*/etc/(?:crontab\b|cron\.d/\S+)")),
    ),
    _Pattern(
        "payload-looking append to .bashrc",
        RISK_PERSISTENCE,
        _find_bashrc_persistence,
    ),
]


def _scan_text(text: str) -> tuple[float, list[str]]:
    risk = 0.0
    caution_notes: list[str] = []
    for pattern in _PATTERNS:
        spans = list(pattern.finder(text))
        valid_lines: list[int] = []
        for start, end in spans:
            if not _looks_like_text(_line_span(text, start, end)):
                continue  # coincidental byte match inside non-text content -- discard
            valid_lines.append(_line_number(text, start))
            if len(valid_lines) >= MAX_REPORTED_LINES_PER_PATTERN:
                break
        if not valid_lines:
            continue
        risk = max(risk, pattern.risk_score)
        lines_desc = ", ".join(str(n) for n in valid_lines)
        remaining = len(spans) - len(valid_lines)
        extra = f" (+{remaining} more)" if remaining > 0 else ""
        caution_notes.append(f"{pattern.label} -- line {lines_desc}{extra}")
    return risk, caution_notes


def run(path: str) -> dict:
    """Text/regex scan for known-dangerous script idioms; never executes/sources the target."""
    try:
        p = Path(path)
        with p.open("rb") as fh:
            head = fh.read(4096)
            is_script, reason = _looks_like_script(p, head)
            if not is_script:
                return {"scanned": False, "reason": "not a script/shebang"}
            fh.seek(0)
            data = fh.read(MAX_SCAN_BYTES)
    except Exception as exc:
        return {"error": str(exc)}

    text = data.decode("utf-8", errors="ignore")
    risk, caution_notes = _scan_text(text)

    result: dict = {"scanned": True, "detected_as": reason}
    if caution_notes:
        result["risk_score"] = risk
        result["caution_notes"] = caution_notes
    return result
