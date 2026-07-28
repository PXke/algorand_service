"""Cross-boundary design tokens, loaded from ``shared/design_tokens.json``.

The palette lives in ``frontend/src/app.css``; a handful of values have to be
restated in files that cannot read a CSS custom property (the SSR ``<style>``
this backend emits, a favicon, the offline page). Those values are the ones in
the JSON, and this is how Python reads them — so the SSR shell and the SPA can
no longer disagree about what colour the paper is.

See ``tools/design/sync_tokens.py`` for the generator/checker that keeps the
non-Python consumers honest.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

Mode = Literal["light", "dark"]

_TOKENS_PATH = Path(__file__).resolve().parent.parent / "design_tokens.json"


@lru_cache(maxsize=1)
def _tokens() -> dict[str, dict[str, str]]:
    raw = json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))
    return raw["tokens"]


def token(name: str, mode: Mode = "light") -> str:
    """Hex value for a token, e.g. ``token("surface")`` -> ``"#f7f4ee"``.

    Falls back to the light value when a token has no separate dark step —
    most don't, because only the browser chrome tint currently differs.
    """
    entry = _tokens().get(name)
    if entry is None:
        raise KeyError(f"unknown design token {name!r} (see shared/design_tokens.json)")
    value = entry.get(mode) or entry.get("light")
    if not value:
        raise KeyError(f"design token {name!r} has no {mode} or light value")
    return value


def rgb(name: str, mode: Mode = "light") -> tuple[int, int, int]:
    """Token as an (R, G, B) tuple, for Pillow — e.g. the OG share cards."""
    value = token(name, mode).lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


@lru_cache(maxsize=2)
def palette(mode: Mode = "light") -> dict[str, str]:
    """All tokens resolved for one mode, keyed by token name."""
    return {name: token(name, mode) for name in _tokens()}
