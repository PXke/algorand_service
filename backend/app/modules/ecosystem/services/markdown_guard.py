"""Reject (never strip) embedded HTML in a submitted text field, plus a markdown-body size cap.

A plain-markdown or plain-text submission has no reason to contain
`<script>` or any raw tag, and silently stripping one would change what
the submitter said without telling them -- rejecting at write time is
honest about the same problem stripping tries to paper over. This is
defense in depth, not the frontend's obligation: the markdown renderer
still goes through the codebase's existing `{@html}` + DOMPurify allowlist
convention (CLAUDE.md section 5) -- this module exists so a malicious body
is refused up front, before it is ever stored.

Markdown autolinks (`<https://example.com>`, `<mailto:a@b.com>`) are
deliberately exempted from the HTML-tag check: they are legitimate
CommonMark syntax that happens to use angle brackets, and rejecting every
submission that links a bare URL this way would refuse ordinary markdown,
not HTML.
"""

from __future__ import annotations

import re

from app.modules.ecosystem.models.domain import EcosystemError

# Any '<' immediately followed by (optional whitespace, then) '!', '/', or a
# letter is treated as the start of an HTML tag/comment/doctype -- this is
# deliberately broader than a strict "well-formed tag" match (no requirement
# that a matching '>' ever follows): a partial or malformed tag is still an
# attempt to embed HTML, and "reject over strip" favors a few false
# positives on stray prose over ever silently admitting real markup.
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
    """Trim, size-cap, and HTML-reject a markdown body. Returns the trimmed body or raises EcosystemError."""
    body = raw.strip()
    if not body:
        raise EcosystemError("invalid_request", "body_md must not be empty", http_status=400)
    encoded_len = len(body.encode("utf-8"))
    if encoded_len > max_bytes:
        raise EcosystemError(
            "invalid_request",
            f"body_md must be at most {max_bytes} bytes (got {encoded_len})",
            http_status=400,
        )
    if _contains_embedded_html(body):
        raise EcosystemError(
            "embedded_html_rejected",
            "body_md appears to contain embedded HTML (a '<' immediately followed by a tag "
            "name, '/', or '!'). This is rejected, not stripped: plain markdown has no reason "
            "to include raw HTML, and silently stripping it would change what you said. "
            "Markdown autolinks like <https://example.com> are fine.",
            http_status=400,
        )
    return body


def reject_embedded_html(value: str, *, field_name: str) -> None:
    """Reject (never strip) embedded HTML in a plain, self-declared text field (name, description, message, ...).

    Same detector and the same "reject over strip" reasoning
    validate_markdown_body's own docstring gives, extended to fields that
    are otherwise only length-checked: a submission's name or description
    flows into every JSON response and the rendered registry page exactly
    like a markdown body does. The CommonMark autolink exemption is kept
    even for non-markdown fields -- it only widens what is ALLOWED (a bare
    `<https://example.com>` pasted into a description), never what is
    rejected.
    """
    if _contains_embedded_html(value):
        raise EcosystemError(
            "embedded_html_rejected",
            f"{field_name} appears to contain embedded HTML (a '<' immediately followed by a "
            "tag name, '/', or '!'). This is rejected, not stripped.",
            http_status=400,
        )


__all__ = ["reject_embedded_html", "validate_markdown_body"]
