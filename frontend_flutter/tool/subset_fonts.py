#!/usr/bin/env python3
"""Build subset static font instances for the Flutter web bundle.

Reads full variable fonts from assets/fonts-src/, instances the weights the
app actually uses, subsets to l10n glyphs, and writes assets/fonts/.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "assets" / "fonts-src"
OUT_DIR = ROOT / "assets" / "fonts"
L10N_DIR = ROOT / "lib" / "l10n"

INTER_WEIGHTS = (400, 700)
SERIF_WEIGHTS = (700,)

EXTRA_RANGES = (
    "U+0020-007F",
    "U+00A0-00FF",
    "U+0100-024F",
    "U+2000-206F",
    "U+0600-06FF",
    "U+0750-077F",
    "U+08A0-08FF",
    "U+FB50-FDFF",
    "U+FE70-FEFF",
)

# (source stem, output stem, weights, italic)
JOBS = (
    ("Inter", "Inter", INTER_WEIGHTS, False),
    ("SourceSerif4", "SourceSerif4", SERIF_WEIGHTS, False),
)


def collect_chars() -> set[str]:
    chars: set[str] = set()
    for arb in L10N_DIR.glob("app_*.arb"):
        data = json.loads(arb.read_text(encoding="utf-8"))
        for key, value in data.items():
            if key.startswith("@") or not isinstance(value, str):
                continue
            chars.update(value)
    return chars


def unicodes_arg(chars: set[str]) -> str:
    codes = {f"U+{ord(c):04X}" for c in chars}
    codes.update(EXTRA_RANGES)
    return ",".join(sorted(codes))


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def instance(src: Path, weight: int, dst: Path) -> None:
    run(
        [
            sys.executable,
            "-m",
            "fontTools.varLib.instancer",
            str(src),
            f"wght={weight}",
            "--output",
            str(dst),
        ]
    )


def subset(path: Path, unicodes: str) -> None:
    tmp = path.with_suffix(".tmp.ttf")
    run(
        [
            sys.executable,
            "-m",
            "fontTools.subset",
            str(path),
            f"--unicodes={unicodes}",
            "--layout-features=*",
            f"--output-file={tmp}",
        ]
    )
    tmp.replace(path)


def build_job(src_stem: str, out_stem: str, weights: tuple[int, ...], italic: bool) -> None:
    src = SRC_DIR / f"{src_stem}.ttf"
    if not src.is_file():
        raise SystemExit(f"missing source font: {src}")

    unicodes = unicodes_arg(collect_chars())
    for weight in weights:
        out_name = f"{out_stem}-w{weight}.ttf"
        out = OUT_DIR / out_name
        instance(src, weight, out)
        before = out.stat().st_size
        subset(out, unicodes)
        after = out.stat().st_size
        label = f"{out_name} (italic)" if italic else out_name
        print(f"  {label}: {before // 1024} KiB -> {after // 1024} KiB")


def main() -> int:
    try:
        import fontTools  # noqa: F401
    except ImportError:
        print("error: pip install fonttools", file=sys.stderr)
        return 1

    if not SRC_DIR.is_dir():
        print(f"error: missing {SRC_DIR}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    for _src, out_stem, weights, _italic in JOBS:
        for weight in weights:
            expected.add(f"{out_stem}-w{weight}.ttf")
    for stale in OUT_DIR.glob("*.ttf"):
        if stale.name not in expected:
            stale.unlink()
            print(f"  removed stale {stale.name}")

    print(">>> Subsetting bundled fonts")
    for job in JOBS:
        build_job(*job)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
