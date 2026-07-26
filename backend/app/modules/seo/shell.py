"""Loads the built Vite SPA `index.html` and injects per-route SEO markup.

Humans still boot the SPA normally (its module scripts are untouched); crawlers
read the injected `<head>` tags, JSON-LD and the visible `#ssr-body` content in
the initial HTML. Once the app mounts it fires `pxke-spa-ready` and a small
script removes `#ssr-body` so browser find does not match duplicated text. Same
bytes go to everyone — no User-Agent cloaking.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from app.core.config import settings

_TITLE_RE = re.compile(r"<title>.*?</title>", re.IGNORECASE | re.DOTALL)
_DESC_RE = re.compile(r'<meta\s+name=["\']description["\'][^>]*>', re.IGNORECASE)
# Baseline OG/Twitter/canonical defaults live in the static index.html; strip
# them so the per-route tags we inject don't duplicate (crawlers dislike dupes).
_OG_RE = re.compile(r'<meta\s+property=["\']og:[^"\']*["\'][^>]*>', re.IGNORECASE)
_TW_RE = re.compile(r'<meta\s+name=["\']twitter:[^"\']*["\'][^>]*>', re.IGNORECASE)
_CANON_RE = re.compile(r'<link\s+rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
_RSS_RE = re.compile(
    r'<link\s+rel=["\']alternate["\'][^>]*type=["\']application/rss\+xml["\'][^>]*>',
    re.IGNORECASE,
)
_HEAD_CLOSE_RE = re.compile(r"</head>", re.IGNORECASE)
_BODY_OPEN_RE = re.compile(r"<body[^>]*>", re.IGNORECASE)
_HTML_LANG_RE = re.compile(r'(<html[^>]*\s)lang=["\'][^"\']*["\']', re.IGNORECASE)

_cache: dict[str, object] = {"path": None, "mtime": 0.0, "html": None}


def _safe_cwd_roots() -> list[Path]:
    """Return cwd / parent when getcwd works.

    During a rolling deploy the systemd WorkingDirectory (an old release path)
    can be deleted while the process is still alive; Path.cwd() then raises
    FileNotFoundError and every SSR document route 500s. Prefer __file__-based
    roots; treat a missing cwd as empty.
    """
    try:
        cwd = Path.cwd()
    except FileNotFoundError:
        return []
    return [cwd, cwd.parent]


def _candidate_dirs() -> list[Path]:
    """Directories to probe for the built Vite SPA, most likely first."""
    if settings.frontend_dist_dir:
        return [Path(settings.frontend_dist_dir)]
    # Auto-detect the built web dir. In prod it sits at <release>/frontend_web,
    # in dev at <repo>/frontend/dist. shell.py is at
    # backend/app/modules/seo/, so the release/repo root is parents[4]; we also
    # probe the systemd WorkingDirectory (releases/current/backend) via cwd, so a
    # path-depth change can't silently strip the SPA's own scripts again.
    here = Path(__file__).resolve()
    roots: list[Path] = [here.parents[depth] for depth in (4, 3, 5) if depth < len(here.parents)]
    roots += _safe_cwd_roots()
    dirs: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for sub in ("frontend_web", "frontend/dist"):
            cand = root / sub
            if cand not in seen:
                seen.add(cand)
                dirs.append(cand)
    return dirs


def _resolve_index() -> Path | None:
    for d in _candidate_dirs():
        candidate = d / "index.html"
        if candidate.is_file():
            return candidate
    return None


def load_template() -> str | None:
    """Return the index.html shell, reloading when the file changes on disk."""
    path = _resolve_index()
    if path is None:
        return None
    mtime = path.stat().st_mtime
    if _cache["path"] != str(path) or _cache["mtime"] != mtime or _cache["html"] is None:
        _cache["path"] = str(path)
        _cache["mtime"] = mtime
        _cache["html"] = path.read_text(encoding="utf-8")
    return _cache["html"]  # type: ignore[return-value]


def ssr_track_snippet(path: str) -> str:
    """Inline script marking the SSR-recorded path so the SPA beacon can skip a duplicate count for the same landing page (it reads sessionStorage.pxke_ssr_pv on boot)."""
    safe = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'<script>try{{sessionStorage.setItem("pxke_ssr_pv","{safe}")}}catch(e){{}}</script>'


def render_document(head_html: str, body_html: str, *, html_lang: str = "en") -> str | None:
    """Inject `head_html` (title/meta/JSON-LD) before </head> and `body_html` (the crawlable `#ssr-body` content) right after <body>. Strips the static title/description so ours win."""
    template = load_template()
    if template is None:
        return None
    doc = _HTML_LANG_RE.sub(
        lambda m: f'{m.group(1)}lang="{html.escape(html_lang, quote=True)}"',
        template,
        count=1,
    )
    doc = _TITLE_RE.sub("", doc, count=1)
    doc = _DESC_RE.sub("", doc, count=1)
    doc = _OG_RE.sub("", doc)
    doc = _TW_RE.sub("", doc)
    doc = _CANON_RE.sub("", doc)
    doc = _RSS_RE.sub("", doc)
    if _HEAD_CLOSE_RE.search(doc):
        # Inject via a callable: a plain replacement string is processed for
        # backslash escapes by re.sub, which turned the \n sequences json.dumps
        # wrote inside the JSON-LD articleBody into raw newlines — invalid JSON
        # that made Google drop the whole NewsArticle block.
        injected = head_html + "\n</head>"
        doc = _HEAD_CLOSE_RE.sub(lambda _m: injected, doc, count=1)
    if _BODY_OPEN_RE.search(doc):
        doc = _BODY_OPEN_RE.sub(lambda m: m.group(0) + "\n" + body_html, doc, count=1)
    return doc
