"""ecosystem.services.markdown_guard: embedded HTML is rejected, never stripped."""

from __future__ import annotations

import pytest

from app.modules.ecosystem.models.domain import EcosystemError
from app.modules.ecosystem.services.markdown_guard import (
    reject_embedded_html,
    validate_markdown_body,
)


def test_markdown_guard_rejects_embedded_html_rather_than_stripping_it() -> None:
    """A <script> tag is refused outright (EcosystemError), never silently removed."""
    with pytest.raises(EcosystemError) as exc_info:
        validate_markdown_body("hello <script>alert(1)</script> world", max_bytes=1000)
    assert exc_info.value.code == "embedded_html_rejected"
    assert exc_info.value.http_status == 400


def test_markdown_guard_rejects_a_raw_img_tag() -> None:
    """A raw <img src=...> is rejected the same way a <script> is."""
    with pytest.raises(EcosystemError):
        validate_markdown_body('<img src="x" onerror="evil()">', max_bytes=1000)


def test_markdown_guard_allows_a_commonmark_autolink() -> None:
    """<https://example.com> is legitimate markdown, not embedded HTML, and must NOT be rejected."""
    body = validate_markdown_body("see <https://example.com> for details", max_bytes=1000)
    assert body == "see <https://example.com> for details"


def test_markdown_guard_rejects_oversized_body() -> None:
    """A body over max_bytes is refused, never silently truncated."""
    with pytest.raises(EcosystemError) as exc_info:
        validate_markdown_body("x" * 100, max_bytes=10)
    assert exc_info.value.http_status == 400


def test_markdown_guard_rejects_empty_body() -> None:
    """An empty (or whitespace-only) body is refused."""
    with pytest.raises(EcosystemError):
        validate_markdown_body("   ", max_bytes=1000)


def test_reject_embedded_html_names_the_field_and_allows_autolinks() -> None:
    """The plain-field variant raises the same code, names the field, and keeps the autolink exemption."""
    with pytest.raises(EcosystemError) as exc_info:
        reject_embedded_html("<b>bold</b>", field_name="name")
    assert exc_info.value.code == "embedded_html_rejected"
    assert exc_info.value.http_status == 400
    assert "name" in exc_info.value.message
    reject_embedded_html("see <https://example.com> where 1 < 2", field_name="description")
