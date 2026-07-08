"""Builds the per-route `<head>` markup, JSON-LD and crawlable SSR body that
get injected into the Flutter shell (see shell.render_document).

The SSR body is a REAL visible `<div id="ssr-body">`, not `<noscript>`:
Googlebot renders JS, ignores noscript, and Flutter paints to canvas — so
noscript-only content is invisible to exactly the crawler that matters most.
The div is served identically to everyone (no user-agent cloaking), doubles as
a fast first paint while Flutter boots, and removes itself on the engine's
`flutter-first-frame` event."""

from __future__ import annotations

import html
import json
import re
from datetime import UTC, datetime

import msgspec

from app.core.config import settings
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo.markdown import md_to_html, md_to_text, truncate
from app.modules.seo.sections import SECTIONS, Section, matches_section


def site_url() -> str:
    return settings.public_site_url.rstrip("/")


def absolute(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return f"{site_url()}/{path.lstrip('/')}"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _attr(value: str) -> str:
    return html.escape(value or "", quote=True)


def _ssr_feed_script(items: list[ArticleFeedItem]) -> str:
    """Embed the home feed as JSON so Flutter can paint immediately without
    waiting on /api/v1/news/feed (SSR HTML is removed on first frame)."""
    rows = [msgspec.structs.asdict(i) for i in items]
    payload = json.dumps({"items": rows}, separators=(",", ":"), ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    return f'<script type="application/json" id="pxke-ssr-feed">{payload}</script>'


def _json_ld(data: dict | list) -> str:
    # `</` would otherwise let a script tag close early inside the body.
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def _same_as() -> list[str]:
    return [u.strip() for u in settings.seo_same_as.split(",") if u.strip()]


def _publisher() -> dict:
    org = {
        "@type": "Organization",
        "name": settings.site_name,
        "logo": {"@type": "ImageObject", "url": absolute("/icons/Icon-512.png")},
    }
    same = _same_as()
    if same:
        org["sameAs"] = same
    return org


# Bing/most audit tools warn above ~65 display chars; Google truncates by
# pixel width around the same point.
_TITLE_MAX_CHARS = 65


def _meta_block(
    *,
    title: str,
    description: str,
    canonical: str,
    image: str,
    og_type: str = "website",
    robots: str | None = None,
    published_iso: str | None = None,
    modified_iso: str | None = None,
    tags: list[str] | None = None,
    image_alt: str = "",
    image_dims: tuple[int, int] | None = None,
    json_ld: list[dict] | None = None,
) -> str:
    # <title> length budget (~65 chars before SERPs truncate and audit tools
    # warn): brand-suffix the title only when the result still fits; a long
    # article headline stands alone (brand already rides in og:site_name), and
    # a headline that is itself over budget is word-boundary clamped. Only the
    # <title> tag is clamped — og:/twitter:/JSON-LD keep the full headline.
    suffixed = title if title.endswith(settings.site_name) else f"{title} — {settings.site_name}"
    if len(suffixed) <= _TITLE_MAX_CHARS:
        full_title = suffixed
    elif len(title) <= _TITLE_MAX_CHARS:
        full_title = title
    else:
        cut = title[: _TITLE_MAX_CHARS - 1]
        space = cut.rfind(" ")
        if space > _TITLE_MAX_CHARS // 2:
            cut = cut[:space]
        full_title = cut.rstrip(" ,;:—-") + "…"
    parts = [
        f"<title>{html.escape(full_title)}</title>",
        f'<meta name="description" content="{_attr(description)}">',
        f'<link rel="canonical" href="{_attr(canonical)}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<link rel="alternate" type="application/rss+xml" '
        f'title="{_attr(settings.site_name)}" href="{_attr(absolute("/feed.xml"))}">',
    ]
    # Indexable pages opt into large image previews (Google Discover / News
    # cards); explicit robots values (noindex pages) pass through unchanged.
    parts.append(f'<meta name="robots" content="{_attr(robots or "max-image-preview:large")}">')
    # Open Graph
    parts += [
        f'<meta property="og:type" content="{_attr(og_type)}">',
        f'<meta property="og:site_name" content="{_attr(settings.site_name)}">',
        f'<meta property="og:title" content="{_attr(title)}">',
        f'<meta property="og:description" content="{_attr(description)}">',
        f'<meta property="og:url" content="{_attr(canonical)}">',
        f'<meta property="og:image" content="{_attr(image)}">',
        '<meta property="og:locale" content="en_US">',
    ]
    if image_alt:
        parts.append(f'<meta property="og:image:alt" content="{_attr(image_alt)}">')
    if image_dims:
        parts.append(f'<meta property="og:image:width" content="{image_dims[0]}">')
        parts.append(f'<meta property="og:image:height" content="{image_dims[1]}">')
    if published_iso:
        parts.append(f'<meta property="article:published_time" content="{_attr(published_iso)}">')
    if modified_iso:
        parts.append(f'<meta property="article:modified_time" content="{_attr(modified_iso)}">')
    for tag in tags or []:
        parts.append(f'<meta property="article:tag" content="{_attr(tag)}">')
    # Twitter
    parts += [
        '<meta name="twitter:card" content="summary_large_image">',
        f'<meta name="twitter:title" content="{_attr(title)}">',
        f'<meta name="twitter:description" content="{_attr(description)}">',
        f'<meta name="twitter:image" content="{_attr(image)}">',
    ]
    if image_alt:
        parts.append(f'<meta name="twitter:image:alt" content="{_attr(image_alt)}">')
    for block in json_ld or []:
        parts.append(_json_ld(block))
    return "\n".join(parts)


_DEFAULT_IMAGE_DIMS = (512, 512)  # icons/Icon-512.png


def _image_for(image_url: str | None) -> tuple[str, bool]:
    """(absolute_url, is_default). Dimensions are only known for the default."""
    if image_url:
        return absolute(image_url), False
    return absolute(settings.seo_default_image), True


# The workers store a brand-icon FALLBACK in image_url when a source has no
# real share image ("a brand logo populates image_url only — it's not a body
# banner", publish_tasks.py). That distinction doesn't survive into the
# article row, so detect icon-shaped URLs here: never stretch them into a
# body hero, and never emit them as og:image (SVG/ICO aren't valid share-card
# formats anyway — the site default wins a card, a favicon forfeits it).
# Word-boundary matched so "algorand_logo_mark.png" is an icon but
# "silicon.png" is not.
_ICON_NAME_RE = re.compile(r"(^|[-_.])(favicon|icon|logo|apple-touch)s?([-_.]|\d|$)")


def _is_icon_like(image_url: str) -> bool:
    from urllib.parse import urlparse

    path = (urlparse(image_url).path or "").lower()
    if path.endswith((".svg", ".ico")):
        return True
    name = path.rsplit("/", 1)[-1]
    return bool(_ICON_NAME_RE.search(name)) or "/icons/" in path


def article_path(article_id: str) -> str:
    return f"/news/articles/{article_id}"


# Readable fallback styling for the pre-boot paint (and no-JS readers); the
# Flutter app replaces it on first frame. Kept tiny and inline so the SSR body
# needs no extra request.
_SSR_STYLE = (
    "<style>#ssr-body{max-width:720px;margin:0 auto;padding:24px;"
    "font:17px/1.6 Georgia,'Times New Roman',serif;color:#1a1a1a}"
    "#ssr-body img{max-width:100%;height:auto}"
    "#ssr-body a{color:#0b57d0}"
    # The loading notice only exists for humans watching the app boot, so it is
    # hidden from the reading flow's start: JS reveals it, and it dies with the
    # div on first frame. No-JS readers and crawlers never see it.
    "#ssr-loading{display:none;font:13px/1.4 system-ui,sans-serif;color:#666;"
    "border-bottom:1px solid #ddd;padding-bottom:10px;margin-bottom:18px}</style>"
)
_SSR_LOADING = (
    '<p id="ssr-loading">Loading the interactive edition…</p>'
    "<script>document.getElementById('ssr-loading').style.display='block';</script>"
)
# Flutter's engine dispatches `flutter-first-frame` on window once the real UI
# has painted. At that point the engine has already made the SSR content
# invisible to users mechanically — it sets position:fixed/inset:0/
# overflow:hidden on <body> and overlays a full-viewport canvas — so the only
# thing left to deduplicate is assistive tech: aria-hidden keeps screen
# readers on the Flutter semantics tree. Deliberately NO display:none and no
# DOM removal: search engines render the page, first-frame fires in their
# renderer (verified via Search Console, 2026-07-08), and CSS-hidden main
# content is devalued in the rendered snapshot — covered-but-visible content
# is not. If Flutter fails to boot, the content stays — a working degraded page.
_SSR_REMOVE_SCRIPT = (
    "<script>window.addEventListener('flutter-first-frame',function(){"
    "var e=document.getElementById('ssr-body');"
    "e&&e.setAttribute('aria-hidden','true');});</script>"
)


def ssr_container(inner_html: str) -> str:
    return f'{_SSR_STYLE}<div id="ssr-body">{_SSR_LOADING}{inner_html}</div>{_SSR_REMOVE_SCRIPT}'


def _breadcrumb(trail: list[tuple[str, str]]) -> dict:
    """BreadcrumbList JSON-LD from (name, absolute_url) pairs."""
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(trail)
        ],
    }


def _website_jsonld() -> dict:
    """WebSite + SearchAction (enables the Google sitelinks search box)."""
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": settings.site_name,
        "url": site_url() + "/",
        "publisher": _publisher(),
        "potentialAction": {
            "@type": "SearchAction",
            "target": {
                "@type": "EntryPoint",
                "urlTemplate": site_url() + "/search?q={search_term_string}",
            },
            "query-input": "required name=search_term_string",
        },
    }


# --- Page builders: each returns (head_html, body_html) -----------------------


def _section_for(tags: list[str]) -> Section | None:
    return next((s for s in SECTIONS if matches_section(s, tags)), None)


def render_article(article: ArticleDetail) -> tuple[str, str]:
    canonical = absolute(article_path(article.article_id))
    image, is_default = _image_for(article.image_url)
    # A brand-icon fallback (favicon/logo) is tile art, not a share image or
    # banner — use the site default for metas and skip the body hero below.
    icon_like = bool(article.image_url) and _is_icon_like(image)
    if icon_like:
        image, is_default = absolute(settings.seo_default_image), True
    body_text = md_to_text(article.body)
    description = truncate(article.summary or body_text, 160)
    published_iso = _iso(article.published_at_epoch)

    trail = [("Home", site_url() + "/")]
    section = _section_for(article.tags or [])
    if section:
        trail.append((section.label, absolute(f"/section/{section.slug}")))
    trail.append((truncate(article.title, 80), canonical))

    news_article = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": truncate(article.title, 110),
        "description": description,
        "datePublished": published_iso,
        "dateModified": published_iso,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "image": [image],
        "articleBody": body_text,
        "publisher": _publisher(),
        "author": _publisher(),
        "keywords": ", ".join(article.tags or []),
        "isAccessibleForFree": True,
    }
    head = _meta_block(
        title=article.title,
        description=description,
        canonical=canonical,
        image=image,
        og_type="article",
        published_iso=published_iso,
        modified_iso=published_iso,
        tags=article.tags,
        image_alt=article.title,
        image_dims=_DEFAULT_IMAGE_DIMS if is_default else None,
        json_ld=[news_article, _breadcrumb(trail)],
    )

    body_html = md_to_html(article.body)
    # Standalone hero image — but only when the article BODY doesn't already
    # embed the same image (writers often lead the markdown with the OG image,
    # which rendered the hero twice back-to-back at the top of the document).
    img_html = ""
    if article.image_url and not icon_like and _attr(image) not in body_html:
        img_html = f'<img src="{_attr(image)}" alt="{_attr(article.title)}">'
    source = (
        f'<p>Source: <a href="{_attr(article.source_url)}" rel="noopener nofollow">'
        f"{_attr(article.source_url)}</a></p>"
        if article.source_url
        else ""
    )
    body = ssr_container(
        f"<article><h1>{html.escape(article.title)}</h1>"
        f'<p><time datetime="{_attr(published_iso)}">{published_iso[:10]}</time></p>'
        f"{img_html}{body_html}{source}</article>"
    )
    return head, body


def _feed_list_jsonld(items: list[ArticleFeedItem], canonical: str, name: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": name,
        "url": canonical,
        "publisher": _publisher(),
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "url": absolute(article_path(item.article_id)),
                    "name": item.title,
                }
                for i, item in enumerate(items)
            ],
        },
    }


def _feed_ssr(items: list[ArticleFeedItem], heading: str) -> str:
    """Crawlable (and pre-boot visible) feed listing — these are the only real
    internal links Google's renderer ever sees, since the Flutter app is canvas."""
    links = "".join(
        f'<li><a href="{_attr(article_path(item.article_id))}">'
        f"{html.escape(item.title)}</a> — {html.escape(truncate(item.summary, 140))}</li>"
        for item in items
    )
    return ssr_container(f"<h1>{html.escape(heading)}</h1><ul>{links}</ul>")


def render_home(items: list[ArticleFeedItem]) -> tuple[str, str]:
    canonical = site_url() + "/"
    description = settings.site_tagline
    head = _meta_block(
        title=settings.site_name,
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[
            _website_jsonld(),
            {"@context": "https://schema.org", **_publisher(), "url": site_url() + "/"},
            _feed_list_jsonld(items, canonical, f"{settings.site_name} — Latest"),
        ],
    )
    head = f"{head}\n{_ssr_feed_script(items)}"
    body = _feed_ssr(items, f"{settings.site_name} — Latest Algorand news")
    return head, body


def render_section(section: Section, items: list[ArticleFeedItem]) -> tuple[str, str]:
    canonical = absolute(f"/section/{section.slug}")
    title = f"{section.label} — Algorand news"
    trail = [("Home", site_url() + "/"), (section.label, canonical)]
    head = _meta_block(
        title=title,
        description=section.description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[_feed_list_jsonld(items, canonical, title), _breadcrumb(trail)],
    )
    body = _feed_ssr(items, section.label)
    return head, body


def render_about() -> tuple[str, str]:
    canonical = absolute("/about")
    description = f"About {settings.site_name}: {settings.site_tagline}"
    head = _meta_block(
        title="About",
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "AboutPage",
                "url": canonical,
                "name": f"About {settings.site_name}",
                "publisher": _publisher(),
            }
        ],
    )
    disclosure = (
        f"{settings.site_name} publishes AI-assisted journalism: articles are drafted "
        "by AI language models from on-chain events, market data and community sources "
        "under automated editorial review, with source links on every story. The "
        "organisation, not an individual byline, is the author of record."
    )
    body = ssr_container(
        f"<h1>About {html.escape(settings.site_name)}</h1>"
        f"<p>{html.escape(settings.site_tagline)}</p>"
        f"<h2>Written with AI</h2><p>{html.escape(disclosure)}</p>"
    )
    return head, body


def render_contact() -> tuple[str, str]:
    canonical = absolute("/contact")
    description = f"Contact {settings.site_name}: send corrections, tips or feedback."
    head = _meta_block(
        title="Contact",
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "ContactPage",
                "url": canonical,
                "name": f"Contact {settings.site_name}",
                "publisher": _publisher(),
            }
        ],
    )
    body = ssr_container(
        f"<h1>Contact {html.escape(settings.site_name)}</h1>"
        "<p>Spotted an error, have a tip, or want to reach the newsroom? "
        "Send us a message with the form on this page — corrections and "
        "feedback go straight to the editors.</p>"
    )
    return head, body


def render_noindex(title: str) -> tuple[str, str]:
    """Minimal shell for utility routes (admin/search/suggestions) — keep them
    out of the index but still serve the app."""
    head = _meta_block(
        title=title,
        description=settings.site_tagline,
        canonical=site_url() + "/",
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        robots="noindex, follow",
    )
    return head, ""
