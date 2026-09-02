"""x402 sandboxed file/tarball security scan -- roadmap item 18b in CLAUDE.md section 9.1.

PROTOTYPE, not a live product: registered only when `settings.x402_scan_enabled`
is True (default False). Built 2026-09-01 at the owner's explicit direction as
a same-day design + working proof-of-concept, per CLAUDE.md 18b's own note
that this item "needs a real sandbox design... before any code" -- that
design decision is made here and in sandbox_runner.py's module docstring,
not deferred.

Scope of this prototype: STATIC analysis only. An uploaded file/tarball is
never executed -- see sandbox/scan.py for the full tool list and rationale
(ClamAV, file-type check, entropy, embedded URL/IP extraction, zip/tar-bomb-
safe archive listing) and for which tools were deliberately left out of v0
(oletools, pdfid, pefile/YARA -- designed, not yet wired; rkhunter -- not a
fit for a single-file target at all, see scan.py's docstring for why).

One route: POST /api/v1/x402/scan/url. No file-upload route yet (v1).

Before this is flipped on for real traffic, two decisions are still owner
calls, not something this prototype decided unilaterally:
  1. WHICH HOST runs the Docker engine this spins containers on. Running
     attacker-supplied files' static analysis on the same host as the
     production newspaper pipeline and the x402 payment/settlement services
     is a real blast-radius question even with --network none + --cap-drop
     ALL + non-root + tmpfs-only (ClamAV, python3, and the container runtime
     itself all have their own CVE history) -- an isolated host is the
     safer default, at added infra cost. Not decided here.
  2. Whether/when to add dynamic (execute-and-observe) analysis. Explicitly
     NOT built here -- see sandbox_runner.py's docstring for why that mode's
     risk profile would reopen the Docker-vs-Firecracker choice.
"""
