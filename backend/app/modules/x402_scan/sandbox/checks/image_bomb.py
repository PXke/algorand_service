"""Pixel/decompression-bomb detection for image inputs -- see package docstring for the contract.

Reads ONLY the image's header/metadata (magic bytes + declared dimension
fields) to estimate the size of the pixel buffer a naive decoder would
allocate, then compares that estimate against the file's own on-disk size
and an absolute cap. NEVER decodes actual pixel/raster data -- no
`PIL.Image.load()`, no `.convert()`, no pixel access, no full raster decode
of any format -- see scan.py's module docstring for why. This module
therefore hand-parses the handful of relevant header fields per format (PNG
IHDR, JPEG SOFn, GIF logical screen descriptor, BMP DIB header, WebP
VP8/VP8L/VP8X) using stdlib `struct` only -- no Pillow or other image
library dependency, on purpose:

  1. It keeps this sandbox's dependency count at zero new packages (see the
     Dockerfile's own comment on minimal-dependency attack-surface).
  2. Pillow's `Image.open()` IS documented and, per its source, actually
     implemented to only lazily parse header data and defer the real raster
     decode until something forces it (`.load()`, `.save()`, pixel access,
     `getdata()`, etc.) -- every built-in plugin's `_open()` reads only
     header/metadata and populates `self.tile` (decode instructions) without
     executing any of them. That said, staking a "never decode" invariant on
     "a third-party library's laziness contract holds for every code path,
     forever, including future Pillow versions and plugins" is a worse bet
     than parsing nine well-documented, stable, decades-old header layouts
     ourselves with zero decode surface at all. Hand-parsing is verifiably
     decode-free by construction; trusting `Image.open()` alone is not --
     so this module hand-parses instead of adding the dependency.

Deliberate scope limits, not bugs:
  - Multi-frame formats (animated GIF, animated WebP via VP8X+ANIM) are read
    for their single logical-screen/canvas size only. A bomb built from many
    normally-sized frames (frame-count amplification, not single-frame
    pixel-dimension amplification) is a different attack shape this check
    does not cover.
  - Indexed/palette formats (PNG color type 3, <=8bpp BMP) are estimated as
    if a naive consumer expands them to RGB for display, since that's the
    common real-world decode path that actually allocates the large buffer,
    not the on-disk 1-byte-per-pixel index buffer.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import BinaryIO

# Absolute cap: a declared decoded raster buffer bigger than this is already
# an unreasonable allocation for a naive downstream consumer to attempt,
# independent of how small the file itself is. 256 MiB comfortably covers
# large legitimate images (an 8000x8000 RGBA photo is ~256 MiB itself) while
# still catching what a bomb is actually built to weaponize -- GB-to-TB
# scale decoded buffers from a KB-scale file.
ABSOLUTE_DECODED_BYTES_CAP = 256 * 1024 * 1024

# Ratio cap: estimated decoded bytes / on-disk file bytes. A real photograph
# rarely compresses beyond ~50-100x even under heavy JPEG/PNG compression; a
# classic pixel bomb (e.g. a few-KB PNG whose IHDR declares a
# multi-billion-pixel canvas) shows ratios in the thousands-to-millions.
# This is the softer of the two signals -- a legitimate, extremely
# flat/solid-color image can also compress unusually well -- so it earns a
# lower risk_score on its own than the absolute cap.
DECODED_TO_FILE_RATIO_CAP = 1000

# Bound how far into the file JPEG marker scanning looks for an SOF segment
# before giving up. Legitimate embedded thumbnails/ICC profiles/EXIF blobs
# are bounded in practice; this stops a crafted file from making the
# scanner itself walk unbounded marker segments.
JPEG_SCAN_LIMIT_BYTES = 8 * 1024 * 1024

# SOF0-SOF15 minus DHT(C4)/JPG(C8)/DAC(CC), which share the numeric range
# but aren't frame headers.
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}
# Markers with no following length-prefixed segment: TEM, SOI, EOI, RST0-RST7.
_JPEG_NO_LENGTH_MARKERS = {0x01, 0xD8, 0xD9} | set(range(0xD0, 0xD8))


def _bytes_per_channel(bit_depth: int) -> int:
    """Decoded storage bytes per channel for a given bit depth (minimum 1 byte/channel)."""
    return max(1, (bit_depth + 7) // 8)


def _read_png(f: BinaryIO) -> tuple[int, int, int, str] | None:
    """(width, height, bytes_per_pixel, "png") from a PNG's IHDR chunk, else None."""
    f.seek(0)
    header = f.read(8 + 8 + 13)
    if len(header) < 29 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width, height, bit_depth, color_type = struct.unpack(">IIBB", header[16:26])
    # 0=gray, 2=RGB, 3=indexed (estimated as expanded to RGB), 4=gray+alpha, 6=RGBA
    channels_by_color_type = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}
    channels = channels_by_color_type.get(color_type)
    if channels is None:
        return None
    return width, height, channels * _bytes_per_channel(bit_depth), "png"


def _read_gif(f: BinaryIO) -> tuple[int, int, int, str] | None:
    """(width, height, bytes_per_pixel, "gif") from a GIF's logical screen descriptor."""
    f.seek(0)
    header = f.read(13)
    if len(header) < 13 or header[:6] not in (b"GIF87a", b"GIF89a"):
        return None
    width, height = struct.unpack("<HH", header[6:10])
    # On-disk GIF is palette-indexed, but a naive decoder commonly expands
    # each frame to RGBA for compositing/animation -- estimate that path.
    return width, height, 4, "gif"


def _bmp_bytes_per_pixel(bit_count: int) -> int:
    if bit_count <= 8:
        return 3  # indexed, naive expansion to RGB
    if bit_count == 16:
        return 2
    if bit_count == 24:
        return 3
    return 4  # 32bpp (and anything unrecognized) -- conservative RGBA estimate


def _read_bmp(f: BinaryIO) -> tuple[int, int, int, str] | None:
    """(width, height, bytes_per_pixel, "bmp") from a BMP file/DIB header."""
    f.seek(0)
    header = f.read(34)
    if len(header) < 26 or header[:2] != b"BM":
        return None
    dib_size = struct.unpack("<I", header[14:18])[0]
    if dib_size == 12:
        # OS/2 BITMAPCOREHEADER: 16-bit width/height, bit_count at offset 24.
        width, height = struct.unpack("<HH", header[18:22])
        bit_count = struct.unpack("<H", header[24:26])[0]
    elif len(header) >= 30:
        # BITMAPINFOHEADER (or newer, same leading layout): 32-bit signed
        # width/height at 18/22 (negative height = top-down row order, the
        # magnitude is still the pixel count), bit_count at 28.
        width, height_signed = struct.unpack("<ii", header[18:26])
        height = abs(height_signed)
        bit_count = struct.unpack("<H", header[28:30])[0]
    else:
        return None
    return width, height, _bmp_bytes_per_pixel(bit_count), "bmp"


def _read_webp(f: BinaryIO) -> tuple[int, int, int, str] | None:
    """(width, height, bytes_per_pixel, "webp") from a WebP RIFF container's first chunk."""
    f.seek(0)
    riff_header = f.read(12)
    if len(riff_header) < 12 or riff_header[:4] != b"RIFF" or riff_header[8:12] != b"WEBP":
        return None
    fourcc = f.read(4)
    chunk_size = f.read(4)  # unused -- only need the payload that follows
    if len(fourcc) < 4 or len(chunk_size) < 4:
        return None
    if fourcc == b"VP8 ":
        # 3-byte frame tag + 3-byte keyframe start code + width(2 LE) + height(2 LE),
        # each a 14-bit dimension with a 2-bit scale factor in the high bits.
        payload = f.read(10)
        if len(payload) < 10 or payload[3:6] != b"\x9d\x01\x2a":
            return None
        w_raw, h_raw = struct.unpack("<HH", payload[6:10])
        width, height = w_raw & 0x3FFF, h_raw & 0x3FFF
    elif fourcc == b"VP8L":
        # 1-byte signature (0x2F) + 4-byte packed header: 14-bit width-1, 14-bit height-1.
        payload = f.read(5)
        if len(payload) < 5 or payload[0] != 0x2F:
            return None
        bits = int.from_bytes(payload[1:5], "little")
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif fourcc == b"VP8X":
        # 1-byte flags + 3-byte reserved + 3-byte canvas width-1 + 3-byte canvas height-1.
        payload = f.read(10)
        if len(payload) < 10:
            return None
        width = int.from_bytes(payload[4:7], "little") + 1
        height = int.from_bytes(payload[7:10], "little") + 1
    else:
        return None
    # WebP commonly decodes to RGBA regardless of lossy/lossless/alpha presence.
    return width, height, 4, "webp"


def _next_jpeg_marker(f: BinaryIO) -> int | None:
    """Advance past any 0xFF padding fill bytes and return the next marker byte, or None at EOF."""
    b = f.read(1)
    if not b or b[0] != 0xFF:
        return None
    marker = f.read(1)
    while marker and marker[0] == 0xFF:
        marker = f.read(1)
    return marker[0] if marker else None


def _read_jpeg(f: BinaryIO) -> tuple[int, int, int, str] | None:
    """(width, height, bytes_per_pixel, "jpeg") from the first SOFn marker segment found."""
    f.seek(0)
    if f.read(2) != b"\xff\xd8":
        return None
    while f.tell() < JPEG_SCAN_LIMIT_BYTES:
        marker = _next_jpeg_marker(f)
        if marker is None:
            return None
        if marker in _JPEG_NO_LENGTH_MARKERS:
            continue
        if marker == 0xDA:  # start of scan -- entropy-coded data follows, no SOF found
            return None
        length_bytes = f.read(2)
        if len(length_bytes) < 2:
            return None
        seg_len = struct.unpack(">H", length_bytes)[0]
        if seg_len < 2:  # malformed -- would never advance, bail out
            return None
        if marker in _JPEG_SOF_MARKERS:
            sof_data = f.read(6)  # precision(1) + height(2) + width(2) + num_components(1)
            if len(sof_data) < 6:
                return None
            precision, height, width, components = struct.unpack(">BHHB", sof_data)
            bytes_per_pixel = max(1, components) * _bytes_per_channel(precision)
            return width, height, bytes_per_pixel, "jpeg"
        f.seek(seg_len - 2, 1)  # length includes its own 2 bytes -- skip the rest of this segment
    return None


# Order matters only for scan cost: JPEG's marker walk is the most expensive
# parse (bounded by JPEG_SCAN_LIMIT_BYTES), so it runs last.
_PARSERS = (_read_png, _read_gif, _read_bmp, _read_webp, _read_jpeg)


def run(path: str) -> dict:
    """Header-only pixel-bomb estimate for an image target; {} for a non-image file."""
    try:
        file_size = Path(path).stat().st_size
        parsed = None
        with Path(path).open("rb") as f:
            for parser in _PARSERS:
                parsed = parser(f)
                if parsed is not None:
                    break
        if parsed is None:
            return {}
        width, height, bytes_per_pixel, fmt = parsed
        if width <= 0 or height <= 0:
            return {}  # not a usable image, not a bomb signal either

        decoded_bytes = width * height * bytes_per_pixel
        ratio = decoded_bytes / file_size if file_size > 0 else float("inf")
        result: dict = {
            "format": fmt,
            "declared_width": width,
            "declared_height": height,
            "estimated_decoded_bytes": decoded_bytes,
            "file_size_bytes": file_size,
            "decoded_to_file_ratio": None if ratio == float("inf") else round(ratio, 1),
        }

        over_absolute = decoded_bytes > ABSOLUTE_DECODED_BYTES_CAP
        over_ratio = ratio > DECODED_TO_FILE_RATIO_CAP
        if over_absolute or over_ratio:
            caution_notes = []
            if over_absolute:
                caution_notes.append(
                    f"declared {width}x{height} {fmt} image would decode to an estimated "
                    f"{decoded_bytes:,} bytes (~{decoded_bytes // (1024 * 1024):,} MiB), over the "
                    f"{ABSOLUTE_DECODED_BYTES_CAP // (1024 * 1024)} MiB sandbox cap -- possible "
                    "pixel/decompression bomb"
                )
            if over_ratio:
                caution_notes.append(
                    f"estimated decoded size is ~{ratio:.0f}x the on-disk file size "
                    f"({file_size:,} bytes) -- disproportionate expansion, a classic "
                    "decompression-bomb signature"
                )
            result["risk_score"] = (
                0.7 if (over_absolute and over_ratio) else (0.5 if over_absolute else 0.35)
            )
            result["caution_notes"] = caution_notes
        return result
    except (
        Exception
    ) as exc:  # a bug here must not sink the whole scan (checks/__init__.py contract)
        return {"error": str(exc)}
