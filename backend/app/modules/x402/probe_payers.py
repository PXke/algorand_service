"""Which payer wallets are OURS (the probe and any other self-funded caller).

CLAUDE.md section 9: probe traffic is the one deliberate exception to "no wash
volume", and it must be excluded from every ranking -- in code, not by label.
Every x402 product that ranks, counts or serves what a payer paid for reads
this set and drops those wallets at the point where their payment would
otherwise become signal (a grade in an average, a vote in a demand total,
spend in a credibility weight, a tile on the board). The payments themselves
are still settled and still recorded in the ledger; only their influence on
what other agents see is removed.
"""

from __future__ import annotations

from app.core.config import settings


def probe_payer_set() -> frozenset[str]:
    """The configured probe/self wallets, uppercased and trimmed, possibly empty.

    Read from settings on every call rather than cached at import so a test
    can monkeypatch `x402_probe_payers` and so a config reload is honoured.
    Algorand addresses are case-insensitive base32, so comparisons go through
    `is_probe_payer`, which uppercases the candidate the same way.
    """
    raw = settings.x402_probe_payers or ""
    return frozenset(part.strip().upper() for part in raw.split(",") if part.strip())


def is_probe_payer(payer: str) -> bool:
    """Whether `payer` is one of our own wallets. Empty/unattributed is never a probe."""
    candidate = (payer or "").strip().upper()
    return bool(candidate) and candidate in probe_payer_set()
