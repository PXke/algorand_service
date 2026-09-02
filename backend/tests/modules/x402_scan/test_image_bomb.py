"""Unit tests for image_bomb.py's header-only pixel-bomb detection.

Constructs minimal valid headers for each supported format by hand (no
Pillow dependency here either -- matches image_bomb.py's own zero-dependency
approach) and asserts declared dimensions are read correctly, the bomb
thresholds trip/don't trip as expected, and a non-image file is a no-op.
"""

from __future__ import annotations

import struct
from pathlib import Path

from app.modules.x402_scan.sandbox.checks import image_bomb


def _png_bytes(width: int, height: int, *, bit_depth: int = 8, color_type: int = 6) -> bytes:
    """A minimal well-formed PNG: signature + IHDR chunk only (no IDAT/IEND needed for header parsing)."""
    ihdr_data = struct.pack(">IIBBBBB", width, height, bit_depth, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data


def _gif_bytes(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 3


def _bmp_bytes(width: int, height: int, *, bit_count: int = 24) -> bytes:
    dib = struct.pack("<IiiHH", 40, width, height, 1, bit_count) + b"\x00" * (40 - 16)
    file_header = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, 14 + len(dib))
    return file_header + dib


def _jpeg_bytes(width: int, height: int, *, components: int = 3) -> bytes:
    """SOI + a single SOF0 marker segment, no scan data needed for header parsing."""
    sof_data = struct.pack(">BHHB", 8, height, width, components)
    sof_data += bytes(components * 3)  # component parameters, unread but keeps segment well-formed
    seg_len = len(sof_data) + 2
    return b"\xff\xd8" + b"\xff\xc0" + struct.pack(">H", seg_len) + sof_data


def _webp_vp8x_bytes(width: int, height: int) -> bytes:
    payload = struct.pack("<B", 0) + b"\x00" * 3
    payload += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    riff_size = 4 + len(chunk)
    return b"RIFF" + struct.pack("<I", riff_size) + b"WEBP" + chunk


def test_png_normal_image_has_no_risk_score(tmp_path: Path) -> None:
    """A normally-sized PNG carries no risk_score/caution_notes keys."""
    p = tmp_path / "small.png"
    # Header-only PNG bytes are tiny; pad with plausible compressed-pixel-data
    # filler so the decoded/file-size ratio reflects a real image, not a
    # bare header with nothing behind it (which would spuriously trip the
    # ratio cap regardless of how small the declared dimensions are).
    p.write_bytes(_png_bytes(100, 100) + b"\x00" * 2000)
    result = image_bomb.run(str(p))
    assert result["format"] == "png"
    assert result["declared_width"] == 100
    assert result["declared_height"] == 100
    assert "risk_score" not in result
    assert "caution_notes" not in result


def test_png_pixel_bomb_trips_absolute_cap(tmp_path: Path) -> None:
    """A PNG declaring a huge canvas trips the absolute decoded-bytes cap."""
    p = tmp_path / "bomb.png"
    p.write_bytes(_png_bytes(40000, 40000, color_type=6))  # 40000*40000*4 ~= 5.7 GiB decoded
    result = image_bomb.run(str(p))
    assert result["format"] == "png"
    assert result["risk_score"] >= 0.5
    assert any("sandbox cap" in note for note in result["caution_notes"])


def test_png_high_ratio_small_dimensions_trips_ratio_cap_only(tmp_path: Path) -> None:
    """A small-but-real PNG whose declared decode size is disproportionate to its tiny file trips the ratio cap, not the absolute cap."""
    p = tmp_path / "ratio_bomb.png"
    # 2000x2000 RGBA -> 16,000,000 decoded bytes, well under the 256MiB absolute
    # cap, but the on-disk header-only file here is ~50 bytes -> ratio >> 1000.
    p.write_bytes(_png_bytes(2000, 2000, color_type=6))
    result = image_bomb.run(str(p))
    assert result["estimated_decoded_bytes"] < image_bomb.ABSOLUTE_DECODED_BYTES_CAP
    assert result["decoded_to_file_ratio"] > image_bomb.DECODED_TO_FILE_RATIO_CAP
    assert result["risk_score"] == 0.35
    assert any("disproportionate expansion" in note for note in result["caution_notes"])


def test_gif_normal_image_has_no_risk_score(tmp_path: Path) -> None:
    """A normally-sized GIF carries no risk_score/caution_notes keys."""
    p = tmp_path / "small.gif"
    # See test_png_normal_image_has_no_risk_score for why filler is appended.
    p.write_bytes(_gif_bytes(50, 50) + b"\x00" * 1000)
    result = image_bomb.run(str(p))
    assert result["format"] == "gif"
    assert result["declared_width"] == 50
    assert result["declared_height"] == 50
    assert "risk_score" not in result


def test_bmp_normal_image_has_no_risk_score(tmp_path: Path) -> None:
    """A normally-sized BMP carries no risk_score/caution_notes keys."""
    p = tmp_path / "small.bmp"
    data = _bmp_bytes(64, 64, bit_count=24)
    p.write_bytes(data + b"\x00" * (64 * 64 * 3))  # pad with plausible pixel data
    result = image_bomb.run(str(p))
    assert result["format"] == "bmp"
    assert result["declared_width"] == 64
    assert result["declared_height"] == 64
    assert "risk_score" not in result


def test_bmp_pixel_bomb_trips_absolute_cap(tmp_path: Path) -> None:
    """A BMP declaring a huge canvas trips the absolute decoded-bytes cap."""
    p = tmp_path / "bomb.bmp"
    p.write_bytes(_bmp_bytes(30000, 30000, bit_count=32))  # 30000*30000*4 ~= 3.35 GiB decoded
    result = image_bomb.run(str(p))
    assert result["format"] == "bmp"
    assert result["risk_score"] >= 0.5


def test_jpeg_normal_image_has_no_risk_score(tmp_path: Path) -> None:
    """A normally-sized JPEG carries no risk_score/caution_notes keys."""
    p = tmp_path / "small.jpg"
    # See the PNG test above for why plausible-sized filler is appended.
    p.write_bytes(_jpeg_bytes(200, 150, components=3) + b"\x00" * 4000)
    result = image_bomb.run(str(p))
    assert result["format"] == "jpeg"
    assert result["declared_width"] == 200
    assert result["declared_height"] == 150
    assert "risk_score" not in result


def test_jpeg_pixel_bomb_trips_absolute_cap(tmp_path: Path) -> None:
    """A JPEG declaring a huge canvas trips the absolute decoded-bytes cap."""
    p = tmp_path / "bomb.jpg"
    p.write_bytes(_jpeg_bytes(20000, 20000, components=3))  # 20000*20000*3 ~= 1.1 GiB decoded
    result = image_bomb.run(str(p))
    assert result["format"] == "jpeg"
    assert result["risk_score"] >= 0.5


def test_webp_vp8x_normal_image_has_no_risk_score(tmp_path: Path) -> None:
    """A normally-sized WebP (VP8X) carries no risk_score/caution_notes keys."""
    p = tmp_path / "small.webp"
    # See test_png_normal_image_has_no_risk_score for why filler is appended.
    p.write_bytes(_webp_vp8x_bytes(80, 60) + b"\x00" * 1500)
    result = image_bomb.run(str(p))
    assert result["format"] == "webp"
    assert result["declared_width"] == 80
    assert result["declared_height"] == 60
    assert "risk_score" not in result


def test_webp_pixel_bomb_trips_absolute_cap(tmp_path: Path) -> None:
    """A WebP declaring a huge canvas trips the absolute decoded-bytes cap."""
    p = tmp_path / "bomb.webp"
    p.write_bytes(_webp_vp8x_bytes(16000, 16000))  # 16000*16000*4 ~= 954 MiB decoded
    result = image_bomb.run(str(p))
    assert result["format"] == "webp"
    assert result["risk_score"] >= 0.5


def test_non_image_file_returns_empty_dict(tmp_path: Path) -> None:
    """A plain non-image file short-circuits to {} (omitted from the report)."""
    p = tmp_path / "plain.txt"
    p.write_text("not an image, nothing to see here")
    assert image_bomb.run(str(p)) == {}


def test_zero_dimension_image_returns_empty_dict(tmp_path: Path) -> None:
    """A malformed/degenerate header (zero width) is treated as not-a-usable-image, not a bomb."""
    p = tmp_path / "zero.png"
    p.write_bytes(_png_bytes(0, 0))
    assert image_bomb.run(str(p)) == {}


def test_nonexistent_path_returns_error_dict() -> None:
    """A missing file surfaces {"error": ...}, never a false-clean {}."""
    result = image_bomb.run("/no/such/path/does-not-exist.png")
    assert "error" in result
