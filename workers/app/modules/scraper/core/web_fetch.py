"""Fetch a page over HTTP or Playwright and extract its main readable content."""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_DROP_TAGS = ("script", "style", "noscript", "svg", "canvas", "iframe", "form")
_DROP_HINTS = (
    "cookie",
    "consent",
    "newsletter",
    "subscribe",
    "signup",
    "sign-up",
    "login",
    "menu",
    "breadcrumb",
    "footer",
    "header",
    "sidebar",
    "promo",
    "advert",
    "banner",
    "social",
    "share",
    "related",
)


# A content block must hold at least this much text to be treated as the article
# body; below it we fall back to the whole cleaned document.
_MIN_MAIN_CHARS = 200


def _main_content_node(soup: BeautifulSoup) -> Tag | None:
    """Readability-style main-content selection (no extra dependency).

    Prefers a semantic <article>/<main>/[role=main]; otherwise scores the
    paragraph-bearing containers by prose weight minus link density (the arc90 /
    Mozilla-Readability core: real paragraphs lift their parent and grandparent)
    and returns the best one. Returns None when no block is clearly the article,
    so the caller falls back to the whole cleaned document — this can only ADD
    signal, never drop below the previous whole-page behaviour.
    """
    for selector in ("article", "main", "[role=main]"):
        node = soup.select_one(selector)
        if node is not None and len(node.get_text(" ", strip=True)) >= _MIN_MAIN_CHARS:
            return node

    scores: dict = {}
    for block in soup.find_all(("p", "pre", "blockquote", "li")):
        text = block.get_text(" ", strip=True)
        if len(text) < 25:
            continue
        # Prose signal: a base point, plus commas (clause density) and length,
        # capped so one huge block can't dominate.
        base = 1 + text.count(",") + min(len(text) // 100, 3)
        parent = block.parent
        if parent is None:
            continue
        scores[parent] = scores.get(parent, 0.0) + base
        grand = parent.parent
        if grand is not None:
            scores[grand] = scores.get(grand, 0.0) + base / 2.0

    best = None
    best_score = 0.0
    for node, raw in scores.items():
        text = node.get_text(" ", strip=True)
        if len(text) < _MIN_MAIN_CHARS:
            continue
        link_len = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        link_density = min(link_len / max(len(text), 1), 1.0)
        adjusted = raw * (1.0 - link_density)
        if adjusted > best_score:
            best, best_score = node, adjusted
    return best


def html_to_plain_text(html: str, *, keep_links: bool = False) -> str:
    """Cleaned main ARTICLE text. Boilerplate (nav/header/footer/cookie/etc.) is stripped, then a readability pass narrows to the main content block so the high-signal body isn't buried behind menus and banners (and isn't lost to a downstream character cap). With keep_links=True, in-content anchors are rendered inline as "label (https://url)" so an LLM reading the page can see where each link points. hrefs should be absolute by the time they reach here."""
    soup = BeautifulSoup(html, "html.parser")
    _strip_boilerplate(soup)
    target = _main_content_node(soup) or soup
    if keep_links:
        for a in target.find_all("a", href=True):
            href = a["href"].strip()
            label = a.get_text(" ", strip=True)
            if label and href.startswith(("http://", "https://")):
                a.replace_with(f"{label} ({href})")
    return "\n".join(line.strip() for line in target.get_text("\n").splitlines() if line.strip())


def extract_content_links(html: str, base_url: str, *, limit: int = 40) -> list[dict[str, str]]:
    """In-content outbound links as [{"text", "url"}], absolute + deduped. Strips boilerplate first so nav/footer/menu links are excluded — what's left is the article's own research trail (the linked blog post, GitHub PR, proposal, …)."""
    from urllib.parse import urljoin

    soup = BeautifulSoup(html, "html.parser")
    _strip_boilerplate(soup)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        absurl = urljoin(base_url, a["href"].strip())
        if not absurl.startswith(("http://", "https://")) or absurl in seen:
            continue
        label = a.get_text(" ", strip=True)
        if not label:
            continue
        seen.add(absurl)
        out.append({"text": label[:120], "url": absurl})
        if len(out) >= limit:
            break
    return out


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:
            # An ancestor was already decomposed in a prior iteration, taking
            # this tag with it; its attrs are gone, so skip it.
            continue
        name = (tag.name or "").lower()
        if name in ("nav", "header", "footer", "aside"):
            tag.decompose()
            continue
        attrs = " ".join(
            str(tag.get(key, "")) for key in ("id", "class", "role", "aria-label", "data-testid")
        ).lower()
        if attrs and any(hint in attrs for hint in _DROP_HINTS):
            tag.decompose()
