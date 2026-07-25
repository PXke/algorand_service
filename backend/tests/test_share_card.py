"""Server-side share-card generator: pure (title, kicker) -> PNG bytes, no I/O — so these tests exercise real rendering, not mocks."""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from app.modules.seo.share_card import CARD_HEIGHT, CARD_WIDTH, render_share_card

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _dims(data: bytes) -> tuple[int, int]:
    img = Image.open(BytesIO(data))
    return img.size


def test_produces_valid_png_at_og_dimensions() -> None:
    """Renders a real PNG at the standard OG-image dimensions."""
    data = render_share_card(title="Nodely's free tier now carries 115M API calls a day")
    assert data.startswith(_PNG_MAGIC)
    assert _dims(data) == (CARD_WIDTH, CARD_HEIGHT)


def test_empty_title_falls_back_to_site_name() -> None:
    """Still renders a valid PNG at the standard dimensions for a blank title."""
    data = render_share_card(title="")
    assert data.startswith(_PNG_MAGIC)
    assert _dims(data) == (CARD_WIDTH, CARD_HEIGHT)


def test_missing_kicker_does_not_crash() -> None:
    """Renders a valid PNG when no kicker is supplied."""
    data = render_share_card(title="A story with no writer tag")
    assert data.startswith(_PNG_MAGIC)


def test_accented_latin_title_renders() -> None:
    """Renders a valid PNG for titles with Latin diacritics outside ASCII."""
    # Confío / Réti — real published titles with diacritics outside ASCII.
    data = render_share_card(title="Confío Delivers 5,500 Web2 Users to Algorand", kicker="latam")
    assert data.startswith(_PNG_MAGIC)


def test_very_long_title_truncates_without_overflow() -> None:
    """Truncates a very long title to stay within the fixed card dimensions."""
    huge = "Word " * 90
    data = render_share_card(title=huge, kicker="stress-test")
    assert data.startswith(_PNG_MAGIC)
    assert _dims(data) == (CARD_WIDTH, CARD_HEIGHT)


def test_non_latin_script_degrades_without_crashing() -> None:
    """Known limitation: bundled fonts are Latin-only (see module docstring).

    Non-Latin characters must not crash or corrupt the canvas size.
    """
    data = render_share_card(title="Algorand 支持 中文 项目发布 🚀 测试", kicker="ecosystem")
    assert data.startswith(_PNG_MAGIC)
    assert _dims(data) == (CARD_WIDTH, CARD_HEIGHT)


def test_rendering_is_deterministic() -> None:
    """Renders byte-identical output for the same title and kicker."""
    a = render_share_card(title="Same title every time", kicker="defi")
    b = render_share_card(title="Same title every time", kicker="defi")
    assert a == b
