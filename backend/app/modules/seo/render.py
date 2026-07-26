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
import json
import re
from datetime import UTC, datetime

import msgspec

from app.core.article_translation_langs import (
    ARTICLE_TRANSLATION_LANG_NAMES,
    SEO_HREFLANG_LOCALES,
    html_lang_for,
    og_locale_for,
)
from app.core.config import settings
from app.modules.news.models.schemas import ArticleDetail, ArticleFeedItem
from app.modules.seo.chrome import SSR_CHROME_STYLE, ssr_page
from app.modules.seo.markdown import md_to_html, md_to_text, truncate
from app.modules.seo.topics import display_tag_label, primary_tag, topic_feed_path


def site_url() -> str:
    """Return the public site base URL with no trailing slash."""
    return settings.public_site_url.rstrip("/")


def absolute(path: str) -> str:
    """Turn a possibly-relative path into an absolute site URL."""
    if path.startswith(("http://", "https://")):
        return path
    return f"{site_url()}/{path.lstrip('/')}"


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


def _clamped_title(title: str) -> str:
    """The <title> tag text: brand-suffixed when the result still fits the ~65-char SERP budget (before SERPs truncate and audit tools warn), otherwise the bare headline (brand already rides in og:site_name), word-boundary clamped if even that alone overflows. Only the <title> tag is clamped this way — og:/twitter:/JSON-LD keep the full headline."""
    suffixed = title if title.endswith(settings.site_name) else f"{title} — {settings.site_name}"
    if len(suffixed) <= _TITLE_MAX_CHARS:
        return suffixed
    if len(title) <= _TITLE_MAX_CHARS:
        return title
    cut = title[: _TITLE_MAX_CHARS - 1]
    space = cut.rfind(" ")
    if space > _TITLE_MAX_CHARS // 2:
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
) -> str:
    full_title = _clamped_title(title)
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


_DEFAULT_IMAGE_DIMS = (512, 512)  # icons/Icon-512.png
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


def _is_icon_like(image_url: str) -> bool:
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


def article_path(article_id: str) -> str:
    """Return the site-relative path for an article's canonical page."""
    return f"/news/articles/{article_id}"


def article_url(article_id: str, lang: str | None = None) -> str:
    """Absolute article URL; non-English locales use ?lang= (matches the API)."""
    base = absolute(article_path(article_id))
    code = (lang or "").strip()
    if code and code != "en":
        return f"{base}?lang={code}"
    return base


def article_hreflang_links(
    article_id: str, translation_langs: list[str] | None
) -> list[tuple[str, str]]:
    """(hreflang BCP-47 tag, absolute URL) pairs including x-default."""
    base = article_url(article_id)
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
        links.append((hreflang, article_url(article_id, code)))
        seen.add(code)
    return links


# Readable fallback styling for the pre-boot paint (and no-JS readers); the
# SPA replaces it once mounted. Kept tiny and inline so the SSR body
# needs no extra request.
_SSR_STYLE = (
    "<style>"
    # Paints the paper background under #ssr-body's own gutters too, so there
    # is no flash of default-white margin around the centered column.
    "html,body{background:#F8F7F4}"
    + SSR_CHROME_STYLE
    # The loading notice only exists for humans watching the app boot, so it is
    # hidden from the reading flow's start: JS reveals it, and it dies with the
    # div on first frame. No-JS readers and crawlers never see it. Styled as a
    # small kicker label (matching the share card's kicker treatment) rather
    # than an apologetic status line, since the content right below it is the
    # real page, not a placeholder.
    + "#ssr-loading{display:none;font:600 11px/1.4 system-ui,sans-serif;"
    "letter-spacing:.06em;text-transform:uppercase;color:#4F46E5;margin:0 0 18px}"
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


def pick_related_articles(
    article: ArticleDetail,
    feed: list[ArticleFeedItem],
    *,
    limit: int = 5,
) -> list[ArticleFeedItem]:
    """Stories sharing a tag with this article (mirrors the SPA detail page)."""
    tags = {t.strip().lower() for t in (article.tags or []) if t.strip()}
    if not tags:
        return []
    related: list[ArticleFeedItem] = []
    for item in feed:
        if item.article_id == article.article_id:
            continue
        item_tags = {t.strip().lower() for t in (item.tags or []) if t.strip()}
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
) -> str:
    langs = ["en", *(c for c in (translation_langs or []) if c != "en")]
    if len(langs) <= 1:
        return ""
    current = (current_lang or "en").strip() or "en"
    parts = []
    for code in langs:
        path = (
            article_path(article_id) if code == "en" else f"{article_path(article_id)}?lang={code}"
        )
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
        f'<li><a href="{_attr(article_path(item.article_id))}">{html.escape(item.title)}</a></li>'
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
    canonical = article_url(article.article_id, lang_code)
    image, is_default = _image_for(article.image_url)
    # A brand-icon fallback (favicon/logo) is tile art, not a share image or
    # banner — skip it for the body hero below same as a missing image.
    icon_like = bool(article.image_url) and _is_icon_like(image)
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

    hreflang_links = article_hreflang_links(article.article_id, translation_langs)
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
    tags_html = _tag_links_html(article.tags)
    langs_html = _translation_links_html(article.article_id, lang_code, translation_langs)
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
                    "url": absolute(article_path(item.article_id)),
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
        f'<li>{prefix}<a href="{_attr(article_path(item.article_id))}">'
        f"{html.escape(item.title)}</a> — {html.escape(truncate(item.summary, 140))}</li>"
    )


def _lead_index(items: list[ArticleFeedItem]) -> int:
    """Prefer the newest story with a real photograph as the front-page lead."""
    window = min(5, len(items))
    for i in range(window):
        url = items[i].image_url
        if url and not _is_icon_like(absolute(url)):
            return i
    return 0


def _lead_html(item: ArticleFeedItem) -> str:
    img = ""
    if item.image_url and not _is_icon_like(absolute(item.image_url)):
        img = f'<img src="{_attr(absolute(item.image_url))}" alt="{_attr(item.title)}">'
    path = _attr(article_path(item.article_id))
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
        if lead.image_url and not _is_icon_like(absolute(lead.image_url))
        else absolute(settings.seo_default_image),
        image_alt=lead.title,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[
            _website_jsonld(),
            {"@context": "https://schema.org", **_publisher(), "url": canonical},
            _feed_list_jsonld(items, canonical, f"{settings.site_name} — Front page"),
        ],
    )
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
    canonical = absolute("/news")
    breadcrumbs = [("Home", site_url() + "/"), ("Latest", canonical)]
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
        json_ld=[
            _feed_list_jsonld(items, canonical, heading),
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
) -> tuple[str, str]:
    """Most-read ledger: the feed ranked by read tally."""
    canonical = absolute("/hot")
    title = f"Most read — {settings.site_name}"
    trail = [("Home", site_url() + "/"), ("Most read", canonical)]
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
) -> tuple[str, str]:
    """Topic landing page: the feed filtered to one writer tag."""
    canonical = absolute(f"/topic/{tag}")
    title = f"{tag} — Algorand news"
    description = f"Algorand stories tagged “{tag}” from {settings.site_name}."
    trail = [("Home", site_url() + "/"), ("Topics", absolute("/topics")), (tag, canonical)]
    feed_path = topic_feed_path(tag)
    head = _meta_block(
        title=title,
        description=description,
        canonical=canonical,
        image=absolute(settings.seo_default_image),
        image_alt=settings.site_name,
        image_dims=_DEFAULT_IMAGE_DIMS,
        json_ld=[_feed_list_jsonld(items, canonical, title), _breadcrumb(trail)],
    )
    head += (
        f'\n<link rel="alternate" type="application/rss+xml" '
        f'title="{_attr(f"{tag} — {settings.site_name}")}" '
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
    head = _meta_block(
        title=title,
        description=settings.site_tagline,
        canonical=site_url() + "/",
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
