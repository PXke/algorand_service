"""Builds the per-route `<head>` markup, JSON-LD and crawlable SSR body that get injected into the Vite SPA shell (see shell.render_document).

The SSR body is a REAL visible `<div id="ssr-body">`, not `<noscript>`:
Googlebot renders JS but ignores noscript, and many share scrapers never run
JS at all — so noscript-only content is invisible to exactly the crawlers that
matter. The div is served identically to everyone (no user-agent cloaking),
doubles as a fast first paint while the SPA boots, and is removed from the DOM
on the app's `pxke-spa-ready` event so browser find does not match duplicates.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import quote

import msgspec
from algorand_shared.design import token
from algorand_shared.taxonomy import display_tag_title, is_meta_tag

from app.core import serialization
from app.core.article_translation_langs import (
    ARTICLE_TRANSLATION_LANG_NAMES,
    SEO_HREFLANG_LOCALES,
    html_lang_for,
    og_locale_for,
)
from app.core.config import settings
from app.modules.ecosystem.models.domain import StoredProject, category_label
from app.modules.glossary.store import GlossaryTerm
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo.chrome import SSR_CHROME_STYLE, registry_ssr_page, ssr_page
from app.modules.seo.markdown import md_to_html, md_to_text, truncate
from app.modules.seo.topics import display_tag_label, primary_tag, topic_feed_path
from app.modules.x402_board.models.domain import StoredPlacement
from app.modules.x402_catalog.services.catalog import CATALOG_PATH
from app.modules.x402_directory.models.domain import StoredListing
from app.modules.x402_features.models.domain import ClaimSummary, StoredFeatureRequest
from app.modules.x402_grading.models.domain import GradedEndpoint


def site_url() -> str:
    """Return the public site base URL with no trailing slash."""
    return settings.public_site_url.rstrip("/")


def absolute(path: str) -> str:
    """Turn a possibly-relative path into an absolute site URL."""
    if path.startswith(("http://", "https://")):
        return path
    return f"{site_url()}/{path.lstrip('/')}"


def registry_site_url() -> str:
    """Public base URL for the Algorand Open Registry's own domain -- NOT site_url() (see render_registry_index/render_registry_entry, the only callers)."""
    return settings.registry_public_site_url.rstrip("/")


def registry_absolute(path: str) -> str:
    """Turn a possibly-relative path into an absolute REGISTRY-domain URL (mirrors absolute(), scoped to registry_site_url())."""
    if path.startswith(("http://", "https://")):
        return path
    return f"{registry_site_url()}/{path.lstrip('/')}"


def x402_site_url() -> str:
    """Public base URL for the x402 marketplace's own domain -- x402 has no SSR document routes yet (2026-09-08), only a sitemap; see sitemap.build_x402_sitemap, the one caller."""
    return settings.x402_public_site_url.rstrip("/")


def x402_absolute(path: str) -> str:
    """Turn a possibly-relative path into an absolute X402-domain URL (mirrors absolute(), scoped to x402_site_url())."""
    if path.startswith(("http://", "https://")):
        return path
    return f"{x402_site_url()}/{path.lstrip('/')}"


def _content_img_src(image_url: str) -> str:
    """Same-origin image-proxy URL for in-page content (LCP-friendly)."""
    abs_url = absolute(image_url)
    if "/api/v1/img?" in abs_url:
        return abs_url
    # Relative so the document and LCP image share one connection.
    return f"/api/v1/img?url={quote(abs_url, safe='')}"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def _word_count(text: str) -> int:
    return len(re.findall(r"\w+", text, flags=re.UNICODE))


def _speakable_jsonld() -> dict:
    return {
        "@type": "SpeakableSpecification",
        "cssSelector": [".ssr-main article h1", ".ssr-main article p"],
    }


def _attr(value: str) -> str:
    return html.escape(value or "", quote=True)


def _ssr_feed_script(items: list[ArticleFeedItem]) -> str:
    """Embed feed rows as JSON so the SPA can paint immediately without waiting on the API (the SSR HTML is removed once it mounts). Used on /, /news and /hot."""
    rows = [msgspec.structs.asdict(i) for i in items]
    payload = serialization.dumps({"items": rows}).replace("</", "<\\/")
    return f'<script type="application/json" id="pxke-ssr-feed">{payload}</script>'


def _json_ld(data: dict | list) -> str:
    # `</` would otherwise let a script tag close early inside the body.
    payload = serialization.dumps(data).replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def _same_as() -> list[str]:
    return [u.strip() for u in settings.seo_same_as.split(",") if u.strip()]


def _publisher() -> dict:
    org = {
        "@type": "Organization",
        "name": settings.site_name,
        "logo": {"@type": "ImageObject", "url": absolute("/icons/icon-512.png")},
    }
    same = _same_as()
    if same:
        org["sameAs"] = same
    return org


# SERP title budget, in display-width units (~65 Latin characters, the point
# where Google's ~600px desktop title link starts to clip and audit tools warn).
_TITLE_WIDTH_BUDGET = 65
# Pathological-length guard only — a model glitch must not ship a 2KB <title>.
# Normal overlong headlines pass through whole; see _clamped_title.
_TITLE_HARD_WIDTH_CAP = 200


def _display_width(text: str) -> int:
    """Approximate SERP display width in Latin-character units.

    Search engines clip the title by PIXEL width, not character count, and a
    CJK glyph occupies roughly two Latin advance widths — so 37 Chinese
    characters already fill the space of ~74 Latin ones. Counting characters
    judges Chinese titles as less than half their real width (measured
    2026-07-29: mean 37.3 chars for zh, so a flat 65-char rule waved through
    98.8% of them) while over-penalising every other script.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _clamped_title(title: str) -> str:
    """The <title> tag text: brand-suffixed while the result still fits the SERP width budget, otherwise the bare headline (the brand already rides in og:site_name).

    The headline itself is NEVER truncated. Google and Yandex shorten an
    overlong title themselves -- at a word boundary, or by rewriting from the
    H1 -- and they do it better than a blind cut, because they can see which
    query matched. Pre-truncating is strictly worse on both counts: the tail
    never reaches the engine at all, and our own ellipsis ships as visible SERP
    text that reads as a broken headline.

    That cost was not theoretical. Measured against the old flat 65-character
    cut (2026-07-29, live corpus): 95.2% of French titles, 94.0% of Russian and
    Spanish, 85.5% of Farsi and Pashto -- and 65.1% of the English ones -- were
    served to crawlers pre-truncated with a trailing "…". Translations run
    15-25% longer than their English source, so a headline that just fits in
    English overflows in every Romance and Slavic target. Those locale pages
    are the best-ranking pages on the site (impression-weighted position 15.8
    vs 21.0 for English), so the cut was landing hardest on exactly the pages
    that were working.
    """
    title = title.strip()
    suffixed = title if title.endswith(settings.site_name) else f"{title} — {settings.site_name}"
    if _display_width(suffixed) <= _TITLE_WIDTH_BUDGET:
        return suffixed
    if _display_width(title) <= _TITLE_HARD_WIDTH_CAP:
        return title
    # Beyond the guard: clamp on a word boundary rather than emit a runaway tag.
    cut = title[: _TITLE_HARD_WIDTH_CAP - 1]
    space = cut.rfind(" ")
    if space > _TITLE_HARD_WIDTH_CAP // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:—-") + "…"


def _og_locale_parts(
    og_locale_alternates: list[str] | None, hreflang_links: list[tuple[str, str]] | None
) -> list[str]:
    """og:locale:alternate tags plus hreflang <link> tags for every translation."""
    parts = [
        f'<meta property="og:locale:alternate" content="{_attr(alt)}">'
        for alt in og_locale_alternates or []
    ]
    parts.extend(
        f'<link rel="alternate" hreflang="{_attr(hreflang)}" href="{_attr(url)}">'
        for hreflang, url in hreflang_links or []
    )
    return parts


def _og_article_meta_parts(
    *,
    image_alt: str,
    image_dims: tuple[int, int] | None,
    published_iso: str | None,
    modified_iso: str | None,
    og_section: str | None,
    tags: list[str] | None,
) -> list[str]:
    """Optional og:image alt/dims and article:published_time/modified_time/section/tag meta tags."""
    parts: list[str] = []
    if image_alt:
        parts.append(f'<meta property="og:image:alt" content="{_attr(image_alt)}">')
    if image_dims:
        parts.append(f'<meta property="og:image:width" content="{image_dims[0]}">')
        parts.append(f'<meta property="og:image:height" content="{image_dims[1]}">')
    if published_iso:
        parts.append(f'<meta property="article:published_time" content="{_attr(published_iso)}">')
    if modified_iso:
        parts.append(f'<meta property="article:modified_time" content="{_attr(modified_iso)}">')
    if og_section:
        parts.append(f'<meta property="article:section" content="{_attr(og_section)}">')
    parts.extend(f'<meta property="article:tag" content="{_attr(tag)}">' for tag in tags or [])
    return parts


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
    og_locale: str = "en_US",
    og_locale_alternates: list[str] | None = None,
    hreflang_links: list[tuple[str, str]] | None = None,
    og_section: str | None = None,
    lang: str = "en",
) -> str:
    full_title = _clamped_title(title)
    parts = [
        f"<title>{html.escape(full_title)}</title>",
        f'<meta name="description" content="{_attr(description)}">',
        f'<link rel="canonical" href="{_attr(canonical)}">',
        # Bing does not read hreflang/xhtml:link at all (Google-only feature);
        # it determines a page's language from <html lang>, this tag, and its
        # own NLP over the body text. <html lang> is already set per-locale
        # (see shell.py); this is the other page-level signal Bing wants.
        f'<meta http-equiv="content-language" content="{_attr(lang)}">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">',
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
        f'<meta property="og:locale" content="{_attr(og_locale)}">',
    ]
    parts += _og_locale_parts(og_locale_alternates, hreflang_links)
    parts += _og_article_meta_parts(
        image_alt=image_alt,
        image_dims=image_dims,
        published_iso=published_iso,
        modified_iso=modified_iso,
        og_section=og_section,
        tags=tags,
    )
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


_DEFAULT_IMAGE_DIMS = (512, 512)  # icons/icon-512.png
_OG_CARD_DIMS = (1200, 630)  # seo/share_card.py — the standard OG share size


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


def is_icon_like(image_url: str) -> bool:
    from urllib.parse import urlparse

    path = (urlparse(image_url).path or "").lower()
    if path.endswith((".svg", ".ico")):
        return True
    name = path.rsplit("/", 1)[-1]
    if bool(_ICON_NAME_RE.search(name)) or "/icons/" in path:
        return True
    # Dynamic per-page OG-image-generator endpoints (Next.js/Vercel's `/og/
    # [slug]` convention): render a logo + page title on a solid background
    # for whatever route asked, so they pass basic image checks (real
    # dimensions, resolves fine) while carrying zero content specific to the
    # article (e.g. algodirectory.app/og/Explore on an unrelated story about
    # ALGO collateral — 2026-07-12).
    return "/og/" in path or "opengraph" in path


def article_path(article_id: str, slug: str | None = None, lang: str | None = None) -> str:
    """Site-relative path for an article's canonical page, locale-prefixed for translations.

    Prefers the permanent slug (migration 056); falls back to the article id so
    rows written before the backfill, and any article whose slug is somehow
    missing, still resolve. The route accepts both forms and 301s id -> slug.

    Non-English locales live under a path segment (``/fr/news/articles/slug``),
    not the ``?lang=fr`` query parameter this used to emit. Google's
    multi-regional guidance is the only URL structure it actively advises
    against; Yandex -- which matters here, since Russian is the top-performing
    locale and Yandex is most of Russian search -- handles path segments far
    more predictably; and query strings get stripped by link sharers and
    shorteners, which the Telegram distribution plan depends on. The old form
    still resolves: the bare route 301s ``?lang=xx`` here (see seo.api.routes).
    """
    base = f"/news/articles/{slug or article_id}"
    code = (lang or "").strip()
    if code and code != "en":
        return f"/{code}{base}"
    return base


def article_url(article_id: str, lang: str | None = None, slug: str | None = None) -> str:
    """Absolute article URL; non-English locales are locale-prefixed paths."""
    return absolute(article_path(article_id, slug, lang))


def article_hreflang_links(
    article_id: str, translation_langs: list[str] | None, slug: str | None = None
) -> list[tuple[str, str]]:
    """(hreflang BCP-47 tag, absolute URL) pairs including x-default."""
    base = article_url(article_id, slug=slug)
    links: list[tuple[str, str]] = [
        ("x-default", base),
        (SEO_HREFLANG_LOCALES["en"], base),
    ]
    seen = {"en", "x-default"}
    for code in translation_langs or []:
        if code in seen:
            continue
        hreflang = SEO_HREFLANG_LOCALES.get(code)
        if not hreflang:
            continue
        links.append((hreflang, article_url(article_id, code, slug)))
        seen.add(code)
    return links


# Readable fallback styling for the pre-boot paint (and no-JS readers); the
# SPA replaces it once mounted. Kept tiny and inline so the SSR body
# needs no extra request.
_SSR_STYLE = (
    "<style>"
    # Paints the paper background under #ssr-body's own gutters too, so there
    # is no flash of default-white margin around the centered column.
    # background-COLOR, not the `background` shorthand: the shorthand resets
    # background-image, which silently wiped the SPA's masthead wash on
    # every SSR-rendered page (the stylesheet loads after this block).
    "html,body{background-color:var(--surface,"
    + token("surface")
    + ")}"
    + SSR_CHROME_STYLE
    # The loading notice only exists for humans watching the app boot, so it is
    # hidden from the reading flow's start: JS reveals it, and it dies with the
    # div on first frame. No-JS readers and crawlers never see it. Styled as a
    # small kicker label (matching the share card's kicker treatment) rather
    # than an apologetic status line, since the content right below it is the
    # real page, not a placeholder.
    + "#ssr-loading{display:none;font:600 11px/1.4 system-ui,sans-serif;"
    "letter-spacing:.06em;text-transform:uppercase;color:" + token("primary") + ";margin:0 0 18px}"
    "</style>"
)
_SSR_LOADING = (
    '<p id="ssr-loading">Live edition loading…</p>'
    "<script>document.getElementById('ssr-loading').style.display='block';</script>"
)
# The SPA dispatches `pxke-spa-ready` on window once it has mounted and painted.
# Until then #ssr-body is the fast first paint (and the no-JS fallback); after,
# it would just be duplicate text sitting under the app, which browser find
# would still match. The timeout is a safety net only: if the app throws before
# dispatching, the SSR content stays readable rather than being torn out.
#
# The title restore exists because the SPA sets document.title on mount, which
# clobbers the per-route <title> injected here — so any crawler that RENDERS
# the page (Bing's does, Google's WRS does) saw one generic title site-wide
# (flagged in the 2026-07-09 Bing audit). Capturing the server-sent value at
# parse time and restoring it after mount wins durably.
_SSR_REMOVE_SCRIPT = (
    "<script>var pxkeSsrTitle=document.title;"
    "function pxkeDropSsr(){"
    "var b=document.getElementById('ssr-body');b&&b.remove();"
    "var f=document.getElementById('pxke-ssr-feed');f&&f.remove();"
    "setTimeout(function(){if(pxkeSsrTitle){document.title=pxkeSsrTitle;}},0);}"
    "window.addEventListener('pxke-spa-ready',pxkeDropSsr,{once:true});"
    "</script>"
)


def ssr_container(
    inner_html: str,
    *,
    active: str | None = None,
    breadcrumbs: list[tuple[str, str]] | None = None,
    topic_links: list[tuple[str, int]] | None = None,
) -> str:
    """Wrap SSR page markup in the crawlable ssr-body div plus its removal script."""
    page = ssr_page(
        inner_html,
        active=active,
        breadcrumbs=breadcrumbs,
        topic_links=topic_links,
    )
    return f'{_SSR_STYLE}<div id="ssr-body">{_SSR_LOADING}{page}</div>{_SSR_REMOVE_SCRIPT}'


def registry_ssr_container(
    inner_html: str, *, active: str | None = None, breadcrumbs: list[tuple[str, str]] | None = None
) -> str:
    """Same wrapper as ssr_container, but the registry product's own chrome (chrome.registry_ssr_page) -- see render_registry_index/render_registry_entry."""
    page = registry_ssr_page(inner_html, active=active, breadcrumbs=breadcrumbs)
    return f'{_SSR_STYLE}<div id="ssr-body">{_SSR_LOADING}{page}</div>{_SSR_REMOVE_SCRIPT}'


def pick_related_articles(
    article: ArticleDetail,
    feed: list[ArticleFeedItem],
    *,
    limit: int = 5,
) -> list[ArticleFeedItem]:
    """Stories sharing a TOPICAL tag with this article (mirrors the SPA detail page).

    Meta/provenance tags (discovery, web, updated, news, ...) are excluded from
    the match, same as the SPA's primaryTopic() -- without this, any two
    service-discovery articles "match" regardless of subject, since almost
    every one of them carries "discovery"/"web" (found 2026-08-09: a DeFi
    swap-aggregator piece matched 24 unrelated articles, nearly all of them
    sharing nothing but those two provenance tags).
    """
    tags = {t.strip().lower() for t in (article.tags or []) if t.strip() and not is_meta_tag(t)}
    if not tags:
        return []
    related: list[ArticleFeedItem] = []
    for item in feed:
        if item.article_id == article.article_id:
            continue
        item_tags = {
            t.strip().lower() for t in (item.tags or []) if t.strip() and not is_meta_tag(t)
        }
        if tags & item_tags:
            related.append(item)
        if len(related) >= limit:
            break
    return related


def _tag_links_html(tags: list[str] | None) -> str:
    if not tags:
        return ""
    links = []
    for raw in tags:
        tag = raw.strip()
        if not tag:
            continue
        slug = tag.lower()
        links.append(f'<a href="{_attr(f"/topic/{slug}")}" rel="tag">{html.escape(tag)}</a>')
    if not links:
        return ""
    return f'<p class="ssr-tags">{" · ".join(links)}</p>'


_LANG_LABELS: dict[str, str] = {
    "en": "English",
    **{code: name.split(" (")[0] for code, name in ARTICLE_TRANSLATION_LANG_NAMES.items()},
}


def _translation_links_html(
    article_id: str,
    current_lang: str | None,
    translation_langs: list[str] | None,
    slug: str | None = None,
) -> str:
    langs = ["en", *(c for c in (translation_langs or []) if c != "en")]
    if len(langs) <= 1:
        return ""
    current = (current_lang or "en").strip() or "en"
    parts = []
    for code in langs:
        path = article_path(article_id, slug, code)
        label = _LANG_LABELS.get(code, code)
        hreflang = SEO_HREFLANG_LOCALES.get(code, code)
        if code == current:
            # hreflang has no formal meaning on a non-link element, but keeps this
            # entry visually/structurally consistent with the head's <link
            # hreflang> for the same language (readers scraping the body picker
            # shouldn't see the current language as the one missing a tag).
            parts.append(
                f'<span aria-current="true" hreflang="{_attr(hreflang)}">'
                f"{html.escape(label)}</span>"
            )
        else:
            parts.append(
                f'<a href="{_attr(path)}" hreflang="{_attr(hreflang)}">{html.escape(label)}</a>'
            )
    return (
        f'<nav class="ssr-langs" aria-label="Translations">'
        f"<p>Read in: {' · '.join(parts)}</p></nav>"
    )


def _related_stories_html(items: list[ArticleFeedItem]) -> str:
    if not items:
        return ""
    links = "".join(
        f'<li><a href="{_attr(article_path(item.article_id, item.slug))}">{html.escape(item.title)}</a></li>'
        for item in items
    )
    return (
        f'<aside class="ssr-related" aria-labelledby="ssr-related-h">'
        f'<h2 id="ssr-related-h">Related stories</h2><ul>{links}</ul></aside>'
    )


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


def render_article(
    article: ArticleDetail,
    *,
    lang: str | None = None,
    translation_langs: list[str] | None = None,
    topic_links: list[tuple[str, int]] | None = None,
    related: list[ArticleFeedItem] | None = None,
) -> tuple[str, str]:
    """Render an article's SSR head markup and body HTML."""
    lang_code = (lang or "").strip() or None
    if lang_code == "en":
        lang_code = None
    canonical = article_url(article.article_id, lang_code, article.slug)
    image, is_default = _image_for(article.image_url)
    # A brand-icon fallback (favicon/logo) is tile art, not a share image or
    # banner — skip it for the body hero below same as a missing image.
    icon_like = bool(article.image_url) and is_icon_like(image)
    og_card = is_default or icon_like
    if og_card:
        # No real photo at all, or only a source favicon/logo: a generated
        # share card (title + kicker on the paper background, see
        # seo/share_card.py) beats the generic square app icon every article
        # without one otherwise shared — proper 1200x630 aspect, and it
        # actually names the story instead of repeating the same tile.
        image, is_default = absolute(f"/og/article/{article.article_id}.png"), True
    body_text = md_to_text(article.body)
    description = truncate(article.summary or body_text, 160)
    published_iso = _iso(article.published_at_epoch)
    # dateModified reflects the last edit/recompose; equals datePublished for
    # never-revised articles (crawlers treat a fresher dateModified as a
    # recrawl signal — the long-standing Bing-audit gap).
    updated_epoch = getattr(article, "updated_at_epoch", None) or 0
    modified_iso = (
        _iso(updated_epoch) if updated_epoch > article.published_at_epoch else published_iso
    )

    trail = [("Home", site_url() + "/")]
    # Breadcrumb through the story's primary writer tag — the paper's real
    # taxonomy (the fixed human sections were retired). The URL always uses
    # the raw tag slug (matches /topic/<tag> in both the app and here); only
    # the visible label goes through display_tag_label ("chain-only" ->
    # "on-chain") — same split the share-card kicker uses.
    primary = primary_tag(article.tags)
    primary_label = display_tag_label(primary) if primary else None
    if primary:
        trail.append((primary_label, absolute(f"/topic/{primary}")))
    trail.append((truncate(article.title, 80), canonical))

    news_article = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": truncate(article.title, 110),
        "description": description,
        "datePublished": published_iso,
        "dateModified": modified_iso,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "image": [image],
        "articleBody": body_text,
        "wordCount": _word_count(body_text),
        "publisher": _publisher(),
        "author": _publisher(),
        "keywords": ", ".join(article.tags or []),
        "isAccessibleForFree": True,
        "speakable": _speakable_jsonld(),
    }
    if primary:
        news_article["articleSection"] = primary_label
    if lang_code:
        news_article["inLanguage"] = html_lang_for(lang_code)

    hreflang_links = article_hreflang_links(article.article_id, translation_langs, article.slug)
    current_og = og_locale_for(lang_code)
    og_alternates = sorted(
        {
            og_locale_for(code)
            for code in (["en", *(translation_langs or [])])
            if og_locale_for(code) != current_og
        }
    )

    head = _meta_block(
        title=article.title,
        description=description,
        canonical=canonical,
        image=image,
        og_type="article",
        published_iso=published_iso,
        modified_iso=modified_iso,
        tags=article.tags,
        image_alt=article.title,
        image_dims=_OG_CARD_DIMS if og_card else None,
        json_ld=[news_article, _breadcrumb(trail)],
        og_locale=current_og,
        og_locale_alternates=og_alternates,
        hreflang_links=hreflang_links,
        og_section=primary_label,
        lang=html_lang_for(lang_code) if lang_code else "en",
    )

    body_html = md_to_html(article.body)
    # Standalone hero image — but only when the article BODY doesn't already
    # embed the same image (writers often lead the markdown with the OG image,
    # which rendered the hero twice back-to-back at the top of the document).
    img_html = ""
    if article.image_url and not icon_like and _attr(image) not in body_html:
        hero_src = image if og_card else _content_img_src(article.image_url)
        img_html = (
            f'<img src="{_attr(hero_src)}" alt="{_attr(article.title)}" '
            f'width="1200" height="630" decoding="async">'
        )
    source = (
        f'<p>Source: <a href="{_attr(article.source_url)}" rel="noopener nofollow">'
        f"{_attr(article.source_url)}</a></p>"
        if article.source_url
        else ""
    )
    tags_html = _tag_links_html(article.tags)
    langs_html = _translation_links_html(
        article.article_id, lang_code, translation_langs, article.slug
    )
    related_html = _related_stories_html(related or [])
    body = ssr_container(
        f'<p class="ssr-back"><a href="/news">← Latest stories</a></p>'
        f"<article><h1>{html.escape(article.title)}</h1>"
        f'<p><time datetime="{_attr(published_iso)}">{published_iso[:10]}</time></p>'
        f"{tags_html}{img_html}{body_html}{source}{langs_html}</article>"
        f"{related_html}",
        breadcrumbs=trail,
        topic_links=topic_links,
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
                    "url": absolute(article_path(item.article_id, item.slug)),
                    "name": item.title,
                }
                for i, item in enumerate(items)
            ],
        },
    }


def _topics_index_jsonld(tags: list[tuple[str, int]], canonical: str, title: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": title,
        "url": canonical,
        "publisher": _publisher(),
        "mainEntity": {
            "@type": "ItemList",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "url": absolute(f"/topic/{tag}"),
                    "name": tag,
                }
                for i, (tag, _count) in enumerate(tags)
            ],
        },
    }


def _story_li(item: ArticleFeedItem, *, rank: int | None = None) -> str:
    prefix = f"{rank}. " if rank is not None else ""
    return (
        f'<li>{prefix}<a href="{_attr(article_path(item.article_id, item.slug))}">'
        f"{html.escape(item.title)}</a> — {html.escape(truncate(item.summary, 140))}</li>"
    )


def _lead_index(items: list[ArticleFeedItem]) -> int:
    """Prefer the newest story with a real photograph as the front-page lead."""
    window = min(5, len(items))
    for i in range(window):
        url = items[i].image_url
        if url and not is_icon_like(absolute(url)):
            return i
    return 0


def _lead_html(item: ArticleFeedItem) -> str:
    img = ""
    if item.image_url and not is_icon_like(absolute(item.image_url)):
        src = _attr(_content_img_src(item.image_url))
        img = (
            f'<img src="{src}" alt="{_attr(item.title)}" '
            f'width="680" height="425" fetchpriority="high" decoding="async">'
        )
    path = _attr(article_path(item.article_id, item.slug))
    return (
        f'<article class="ssr-lead">'
        f'<h1><a href="{path}">{html.escape(item.title)}</a></h1>'
        f"{img}"
        f"<p>{html.escape(truncate(item.summary or '', 220))}</p>"
        f"</article>"
    )


def _feed_ssr(
    items: list[ArticleFeedItem],
    heading: str,
    *,
    active: str | None = None,
    breadcrumbs: list[tuple[str, str]] | None = None,
    topic_links: list[tuple[str, int]] | None = None,
    intro_html: str = "",
) -> str:
    """Crawlable (and pre-boot visible) feed listing — the internal links a crawler sees without executing the SPA's client-side router."""
    links = "".join(_story_li(item) for item in items)
    return ssr_container(
        f"<h1>{html.escape(heading)}</h1>{intro_html}<ul>{links}</ul>",
        active=active,
        breadcrumbs=breadcrumbs,
        topic_links=topic_links,
    )


def render_front(
    items: list[ArticleFeedItem],
    hot: list[ArticleFeedItem],
    *,
    topic_links: list[tuple[str, int]] | None = None,
) -> tuple[str, str]:
    """Editorial front page at / — mirrors the SPA front-page layout."""
    canonical = site_url() + "/"
    # Front-page <title>: what the paper IS, keyword-first, brand last — the
    # bare brand drew zero clicks on its own SERP (task #39). Ends with
    # site_name so _meta_block doesn't suffix the brand a second time; total
    # stays under the ~65-char clamp.
    front_title = f"Daily Algorand Ecosystem News & Analysis — {settings.site_name}"
    if not items:
        head = _meta_block(
            title=front_title,
            description=settings.site_tagline,
            canonical=canonical,
            image=absolute(settings.seo_default_image),
            image_alt=settings.site_name,
            image_dims=_DEFAULT_IMAGE_DIMS,
            json_ld=[
                _website_jsonld(),
                {"@context": "https://schema.org", **_publisher(), "url": canonical},
            ],
        )
        body = ssr_container(
            f"<h1>{html.escape(settings.site_name)}</h1>"
            f"<p>{html.escape(settings.site_tagline)}</p>",
            breadcrumbs=[("Home", canonical)],
            topic_links=topic_links,
        )
        return head, body

    lead_idx = _lead_index(items)
    lead = items[lead_idx]
    others = [item for i, item in enumerate(items) if i != lead_idx]
    secondary = others[:4]
    rest = others[4:]

    sections: list[str] = [
        '<div class="ssr-front">',
        f'<section aria-labelledby="ssr-lead-h">{_lead_html(lead)}</section>',
    ]
    if secondary:
        sec_links = "".join(_story_li(item) for item in secondary)
        sections.append(
            f'<section aria-labelledby="ssr-top-h">'
            f'<h2 id="ssr-top-h">Top stories</h2><ul>{sec_links}</ul></section>'
        )
    if hot:
        hot_links = "".join(_story_li(item, rank=i + 1) for i, item in enumerate(hot))
        sections.append(
            f'<section aria-labelledby="ssr-hot-h">'
            f'<h2 id="ssr-hot-h"><a href="/hot">Most read</a></h2>'
            f"<ol>{hot_links}</ol></section>"
        )
    if rest:
        rest_links = "".join(_story_li(item) for item in rest)
        sections.append(
            f'<section aria-labelledby="ssr-more-h">'
            f'<h2 id="ssr-more-h">More news</h2><ul>{rest_links}</ul></section>'
        )
    sections.append(
        '<p class="ssr-more-feed"><a href="/news">Full chronological feed →</a></p></div>'
    )
    main_html = "".join(sections)

    head = _meta_block(
        title=front_title,
        description=settings.site_tagline,
        canonical=canonical,
        image=absolute(lead.image_url)
        if lead.image_url and not is_icon_like(absolute(lead.image_url))
        else absolute(settings.seo_default_image),
        image_alt=lead.title,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[
            _website_jsonld(),
            {"@context": "https://schema.org", **_publisher(), "url": canonical},
            _feed_list_jsonld(items, canonical, f"{settings.site_name} — Front page"),
        ],
    )
    if lead.image_url and not is_icon_like(absolute(lead.image_url)):
        lcp = _attr(_content_img_src(lead.image_url))
        head = f'{head}\n<link rel="preload" as="image" href="{lcp}" fetchpriority="high">'
    head = f"{head}\n{_ssr_feed_script(items)}"
    body = ssr_container(
        main_html,
        breadcrumbs=[("Home", canonical)],
        topic_links=topic_links,
    )
    return head, body


def render_news_feed(
    items: list[ArticleFeedItem],
    *,
    topic_links: list[tuple[str, int]] | None = None,
    total_count: int | None = None,
) -> tuple[str, str]:
    """Chronological file at /news."""
    # Canonicalised to the front page, not self-referential: the two carry the
    # same stories and measured 95% identical text, which is the definition of
    # a duplicate. Left self-canonical, Search Console reported "Duplicate
    # without user-selected canonical" and picked one of them itself. The
    # homepage is the stronger URL, so point at it and let /news stay a
    # crawlable, linkable route that simply is not indexed separately.
    canonical = site_url() + "/"
    self_url = absolute("/news")
    breadcrumbs = [("Home", site_url() + "/"), ("Latest", self_url)]
    heading = f"{settings.site_name} — Latest"
    intro = ""
    if total_count is not None and total_count > len(items):
        intro = (
            f'<p class="ssr-muted">Showing the {len(items)} newest of {total_count} '
            f"recent stories in the archive. "
            f'<a href="/feed.xml">Subscribe via RSS</a> for the full syndicated feed.</p>'
        )
    head = _meta_block(
        title="Latest",
        description=settings.site_tagline,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        robots="noindex, follow",
        json_ld=[
            # self_url: the ItemList describes THIS page's contents,
            # whereas the canonical points at the front page.
            _feed_list_jsonld(items, self_url, heading),
            _breadcrumb(breadcrumbs),
        ],
    )
    head = f"{head}\n{_ssr_feed_script(items)}"
    body = _feed_ssr(
        items,
        heading,
        active="/news",
        breadcrumbs=breadcrumbs,
        topic_links=topic_links,
        intro_html=intro,
    )
    return head, body


def render_home(
    items: list[ArticleFeedItem],
    *,
    canonical_path: str = "/news",
    topic_links: list[tuple[str, int]] | None = None,
) -> tuple[str, str]:
    """Backward-compatible alias for the /news feed renderer."""
    _ = canonical_path  # only /news is supported; / uses render_front
    return render_news_feed(items, topic_links=topic_links)


def render_hot(
    items: list[ArticleFeedItem],
    *,
    topic_links: list[tuple[str, int]] | None = None,
    canonical_path: str = "/hot",
) -> tuple[str, str]:
    """Most-read ledger: the feed ranked by read tally. Serves both /hot (recency-weighted) and /top (all-time) — canonical_path keeps them from claiming each other's URL."""
    canonical = absolute(canonical_path)
    label = "Top stories" if canonical_path == "/top" else "Most read"
    title = f"{label} — {settings.site_name}"
    trail = [("Home", site_url() + "/"), (label, canonical)]
    head = _meta_block(
        title="Most read",
        description=f"The {settings.site_name} stories readers are opening most right now.",
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[_feed_list_jsonld(items, canonical, title), _breadcrumb(trail)],
    )
    head = f"{head}\n{_ssr_feed_script(items)}"
    body = _feed_ssr(
        items,
        "Most read",
        active="/hot",
        breadcrumbs=trail,
        topic_links=topic_links,
    )
    return head, body


def render_topic(
    tag: str,
    items: list[ArticleFeedItem],
    *,
    topic_links: list[tuple[str, int]] | None = None,
    total_count: int | None = None,
    indexable: bool = True,
) -> tuple[str, str]:
    """Topic landing page: the feed filtered to one writer tag."""
    canonical = absolute(f"/topic/{tag}")
    # The reader-facing label, not the raw slug: titles read "DeFi — Algorand
    # news" rather than "defi — …". The slug still builds the URL, which is why
    # only the display strings go through display_tag_label.
    label = display_tag_title(tag)
    title = f"{label} — Algorand news"
    description = f"Algorand stories tagged “{label}” from {settings.site_name}."
    trail = [("Home", site_url() + "/"), ("Topics", absolute("/topics")), (label, canonical)]
    feed_path = topic_feed_path(tag)
    head = _meta_block(
        title=title,
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        robots=None if indexable else "noindex, follow",
        json_ld=[_feed_list_jsonld(items, canonical, title), _breadcrumb(trail)],
    )
    head += (
        f'\n<link rel="alternate" type="application/rss+xml" '
        f'title="{_attr(f"{label} — {settings.site_name}")}" '
        f'href="{_attr(absolute(feed_path))}">'
    )
    head = f"{head}\n{_ssr_feed_script(items)}"
    intro = (
        f'<p class="ssr-muted"><a href="{_attr(feed_path)}">Subscribe to this topic (RSS)</a></p>'
    )
    if total_count is not None and total_count > len(items):
        intro += (
            f'<p class="ssr-muted">Showing {len(items)} of {total_count} stories '
            f"tagged “{html.escape(tag)}”.</p>"
        )
    body = _feed_ssr(
        items,
        tag,
        active="/topics",
        breadcrumbs=trail,
        topic_links=topic_links,
        intro_html=intro,
    )
    return head, body


def render_topics(tags: list[tuple[str, int]]) -> tuple[str, str]:
    """Topics index: crawlable links to every reliable topic page."""
    canonical = absolute("/topics")
    title = f"Topics — {settings.site_name}"
    description = f"Every topic {settings.site_name} covers, ranked by coverage."
    trail = [("Home", site_url() + "/"), ("Topics", canonical)]
    head = _meta_block(
        title="Topics",
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[
            _topics_index_jsonld(tags, canonical, title),
            _breadcrumb(trail),
        ],
    )
    links = "".join(
        f'<li><a href="{_attr(absolute(f"/topic/{tag}"))}">{html.escape(tag)}</a>'
        f" — {count} stories "
        f'(<a href="{_attr(topic_feed_path(tag))}">RSS</a>)</li>'
        for tag, count in tags
    )
    body = ssr_container(
        f"<h1>{html.escape(title)}</h1><ul>{links}</ul>",
        active="/topics",
        breadcrumbs=trail,
        topic_links=tags,
    )
    return head, body


def render_glossary_index(terms: list[GlossaryTerm]) -> tuple[str, str]:
    """Glossary index: crawlable links to every published term, schema.org DefinedTermSet."""
    canonical = absolute("/glossary")
    title = f"Glossary — {settings.site_name}"
    description = f"Plain-language definitions for {settings.site_name} readers."
    trail = [("Home", site_url() + "/"), ("Glossary", canonical)]
    json_ld = {
        "@context": "https://schema.org",
        "@type": "DefinedTermSet",
        "@id": canonical,
        "name": title,
        "url": canonical,
        "hasDefinedTerm": [
            {
                "@type": "DefinedTerm",
                "name": t.term,
                "url": absolute(f"/glossary/{t.slug}"),
            }
            for t in terms
        ],
    }
    head = _meta_block(
        title="Glossary",
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[json_ld, _breadcrumb(trail)],
    )
    links = "".join(
        f'<li><a href="{_attr(absolute(f"/glossary/{t.slug}"))}">{html.escape(t.term)}</a>'
        f" — {html.escape(t.definition[:140])}</li>"
        for t in terms
    )
    body = ssr_container(
        f"<h1>{html.escape(title)}</h1><ul>{links}</ul>",
        active="/glossary",
        breadcrumbs=trail,
    )
    return head, body


def render_glossary_term(term: GlossaryTerm) -> tuple[str, str]:
    """One glossary term's SSR page, schema.org DefinedTerm."""
    canonical = absolute(f"/glossary/{term.slug}")
    title = f"{term.term} — {settings.site_name} Glossary"
    description = term.definition[:280]
    trail = [
        ("Home", site_url() + "/"),
        ("Glossary", absolute("/glossary")),
        (term.term, canonical),
    ]
    json_ld = {
        "@context": "https://schema.org",
        "@type": "DefinedTerm",
        "@id": canonical,
        "name": term.term,
        "description": term.definition,
        "url": canonical,
        "inDefinedTermSet": absolute("/glossary"),
    }
    head = _meta_block(
        title=title,
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[json_ld, _breadcrumb(trail)],
    )
    aliases_html = ""
    if term.aliases:
        aliases_html = (
            '<p class="ssr-muted">Also known as: ' + html.escape(", ".join(term.aliases)) + "</p>"
        )
    body = ssr_container(
        f"<h1>{html.escape(term.term)}</h1><p>{html.escape(term.definition)}</p>{aliases_html}",
        active="/glossary",
        breadcrumbs=trail,
    )
    return head, body


# --- Algorand Open Registry (algorand-registry.pxke.me) ---------------------
#
# SSR mirror of frontend/src/routes/Registry.svelte and RegistryEntry.svelte.
# A SEPARATE product on its own subdomain -- every canonical/OG/breadcrumb
# URL below is built with registry_absolute()/registry_site_url(), never the
# news site's absolute()/site_url(), and the chrome is registry_ssr_container
# (its own nav/brand/footer), never ssr_container. Design doc section 6.2:
# "one index renderer, one detail renderer" -- reads the SAME store calls
# the JSON routes (app.modules.ecosystem.api.routes) use, passed in by the
# caller (seo/api/routes.py), not a second copy of that read.
_REGISTRY_DEFAULT_IMAGE_DIMS = (512, 512)
_REGISTRY_TAGLINE = (
    "A free, human-reviewed directory of Algorand projects — wallets, DeFi, NFTs, tooling and more."
)


def render_registry_index(entries: list[StoredProject]) -> tuple[str, str]:
    """Registry index: every approved entry, schema.org ItemList."""
    canonical = registry_site_url() + "/"
    title = "Algorand Open Registry"
    description = _REGISTRY_TAGLINE
    json_ld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "@id": canonical,
        "name": title,
        "url": canonical,
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": i + 1,
                "url": registry_absolute(f"/registry/{item.slug}"),
                "name": item.name,
            }
            for i, item in enumerate(entries)
        ],
    }
    head = _meta_block(
        title=title,
        description=description,
        canonical=canonical,
        image=registry_absolute(settings.seo_default_image),
        image_alt=title,
        image_dims=_REGISTRY_DEFAULT_IMAGE_DIMS,
        json_ld=[json_ld],
    )
    links = "".join(
        f'<li><a href="{_attr(registry_absolute(f"/registry/{item.slug}"))}">'
        f"{html.escape(item.name)}</a> — {html.escape(category_label(item.category))}"
        f"<br>{html.escape(item.description[:160])}</li>"
        for item in entries
    )
    body = registry_ssr_container(
        f"<h1>{html.escape(title)}</h1><p>{html.escape(description)}</p><ul>{links}</ul>",
        active="/registry",
        breadcrumbs=[("Registry", canonical)],
    )
    return head, body


def render_registry_entry(entry: StoredProject) -> tuple[str, str]:
    """One registry entry's SSR page, schema.org SoftwareApplication."""
    canonical = registry_absolute(f"/registry/{entry.slug}")
    title = f"{entry.name} — Algorand Open Registry"
    description = entry.description[:280]
    trail = [
        ("Registry", registry_site_url() + "/"),
        (
            category_label(entry.category),
            registry_absolute(f"/registry?category={quote(entry.category)}"),
        ),
        (entry.name, canonical),
    ]
    json_ld = {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "@id": canonical,
        "name": entry.name,
        "description": entry.description,
        "url": canonical,
        "applicationCategory": category_label(entry.category),
        "sameAs": [u for u in (entry.url, entry.repo_url) if u],
    }
    head = _meta_block(
        title=title,
        description=description,
        canonical=canonical,
        image=registry_absolute(settings.seo_default_image),
        image_alt=entry.name,
        image_dims=_REGISTRY_DEFAULT_IMAGE_DIMS,
        json_ld=[json_ld, _breadcrumb(trail)],
    )
    links_html = ""
    if entry.url:
        links_html += f'<p><a href="{_attr(entry.url)}">Visit site</a></p>'
    if entry.repo_url:
        links_html += f'<p><a href="{_attr(entry.repo_url)}">Source code</a></p>'
    body = registry_ssr_container(
        f"<h1>{html.escape(entry.name)}</h1><p>{html.escape(entry.description)}</p>{links_html}",
        active="/registry",
        breadcrumbs=trail,
    )
    return head, body


def render_registry_noindex(title: str, *, path: str) -> tuple[str, str]:
    """Minimal registry-chrome shell for utility routes (submit, suggest-a-change) -- keep them out of the index but still serve the app. Mirrors render_noindex, registry-scoped."""
    canonical = registry_absolute(path)
    head = _meta_block(
        title=title,
        description=_REGISTRY_TAGLINE,
        canonical=canonical,
        image=registry_absolute(settings.seo_default_image),
        image_alt="Algorand Open Registry",
        image_dims=_REGISTRY_DEFAULT_IMAGE_DIMS,
        robots="noindex, follow",
    )
    body = registry_ssr_container(
        f"<h1>{html.escape(title)}</h1>",
        active=None,
        breadcrumbs=[("Registry", registry_site_url() + "/"), (title, canonical)],
    )
    return head, body


def render_about() -> tuple[str, str]:
    """Render the static About page's SSR head markup and body HTML."""
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
        f"<h2>Written with AI</h2><p>{html.escape(disclosure)}</p>",
        active="/about",
        breadcrumbs=[("Home", site_url() + "/"), ("About", canonical)],
    )
    return head, body


def render_contact() -> tuple[str, str]:
    """Render the static Contact page's SSR head markup and body HTML."""
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
        "feedback go straight to the editors.</p>",
        active="/contact",
        breadcrumbs=[("Home", site_url() + "/"), ("Contact", canonical)],
    )
    return head, body


def render_noindex(title: str, *, active: str | None = None) -> tuple[str, str]:
    """Minimal shell for utility routes (admin/search/suggestions) — keep them out of the index but still serve the app."""
    # Self-referential canonical, not the homepage. noindex plus a canonical
    # pointing somewhere else are contradictory instructions — one says "drop
    # this page", the other says "credit it to /" — and Google resolves the
    # conflict however it likes. These pages are not duplicates of the front
    # page either, which is what "Duplicate, Google chose different canonical
    # than user" reports. noindex alone says exactly what we mean.
    canonical = absolute(active) if active else site_url() + "/"
    head = _meta_block(
        title=title,
        description=settings.site_tagline,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        robots="noindex, follow",
    )
    body = ssr_container(
        f"<h1>{html.escape(title)}</h1>",
        active=active,
        breadcrumbs=[("Home", site_url() + "/"), (title, site_url() + "/")],
    )
    return head, body


# --- x402 agent marketplace (SSR mirror of frontend/src/routes/X402.svelte) ---
#
# Tabs match the SPA's X402Tab type exactly (App.svelte's matchPath). An
# unknown :tab value is handled by the caller (seo/api/routes.py's x402
# handler) resolving it to "directory" before this module ever sees it --
# the same fallback the SPA's router applies -- so every function here can
# assume `tab` is one of the five below.

# "news" is deliberately still a valid key in every dict below (its own
# section on the endpoints page reuses that description/label text) even
# though it is no longer a Marketplace sub-tab -- see render_x402's
# "endpoints" branch. Frontend split 2026-09-06: the SPA's own X402Tab type
# dropped 'news' the same way (frontend/src/App.svelte) once News moved to
# its own page at /x402/endpoints alongside PXke's other own-product catalog
# entries, separate from the Marketplace (directory/board/requests/grades),
# which is about OTHER agents' listed endpoints, not ours.
X402_TABS: tuple[str, ...] = ("directory", "board", "requests", "grades")

_X402_TAB_PATHS: dict[str, str] = {
    "directory": "/x402",
    "board": "/x402/board",
    "requests": "/x402/requests",
    "grades": "/x402/grades",
    "news": "/x402/news",
    "endpoints": "/x402/endpoints",
}

_X402_TAB_NAV_LABELS: dict[str, str] = {
    "directory": "Directory",
    "board": "Board",
    "requests": "Requests",
    "grades": "Grades",
    "news": "News",
    "endpoints": "Our Endpoints",
}

# <title>/meta description per tab -- mirrors x402PricingHeading/x402Lead
# copy from frontend/src/lib/i18n/locales/en.json, condensed for crawlers.
_X402_TAB_HEAD_TITLES: dict[str, str] = {
    "directory": "x402 Endpoint Directory",
    "board": "x402 Visibility Board",
    "requests": "x402 Feature Requests",
    "grades": "x402 Endpoint Grades",
    "news": "x402 News Engine",
    "endpoints": "PXke's x402 Endpoints",
}

_X402_TAB_DESCRIPTIONS: dict[str, str] = {
    "directory": (
        "Browse x402 endpoints for sale to AI agents on Algorand mainnet, priced in "
        "USDC and paid per call. Listing costs $0.02 and stays live as long as the "
        "endpoint keeps passing health probes -- no renewal needed. Browsing is free. "
        "Filter the free search with ?tag=<tag> or ?category=<category>."
    ),
    "board": (
        "The x402 paid visibility board: agents pay $0.05 in USDC to place a link "
        "and pitch on Algorand mainnet for a fixed 14 days, with a paid boost "
        "available for top placement during that window. Free to browse."
    ),
    "requests": (
        "Feature requests agents want built as x402 endpoints. Filing is free; "
        "votes cost $0.02 in USDC on Algorand mainnet and rank the demand."
    ),
    "grades": (
        "x402 endpoints agents have graded after paying them, on Algorand mainnet. "
        "Grading costs $0.02 in USDC; the weighted score is a paid read."
    ),
    "news": (
        "The x402 News Engine: pay-per-call access to the PXke Algorand newspaper. "
        "Headlines and full articles are free; full-text search costs $0.001, "
        "settled in USDC on Algorand mainnet."
    ),
    "endpoints": (
        "PXke's own x402 endpoints on Algorand mainnet, paid per call in USDC: the "
        "News Engine, agent backup storage, uptime/reachability checks, a sandboxed "
        "file scan, and the agent social network. These are also listed in our open "
        "marketplace directory, the same as anyone else's. See the live catalog for "
        "exact prices."
    ),
}

# Mirrors the Svelte page's pricing dl (x402Price*Label/Value keys) and the
# settings the routes actually charge (x402_listing_price etc.) -- static
# copy, not a live settings read, same as the SPA's own hardcoded strings.
_X402_PRICING_ROWS: tuple[tuple[str, str], ...] = (
    ("List an endpoint (while healthy)", "$0.02"),
    ("Place on the board (14 days)", "$0.05"),
    ("File a feature request", "free"),
    ("Vote on a request", "$0.02"),
    ("Read ranked demand", "$0.05"),
    ("Grade an endpoint", "$0.02"),
    ("Read a score", "$0.03"),
    ("Read one news article", "free"),
    ("Search news articles", "$0.001"),
)

# (label, free GET path) -- mirrors X402.svelte's `endpoints` derived list.
_X402_AGENT_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("Directory", "/api/v1/x402/search?tag="),
    ("Board", "/api/v1/x402/board"),
    ("Requests", "/api/v1/x402/features"),
    ("Grades", "/api/v1/x402/grades"),
    ("News", "/api/v1/x402/news"),
)


def _x402_short_addr(address: str) -> str:
    """Mirror X402.svelte's shortAddr(): first 6 + last 4 chars of a long wallet address."""
    return f"{address[:6]}…{address[-4:]}" if len(address) > 12 else address


def _x402_epoch_date(epoch: int | None) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d")


def _x402_host(url: str) -> str:
    """Mirror X402.svelte's hostOf(): the link's host, or the raw string if unparseable."""
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


def _x402_is_http(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def _x402_intro_html() -> str:
    """Static marketplace explanation + pricing table + "for agents" box, shared by every tab."""
    price_rows = "".join(
        f"<div class='ssr-x402-price-row'><span>{html.escape(label)}</span>"
        f"<span>{html.escape(price)}</span></div>"
        for label, price in _X402_PRICING_ROWS
    )
    endpoint_items = "".join(
        f"<li>{html.escape(label)}: <code>GET {html.escape(absolute(path))}</code></li>"
        for label, path in _X402_AGENT_ENDPOINTS
    )
    curl_example = (
        f"curl -X POST {absolute('/api/v1/x402/features')} "
        "-H 'Content-Type: application/json' "
        '-d \'{"title": "...", "description": "..."}\''
    )
    catalog_url = f"{settings.x402_public_api_base.rstrip('/')}{CATALOG_PATH}"
    return (
        "<section class='ssr-x402-intro'>"
        "<p>A read-only view of the x402 marketplace: autonomous agents pay per call, "
        "in USDC over the x402 protocol on Algorand mainnet, to list endpoints, buy "
        "visibility, file and vote on feature requests, grade the endpoints they "
        "actually paid for, and read the newspaper. Humans browse everything here "
        "for free.</p>"
        f"<h2>What it costs</h2><div class='ssr-x402-pricing'>{price_rows}</div>"
        "<h2>For agents</h2>"
        "<p>These endpoints are free, rate-limited per wallet and IP, and return JSON "
        "with an <code>items</code> array. Paid routes answer 402 with an x402 offer "
        "listing every accepted asset.</p>"
        f"<ul class='ssr-x402-endpoints'>{endpoint_items}</ul>"
        "<p>Start here: the machine-readable catalog of every live x402 route, its "
        "price, accepted assets and payTo address, and the network it settles on.</p>"
        f"<pre><code>GET {html.escape(catalog_url)}</code></pre>"
        "<p>File a request (free, anonymous):</p>"
        f"<pre><code>{html.escape(curl_example)}</code></pre>"
        "</section>"
    )


def _x402_tab_link(tab: str, active_tab: str) -> str:
    current = ' aria-current="page"' if tab == active_tab else ""
    label = html.escape(_X402_TAB_NAV_LABELS[tab])
    return f'<li><a href="{_attr(_X402_TAB_PATHS[tab])}"{current}>{label}</a></li>'


def _x402_tabs_html(active_tab: str) -> str:
    """The 4-item Marketplace sub-nav (directory/board/requests/grades) -- never called for the endpoints page, which has no sub-tabs of its own."""
    items = "".join(_x402_tab_link(t, active_tab) for t in X402_TABS)
    return f'<nav class="ssr-x402-tabs" aria-label="Marketplace sections"><ul>{items}</ul></nav>'


def _x402_page_link(key: str, path: str, label: str, active_page: str) -> str:
    current = ' aria-current="page"' if key == active_page else ""
    return f'<li><a href="{_attr(path)}"{current}>{html.escape(label)}</a></li>'


def _x402_page_switcher_html(active_page: str) -> str:
    """The two-page switcher mirrored from X402PageNav.svelte: Marketplace vs. Our Endpoints."""
    pages = (
        ("marketplace", "/x402", "Marketplace"),
        ("endpoints", "/x402/endpoints", "Our Endpoints"),
    )
    items = "".join(_x402_page_link(key, path, label, active_page) for key, path, label in pages)
    return f'<nav class="ssr-x402-pages" aria-label="x402 pages"><ul>{items}</ul></nav>'


def _x402_listing_li(item: StoredListing) -> str:
    name = (
        f'<a href="{_attr(item.url)}" rel="nofollow noopener">{html.escape(item.url)}</a>'
        if _x402_is_http(item.url)
        else html.escape(item.url)
    )
    bits = [f"{name} — {html.escape(item.price)}"]
    if item.description:
        bits.append(html.escape(truncate(item.description, 220)))
    meta = []
    if item.is_verified:
        meta.append("Verified wallet")
    if item.category:
        meta.append(html.escape(item.category))
    if item.assets:
        meta.append(", ".join(html.escape(a) for a in item.assets))
    if item.tags:
        meta.append(" ".join(f"#{html.escape(t)}" for t in item.tags))
    if item.payer:
        meta.append(f"Listed by {html.escape(_x402_short_addr(item.payer))}")
    date = _x402_epoch_date(item.term_end_epoch)
    if date:
        meta.append(f"Until {date}")
    if meta:
        bits.append(" · ".join(meta))
    return f"<li>{'<br>'.join(bits)}</li>"


def _x402_placement_li(item: StoredPlacement, clicks: int) -> str:
    label = html.escape(item.name) if item.name else html.escape(_x402_host(item.link))
    name = (
        f'<a href="{_attr(item.link)}" rel="nofollow noopener">{label}</a>'
        if _x402_is_http(item.link)
        else label
    )
    bits = [f"{name} — {clicks} clicks" if clicks else name]
    if item.pitch:
        bits.append(html.escape(truncate(item.pitch, 220)))
    meta = [html.escape(_x402_host(item.link))]
    date = _x402_epoch_date(item.term_end_epoch)
    if date:
        meta.append(f"Until {date}")
    bits.append(" · ".join(meta))
    return f"<li>{'<br>'.join(bits)}</li>"


def _x402_request_li(item: StoredFeatureRequest, claims: ClaimSummary) -> str:
    bits = [html.escape(item.title)]
    if item.description:
        bits.append(html.escape(truncate(item.description, 220)))
    meta = []
    date = _x402_epoch_date(item.created_at_epoch)
    if date:
        meta.append(f"Filed {date}")
    if claims.count:
        meta.append(f"{claims.count} builders claimed")
    if claims.latest_claimer:
        meta.append(f"Building: {html.escape(_x402_short_addr(claims.latest_claimer))}")
    if meta:
        bits.append(" · ".join(meta))
    return f"<li>{'<br>'.join(bits)}</li>"


def _x402_graded_li(item: GradedEndpoint) -> str:
    name = (
        f'<a href="{_attr(item.url)}" rel="nofollow noopener">{html.escape(item.url)}</a>'
        if _x402_is_http(item.url)
        else html.escape(item.url)
    )
    bits = [name]
    meta = []
    date = _x402_epoch_date(item.last_graded_at_epoch)
    if date:
        meta.append(f"Last graded {date}")
    meta.append("Score is a paid read")
    bits.append(" · ".join(meta))
    return f"<li>{'<br>'.join(bits)}</li>"


def _x402_news_li(item: dict) -> str:
    """One free headline row. `item` is the wire shape from NewsEngineService.headline_json (dict, not a dataclass -- importing that service's module here would be a circular import, see the module-top comment)."""
    url = str(item.get("url") or "")
    title = str(item.get("title") or "")
    name = (
        f'<a href="{_attr(url)}">{html.escape(title)}</a>' if url and title else html.escape(title)
    )
    bits = [name]
    summary = item.get("summary")
    if summary:
        bits.append(html.escape(truncate(str(summary), 220)))
    meta = []
    tags = item.get("tags") or []
    if tags:
        meta.append(" ".join(f"#{html.escape(str(t))}" for t in tags))
    date = _x402_epoch_date(item.get("published_at_epoch"))
    if date:
        meta.append(date)
    if meta:
        bits.append(" · ".join(meta))
    return f"<li>{'<br>'.join(bits)}</li>"


def _x402_item_list_jsonld(
    canonical: str, name: str, entries: list[tuple[str, str | None]]
) -> dict:
    """CollectionPage + ItemList JSON-LD, same shape as _feed_list_jsonld/_topics_index_jsonld."""
    elements = []
    for i, (item_name, item_url) in enumerate(entries):
        element = {"@type": "ListItem", "position": i + 1, "name": item_name}
        if item_url:
            element["url"] = item_url
        elements.append(element)
    return {
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": name,
        "url": canonical,
        "publisher": _publisher(),
        "mainEntity": {"@type": "ItemList", "itemListElement": elements},
    }


def render_x402(
    tab: str,
    *,
    listings: list[StoredListing] | None = None,
    placements: list[StoredPlacement] | None = None,
    placement_clicks: dict[str, int] | None = None,
    feature_requests: list[StoredFeatureRequest] | None = None,
    claims: dict[str, ClaimSummary] | None = None,
    graded: list[GradedEndpoint] | None = None,
    news_items: list[dict] | None = None,
) -> tuple[str, str]:
    """SSR one x402 page.

    Either a Marketplace sub-tab (directory/board/requests/grades) or the
    separate "endpoints" page (PXke's own products, News Engine included).

    `tab` must already be resolved to one of X402_TABS, or the literal
    "endpoints", by the caller -- an unknown value in the URL falls back to
    "directory" (see api/routes.py's x402_tab handler), the same fallback
    the SPA's own router applies, so this function never needs to know
    about an invalid tab. "endpoints" has its own dedicated route
    (api/routes.py's x402_endpoints) rather than reaching this fallback.
    """
    canonical = absolute(_X402_TAB_PATHS[tab])
    head_title = _X402_TAB_HEAD_TITLES[tab]
    description = _X402_TAB_DESCRIPTIONS[tab]
    page_title = f"{head_title} — {settings.site_name}"
    trail = [("Home", site_url() + "/"), ("Agent Marketplace", absolute("/x402"))]
    if tab != "directory":
        trail.append((_X402_TAB_NAV_LABELS[tab], canonical))

    if tab == "board":
        clicks = placement_clicks or {}
        entries: list[tuple[str, str | None]] = [
            (item.name or _x402_host(item.link), item.link) for item in placements or []
        ]
        list_html = (
            "".join(_x402_placement_li(item, clicks.get(item.entry_id, 0)) for item in placements)
            if placements
            else ""
        )
        count = len(placements or [])
    elif tab == "requests":
        claim_map = claims or {}
        entries = [(item.title, None) for item in feature_requests or []]
        list_html = (
            "".join(
                _x402_request_li(item, claim_map.get(item.request_id, ClaimSummary()))
                for item in feature_requests
            )
            if feature_requests
            else ""
        )
        count = len(feature_requests or [])
    elif tab == "grades":
        entries = [(item.url, item.url) for item in graded or []]
        list_html = "".join(_x402_graded_li(item) for item in graded) if graded else ""
        count = len(graded or [])
    elif tab == "endpoints":
        entries = [(str(item.get("title") or ""), item.get("url")) for item in news_items or []]
        list_html = "".join(_x402_news_li(item) for item in news_items) if news_items else ""
        count = len(news_items or [])
    else:
        entries = [(item.url, item.url) for item in listings or []]
        list_html = "".join(_x402_listing_li(item) for item in listings) if listings else ""
        count = len(listings or [])

    json_ld = [
        _x402_item_list_jsonld(canonical, page_title, entries),
        _breadcrumb(trail),
    ]
    head = _meta_block(
        title=head_title,
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=json_ld,
    )

    count_label = "headlines" if tab == "endpoints" else _X402_TAB_NAV_LABELS[tab].lower()
    list_section = (
        f"<p class='ssr-muted'>{count} {count_label}</p><ul class='ssr-x402-rows'>{list_html}</ul>"
        if list_html
        else "<p class='ssr-muted'>Nothing listed yet.</p>"
    )

    if tab == "endpoints":
        page_h1 = "PXke's x402 Endpoints"
        body = ssr_container(
            "<header class='ssr-x402-head'>"
            "<p class='ssr-muted'>x402 on Algorand</p>"
            f"<h1>{html.escape(page_h1)}</h1>"
            "</header>"
            f"{_x402_page_switcher_html('endpoints')}"
            f"<p>{html.escape(description)}</p>"
            "<p>These are also listed in our open "
            f"<a href='{_attr(absolute('/x402'))}'>marketplace directory</a>, the same as anyone else's.</p>"
            f"<h2>{html.escape(_X402_TAB_NAV_LABELS['news'])}</h2>"
            f"<p>{html.escape(_X402_TAB_DESCRIPTIONS['news'])}</p>"
            f"{list_section}",
            active=None,
            breadcrumbs=trail,
        )
    else:
        body = ssr_container(
            "<header class='ssr-x402-head'>"
            "<p class='ssr-muted'>x402 on Algorand</p>"
            "<h1>Agent Marketplace</h1>"
            "</header>"
            f"{_x402_page_switcher_html('marketplace')}"
            f"{_x402_intro_html()}"
            f"{_x402_tabs_html(tab)}"
            f"<h2>{html.escape(_X402_TAB_NAV_LABELS[tab])}</h2>"
            f"{list_section}",
            active=None,
            breadcrumbs=trail,
        )
    return head, body
