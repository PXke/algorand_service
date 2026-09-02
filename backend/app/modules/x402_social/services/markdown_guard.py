"""Reject (never strip) embedded HTML in a post/comment body, plus the size cap (design doc section 2.2).

The design doc's own reasoning: a plain-markdown post has no reason to
contain `<script>` or any raw tag, and silently stripping one would change
what the author paid to say without telling them -- rejecting at write time
is honest about the same problem stripping tries to paper over. This is
defense in depth, not the frontend's obligation: the eventual markdown
renderer still goes through the codebase's existing `{@html}` + DOMPurify
allowlist convention (CLAUDE.md section 5) -- this module exists so a
malicious body is refused up front, before it is ever paid for and stored.

Markdown autolinks (`<https://example.com>`, `<mailto:a@b.com>`) are
deliberately exempted from the HTML-tag check: they are legitimate
CommonMark syntax that happens to use angle brackets, and rejecting every
post that links a bare URL this way would refuse ordinary markdown, not
HTML.
"""

from __future__ import annotations

import re

from app.modules.x402_social.models.domain import SocialError

# Any '<' immediately followed by (optional whitespace, then) '!', '/', or a
# letter is treated as the start of an HTML tag/comment/doctype -- this is
# deliberately broader than a strict "well-formed tag" match (no requirement
# that a matching '>' ever follows): a partial or malformed tag is still an
# attempt to embed HTML, and the design doc's own call is "reject over
# strip," which favors a few false positives on stray prose over ever
# silently admitting real markup.
_HTML_OPEN_RE = re.compile(r"<\s*[!/a-zA-Z]")

# CommonMark autolinks: <scheme:...> or <mailto:...> with no whitespace or
# further '<'/'>' inside. Stripped out of the body BEFORE the HTML check so a
# legitimate `<https://example.com>` link never trips the tag detector above.
_AUTOLINK_RE = re.compile(r"<(?:[a-zA-Z][a-zA-Z0-9+.-]*:|mailto:)[^\s<>]+>")


def _contains_embedded_html(text: str) -> bool:
    """Shared detector: does `text` contain a '<' immediately followed by a tag name, '/', or '!', outside a CommonMark autolink?"""
    scan_target = _AUTOLINK_RE.sub("", text)
    return bool(_HTML_OPEN_RE.search(scan_target))


def validate_markdown_body(raw: str, *, max_bytes: int) -> str:
    """Trim, size-cap, and HTML-reject a markdown body. Returns the trimmed body or raises SocialError.

    Called BEFORE the payment gate on every post/comment write, so a
    malformed body is a free 400, never a charged one -- same "checkable
    without a payer, so check it first" precedent as
    x402_board.normalize_link and x402_directory's tag/category validation.
    """
    body = raw.strip()
    if not body:
        raise SocialError("invalid_request", "body_md must not be empty", http_status=400)
    encoded_len = len(body.encode("utf-8"))
    if encoded_len > max_bytes:
        raise SocialError(
            "invalid_request",
            f"body_md must be at most {max_bytes} bytes (got {encoded_len})",
            http_status=400,
        )
    if _contains_embedded_html(body):
        raise SocialError(
            "embedded_html_rejected",
            "body_md appears to contain embedded HTML (a '<' immediately followed by a tag "
            "name, '/', or '!'). This is rejected, not stripped: plain markdown has no reason "
            "to include raw HTML, and silently stripping it would change what you paid to say. "
            "Markdown autolinks like <https://example.com> are fine.",
            http_status=400,
        )
    return body


def reject_embedded_html(value: str, *, field_name: str) -> None:
    """Reject (never strip) embedded HTML in a plain, non-markdown, self-declared text field -- profile name/bio/mission/location/each interest, group name/description (finding 9, 2026-security-audit, defense in depth).

    Same detector and the same "reject over strip" reasoning
    validate_markdown_body's own docstring gives, extended to fields that
    were previously length-checked ONLY: a profile bio or group description
    flows into every JSON response and the prose template renderer exactly
    like a post body does, so `&lt;script&gt;...&lt;/script&gt;` (well under
    any of these fields' byte caps) being accepted and stored verbatim is
    the same class of gap, just on a field nobody thought of as
    "content." The CommonMark autolink exemption is kept even though these
    fields are not markdown -- it only widens what is ALLOWED (a bare
    `<https://example.com>` pasted into a bio), never what is rejected, so
    there is no reason to special-case it away here.

    Called BEFORE the payment gate on POST /register (validate_profile_fields
    runs pre-gate there) and unconditionally on PATCH /profile and group
    create -- same "checkable without a payer, check it first where
    possible" precedent as every other validator in this module and
    services/markdown_guard.py.
    """
    if _contains_embedded_html(value):
        raise SocialError(
            "embedded_html_rejected",
            f"{field_name} appears to contain embedded HTML (a '<' immediately followed by a "
            "tag name, '/', or '!'). This is rejected, not stripped, the same way a post body "
            "is.",
            http_status=400,
        )
