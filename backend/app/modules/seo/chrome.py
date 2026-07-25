"""Shared site chrome for SSR bodies: masthead, primary nav, breadcrumbs and footer. Mirrors the Flutter shell/footer so crawlers see the same link graph humans get after the app boots (canvas apps otherwise expose almost no <a>)."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from app.core.config import settings


def _site_url() -> str:
    return settings.public_site_url.rstrip("/")


# (label, path) — matches the newspaper section nav in app_shell.dart / site_footer.dart.
NAV_LINKS: tuple[tuple[str, str], ...] = (
    ("Latest", "/news"),
    ("Most read", "/hot"),
    ("Topics", "/topics"),
    ("Search", "/search"),
    ("About", "/about"),
    ("Contact", "/contact"),
)

_FOOTER_TOPIC_CAP = 12


def _attr(value: str) -> str:
    return html.escape(value or "", quote=True)


def _path(url: str) -> str:
    """Turn an absolute site URL into a root-relative path for <a href>."""
    base = _site_url()
    if url.startswith(base):
        rest = url[len(base) :]
        return rest if rest.startswith("/") else f"/{rest}"
    if url.startswith(("http://", "https://")):
        return url
    return url if url.startswith("/") else f"/{url}"


def _nav_html(*, active: str | None) -> str:
    items = []
    for label, path in NAV_LINKS:
        extra = ' aria-current="page"' if active and path == active else ""
        items.append(f'<li><a href="{_attr(path)}"{extra}>{html.escape(label)}</a></li>')
    # Home/nameplate is separate; nav covers the section axis.
    return f'<nav class="ssr-nav" aria-label="Primary"><ul>{"".join(items)}</ul></nav>'


def _breadcrumb_html(trail: list[tuple[str, str]]) -> str:
    if not trail:
        return ""
    items = []
    for i, (name, url) in enumerate(trail):
        if i < len(trail) - 1:
            items.append(f'<li><a href="{_attr(_path(url))}">{html.escape(name)}</a></li>')
        else:
            items.append(f'<li aria-current="page">{html.escape(name)}</li>')
    return f'<nav class="ssr-crumbs" aria-label="Breadcrumb"><ol>{"".join(items)}</ol></nav>'


def _footer_html(*, topic_links: list[tuple[str, int]] | None) -> str:
    news_links = "".join(
        f'<li><a href="{_attr(path)}">{html.escape(label)}</a></li>'
        for label, path in NAV_LINKS[:3]
    )
    about_links = "".join(
        f'<li><a href="{_attr(path)}">{html.escape(label)}</a></li>'
        for label, path in NAV_LINKS[3:]
    )
    syndication_links = (
        f'<li><a href="{_attr("/feed.xml")}" type="application/rss+xml">RSS feed</a></li>'
        f'<li><a href="{_attr("/sitemap.xml")}">Sitemap</a></li>'
    )
    topic_block = ""
    if topic_links:
        links = "".join(
            f'<li><a href="{_attr(f"/topic/{tag}")}">{html.escape(tag)}</a>'
            f' <span class="ssr-muted">({count})</span></li>'
            for tag, count in topic_links[:_FOOTER_TOPIC_CAP]
        )
        topic_block = (
            f'<section class="ssr-footer-col" aria-labelledby="ssr-topics-h">'
            f'<h2 id="ssr-topics-h">Topics</h2><ul>{links}</ul></section>'
        )
    year = datetime.now(tz=UTC).year
    return (
        f'<footer class="ssr-footer">'
        f'<div class="ssr-footer-grid">'
        f'<section class="ssr-footer-brand" aria-labelledby="ssr-brand-h">'
        f'<h2 id="ssr-brand-h"><a href="/">{html.escape(settings.site_name)}</a></h2>'
        f"<p>{html.escape(settings.site_tagline)}</p>"
        f"</section>"
        f'<section class="ssr-footer-col" aria-labelledby="ssr-news-h">'
        f'<h2 id="ssr-news-h">News</h2><ul>{news_links}</ul></section>'
        f'<section class="ssr-footer-col" aria-labelledby="ssr-about-h">'
        f'<h2 id="ssr-about-h">About</h2><ul>{about_links}</ul></section>'
        f'<section class="ssr-footer-col" aria-labelledby="ssr-synd-h">'
        f'<h2 id="ssr-synd-h">Syndication</h2><ul>{syndication_links}</ul></section>'
        f"{topic_block}"
        f"</div>"
        f'<p class="ssr-rights">© {year} {html.escape(settings.site_name)}. '
        f"All rights reserved.</p>"
        f"</footer>"
    )


def ssr_page(
    main_html: str,
    *,
    active: str | None = None,
    breadcrumbs: list[tuple[str, str]] | None = None,
    topic_links: list[tuple[str, int]] | None = None,
) -> str:
    """Site chrome wrapping crawlable main content (inside #ssr-body)."""
    crumbs = _breadcrumb_html(breadcrumbs or [])
    return (
        f'<header class="ssr-header">'
        f'<div class="ssr-masthead">'
        f'<p class="ssr-brand"><a href="/">{html.escape(settings.site_name)}</a></p>'
        f'<p class="ssr-tagline">{html.escape(settings.site_tagline)}</p>'
        f"</div>"
        f"{_nav_html(active=active)}"
        f"</header>"
        f"{crumbs}"
        f'<main class="ssr-main">{main_html}</main>'
        f"{_footer_html(topic_links=topic_links)}"
    )


# Palette mirrors AppThemeExtension.light (frontend_flutter/lib/core/theme/
# app_theme_extension.dart) and share_card.py's _PAPER/_INK/_MUTED/_ACCENT, so
# the pre-boot paint reads as the same paper instead of flashing to a
# different look-and-feel the instant Flutter takes over.
SSR_CHROME_STYLE = (
    "#ssr-body{max-width:880px;margin:0 auto;padding:24px 20px 32px;"
    "border-top:4px solid #4F46E5;background:#F8F7F4;"
    "font:17px/1.55 Georgia,'Times New Roman',serif;color:#13161C}"
    "#ssr-body img{max-width:100%;height:auto}"
    "#ssr-body a{color:#4F46E5;text-decoration:none}"
    "#ssr-body a:hover{text-decoration:underline}"
    ".ssr-header{border-bottom:1px solid #DADDE4;padding-bottom:16px;margin-bottom:20px;"
    "padding-top:20px}"
    ".ssr-masthead{margin-bottom:12px}"
    ".ssr-brand{margin:0;font:700 1.35em Georgia,'Times New Roman',serif}"
    ".ssr-brand a{color:inherit}"
    ".ssr-tagline{margin:4px 0 0;font:13px/1.4 system-ui,sans-serif;color:#5C6573}"
    ".ssr-nav ul{display:flex;flex-wrap:wrap;gap:6px 18px;margin:0;padding:0;list-style:none;"
    "font:14px system-ui,sans-serif}"
    ".ssr-nav a[aria-current=page]{font-weight:700;color:#13161C;text-decoration:underline}"
    ".ssr-crumbs{margin:0 0 18px;font:13px system-ui,sans-serif;color:#5C6573}"
    ".ssr-crumbs ol{display:flex;flex-wrap:wrap;gap:4px 0;margin:0;padding:0;list-style:none}"
    ".ssr-crumbs li+li::before{content:'›';margin:0 8px;color:#9AA0AC}"
    ".ssr-main{margin-bottom:28px}"
    ".ssr-footer{border-top:1px solid #DADDE4;padding-top:24px;"
    "font:14px/1.5 system-ui,sans-serif}"
    ".ssr-footer-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));"
    "gap:20px 28px;margin-bottom:20px}"
    ".ssr-footer h2{margin:0 0 10px;font-size:11px;letter-spacing:.08em;"
    "text-transform:uppercase;color:#5C6573;font-weight:700}"
    ".ssr-footer ul{margin:0;padding:0;list-style:none}"
    ".ssr-footer li{margin:0 0 6px}"
    ".ssr-footer-brand h2{font-size:1.05em;letter-spacing:0;text-transform:none;color:#13161C}"
    ".ssr-muted{color:#5C6573;font-size:.92em}"
    ".ssr-rights{margin:0;font-size:12px;color:#5C6573}"
    ".ssr-tags{font:14px system-ui,sans-serif;color:#5C6573;margin:0 0 14px}"
    ".ssr-langs{font:14px system-ui,sans-serif;color:#5C6573;margin:20px 0 0;"
    "padding-top:14px;border-top:1px solid #DADDE4}"
    ".ssr-langs [aria-current=true]{font-weight:700;color:#13161C}"
    ".ssr-related{margin-top:28px;padding-top:20px;border-top:1px solid #DADDE4;"
    "font:15px system-ui,sans-serif}"
    ".ssr-related h2{margin:0 0 12px;font-size:13px;letter-spacing:.06em;"
    "text-transform:uppercase;color:#5C6573;font-weight:700}"
    ".ssr-related ul{margin:0;padding:0;list-style:none}"
    ".ssr-related li{margin:0 0 8px}"
    ".ssr-back{font:14px system-ui,sans-serif;margin:0 0 16px}"
    ".ssr-front section{margin:0 0 28px}"
    ".ssr-front h2{margin:0 0 12px;font:13px system-ui,sans-serif;letter-spacing:.06em;"
    "text-transform:uppercase;color:#5C6573;font-weight:700}"
    ".ssr-front h2 a{color:inherit}"
    ".ssr-lead{margin-bottom:8px}"
    ".ssr-lead h1{margin:0 0 12px;font-size:1.65em;line-height:1.25}"
    # Headlines read as editorial text, not hyperlinks — accent is a small
    # flourish elsewhere (kicker, nav, footer), not the whole headline.
    # (#ssr-body prefix matches the specificity of "#ssr-body a" above.)
    "#ssr-body .ssr-lead h1 a,#ssr-body .ssr-front li a{color:#13161C}"
    "#ssr-body .ssr-lead h1 a:hover,#ssr-body .ssr-front li a:hover{color:#4F46E5}"
    ".ssr-lead p{margin:0 0 12px}"
    ".ssr-front ul,.ssr-front ol{margin:0;padding:0 0 0 1.2em}"
    ".ssr-front li{margin:0 0 8px}"
    ".ssr-more-feed{margin-top:8px;font:14px system-ui,sans-serif}"
)
