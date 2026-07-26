"""Server-side generated OG/share image for articles with no real photo.

Most stories have no usable hero (the pipeline stores a source favicon or
nothing at all — see looksLikeLogoUrl on the frontend), so every link shared
to Slack/Discord/X/iMessage previewed as the bare app icon. This draws the
same "lead package" signature the app uses on-page — accent slug, small-caps
kicker, serif headline, PXke wordmark — onto a proper 1200x630 share image,
so a shared link looks like the paper instead of a generic square logo.

Pure/deterministic: (title, kicker) -> PNG bytes, no I/O. The caller (routes)
owns fetching the article and caching the result.

KNOWN LIMITATION: the bundled fonts cover Latin (incl. extended/diacritics)
only — the newsroom composes in English, so this is accepted rather than
merging in CJK/Arabic glyph coverage. A title containing non-Latin script
degrades gracefully (those characters measure and draw as zero-width, so
they silently drop rather than corrupting layout or crashing) but the
headline will visibly lose those words.
"""

from __future__ import annotations

import contextlib
from functools import cache
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ASSETS = Path(__file__).parent / "assets" / "fonts"

CARD_WIDTH = 1200
CARD_HEIGHT = 630

# Same palette as the SPA's light theme tokens (frontend/src/app.css :root) —
# the share card is always the light "paper" look regardless of the viewer's
# app theme, matching how a printed front page has one identity.
_PAPER = (242, 244, 242)
_INK = (18, 24, 22)
_MUTED = (90, 102, 98)
_ACCENT = (14, 122, 114)
_ACCENT_FACET = (26, 155, 144)

_PAD_X = 76
_PAD_TOP = 72
_PAD_BOTTOM = 64
_MARK_SIZE = 52


@cache
def _font(name: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_ASSETS / name), size)
    with_variation = getattr(font, "set_variation_by_axes", None)
    if with_variation is not None:
        # non-variable fallback font: use its single built-in weight
        with contextlib.suppress(Exception):
            with_variation([weight])
    return font


def _serif(size: int, *, bold: bool = True) -> ImageFont.FreeTypeFont:
    return _font("SourceSerif4.ttf", size, 700 if bold else 400)


def _sans(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return _font("Inter.ttf", size, 700 if bold else 400)


def _wrap_to_width(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float
) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _fit_headline(
    draw: ImageDraw.ImageDraw, title: str, max_width: float, max_height: float
) -> tuple[list[str], ImageFont.FreeTypeFont]:
    """Largest serif size (from a fixed ramp) whose wrapped headline fits the box, so a short title reads big and a long one still fits without overflowing into the footer."""
    for size in (64, 56, 48, 42, 37, 33):
        font = _serif(size)
        lines = _wrap_to_width(draw, title, font, max_width)
        line_height = font.size * 1.2
        block_height = line_height * len(lines)
        if block_height <= max_height and len(lines) <= 5:
            return lines, font
    # Smallest size still overflowing: truncate to what fits + ellipsis.
    font = _serif(33)
    lines = _wrap_to_width(draw, title, font, max_width)
    line_height = font.size * 1.2
    max_lines = max(1, int(max_height // line_height))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while draw.textlength(last + "…", font=font) > max_width and len(last) > 1:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines, font


def _brand_mark(img: Image.Image, draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    """Flat indigo tile + diagonal facet + white 'P' — mirrors BrandMark.svelte (frontend/src/components/BrandMark.svelte) so the card and the app show the same monogram. Base rect + facet triangle are drawn full-size on their own layer, then rounded together with one alpha mask (only the OUTER tile boundary is rounded, not the triangle itself — same as the SVG's clip)."""
    radius = size * 0.23
    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tile)
    tdraw.rectangle([0, 0, size, size], fill=(*_ACCENT, 255))
    tdraw.polygon(
        [(size * 0.46, 0), (size, 0), (size, size * 0.54)],
        fill=(*_ACCENT_FACET, 255),
    )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size, size], radius=radius, fill=255)
    tile.putalpha(mask)
    img.paste(tile, (x, y), tile)

    letter_font = _sans(int(size * 0.56), bold=True)
    bbox = draw.textbbox((0, 0), "P", font=letter_font)
    lw, lh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(
        (x + (size - lw) / 2 - bbox[0], y + (size - lh) / 2 - bbox[1]),
        "P",
        font=letter_font,
        fill=(255, 255, 255),
    )


def render_share_card(*, title: str, kicker: str = "") -> bytes:
    """Draw the card and return PNG bytes."""
    title = (title or "").strip() or "PXke Algorand"
    img = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), _PAPER)
    draw = ImageDraw.Draw(img)

    # Top nameplate rule (mirrors the app-bar bottom border's accent moments).
    draw.rectangle([0, 0, CARD_WIDTH, 6], fill=_ACCENT)

    # Brand mark + wordmark, top-left.
    mark_y = _PAD_TOP
    _brand_mark(img, draw, _PAD_X, mark_y, _MARK_SIZE)
    wordmark_font = _sans(24, bold=True)
    wbbox = draw.textbbox((0, 0), "PXKE ALGORAND", font=wordmark_font)
    wh = wbbox[3] - wbbox[1]
    draw.text(
        (_PAD_X + _MARK_SIZE + 18, mark_y + (_MARK_SIZE - wh) / 2 - wbbox[1]),
        "PXKE ALGORAND",
        font=wordmark_font,
        fill=_INK,
    )

    content_top = mark_y + _MARK_SIZE + 56
    max_width = CARD_WIDTH - 2 * _PAD_X

    # Accent slug + kicker (same "department mark" as the on-page lead/kicker).
    cursor_y = content_top
    if kicker:
        draw.rectangle([_PAD_X, cursor_y, _PAD_X + 40, cursor_y + 4], fill=_ACCENT)
        cursor_y += 22
        kicker_font = _sans(26, bold=True)
        draw.text((_PAD_X, cursor_y), kicker.upper(), font=kicker_font, fill=_ACCENT)
        kbbox = draw.textbbox((0, 0), kicker.upper(), font=kicker_font)
        cursor_y += (kbbox[3] - kbbox[1]) + 30
    else:
        cursor_y += 12

    footer_font = _sans(22)
    footer_h = draw.textbbox((0, 0), "algorand.pxke.me", font=footer_font)[3]
    headline_bottom_limit = CARD_HEIGHT - _PAD_BOTTOM - footer_h - 28
    lines, headline_font = _fit_headline(draw, title, max_width, headline_bottom_limit - cursor_y)
    line_height = headline_font.size * 1.22
    for line in lines:
        draw.text((_PAD_X, cursor_y), line, font=headline_font, fill=_INK)
        cursor_y += line_height

    # Domain signature, bottom-left — the quiet "this came from us" mark.
    draw.text(
        (_PAD_X, CARD_HEIGHT - _PAD_BOTTOM - footer_h),
        "algorand.pxke.me",
        font=footer_font,
        fill=_MUTED,
    )

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
