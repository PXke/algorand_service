"""Strip unsafe HTML from writer-emitted article bodies.

Article bodies are Markdown source that the frontend renders with `marked`
(see frontend/src/components/Markdown.svelte) and injects via `{@html}`.
Marked passes inline raw HTML straight through by default, so an
LLM-composed or admin-edited body containing a literal `<script>`,
`<iframe>`, or an `onerror=` handler would execute in the reader's browser
unless it is stripped before storage. This allowlist mirrors the frontend's
DOMPurify config (W1-A) so a body that survives this server-side pass also
survives the client-side one, and vice versa.
"""

from __future__ import annotations

import nh3

# Tags a GFM-rendered article body legitimately produces or embeds inline.
# Anything else (script, style, iframe, form, object/embed, svg, on*
# handlers, ...) is stripped -- the disallowed wrapper tag is removed but
# its text content is kept (except script/style, whose content nh3 always
# drops too), so a rejected tag never silently deletes real prose.
ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "del",
    "ins",
    "mark",
    "sub",
    "sup",
    "blockquote",
    "q",
    "cite",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "a",
    "img",
    "code",
    "pre",
    "kbd",
    "samp",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
    "div",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# http(s) for normal links/images, mailto for contact links -- no
# javascript:, data:, tel:, sms:, bitcoin:, etc.
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def sanitize_markdown_body(text: str) -> str:
    """Strip disallowed HTML from an article body before storage/display."""
    cleaned = nh3.clean(
        text,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
    return cleaned.strip()
