"""The palette and the tag policy must not drift between the SSR and the SPA.

There is no CI in this repo, so this test is the only thing standing between a
palette change and three different brand blues shipping at once — which is
exactly what happened on 2026-07-27: the favicon, the PWA manifest and the
runtime theme-color each held their own hand-copied hex.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECKER = REPO / "tools" / "design" / "sync_tokens.py"

# The docker test image copies backend/, workers/, shared/ and deploy/ — not
# tools/ or frontend/ (docker/Dockerfile). This is a repo-level check running
# under the backend suite because that is the repo's only pytest harness, so
# skip where the checker genuinely is not present rather than failing on exit 2.
pytestmark = pytest.mark.skipif(
    not CHECKER.is_file(), reason=f"design token checker not present at {CHECKER}"
)


def test_design_tokens_and_tag_policy_are_in_sync() -> None:
    """Shell out to the sync checker; a nonzero exit means a hex or tag literal drifted."""
    proc = subprocess.run(
        [sys.executable, str(CHECKER)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        "design tokens drifted from shared/design_tokens.json.\n"
        "Run `python3 tools/design/sync_tokens.py --write` if the generated "
        "files are stale, or fix the listed literals by hand.\n\n"
        f"{proc.stdout}{proc.stderr}"
    )
