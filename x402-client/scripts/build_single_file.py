#!/usr/bin/env python3
"""Concatenate exceptions.py + signer.py + client.py into one importable file.

The `pxke_x402.py` single-file build hosted at
https://algorand-api.pxke.me/sdk/pxke_x402.py (and .../sdk/latest.py) exists
for anyone who'd rather `curl` one file than `pip install`. It was hand-built
once; this script makes rebuilding it after a source change mechanical
instead of another manual concatenation.

Usage: python scripts/build_single_file.py > pxke_x402.py
(run from the x402-client/ directory)
"""

from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "pxke_x402"


def _strip_module_docstring_and_relative_imports(text: str, *, drop_stdlib_reimports: list[str]) -> str:
    """Remove the file's own module docstring, `from __future__ import annotations`, and any
    `from .foo import ...` relative import (everything ends up in one namespace) -- keep every
    other import, but only once across the whole bundle (dedup handled by the caller for the
    stdlib imports named in `drop_stdlib_reimports`).
    """
    # Drop the leading triple-quoted module docstring (first statement in the file).
    text = re.sub(r'^"""(?:[^"\\]|\\.)*?"""\n+', "", text, count=1, flags=re.DOTALL)
    text = text.replace("from __future__ import annotations\n", "")
    text = re.sub(r"^from \.\w+ import [^\n]+\n", "", text, count=0, flags=re.MULTILINE)
    for mod in drop_stdlib_reimports:
        text = re.sub(rf"^import {re.escape(mod)}\n", "", text, count=1, flags=re.MULTILINE)
    return text.strip("\n")


def build() -> str:
    version = re.search(
        r'version\s*=\s*"([^"]+)"', (PKG.parent / "pyproject.toml").read_text()
    ).group(1)

    header = f'''#!/usr/bin/env python3
"""pxke_x402 -- single-file client for PXke's x402 marketplace (https://algorand-api.pxke.me).

Consolidated from the pxke-x402-client package (pip install pxke-x402-client,
or from the tarball at https://algorand-api.pxke.me/sdk/pxke_x402-{version}.tar.gz)
for anyone who'd rather just download one file and import it directly.

Requires: requests, py-algorand-sdk, msgpack, x402-avm>=2.0.2 (pip install
requests py-algorand-sdk msgpack "x402-avm[avm]>=2.0.2").

Free methods need no wallet. Paid methods need mnemonic= at construction --
real Algorand mainnet, real USDC/EURQ/USDQ, real money. See PxkeClient's own
docstring below for the full method list.

Source of truth: https://algorand-api.pxke.me/api/v1/x402 (always fetch this
for current prices/routes rather than trusting a cached copy of this file).

Built by scripts/build_single_file.py -- do not hand-edit; edit the package
sources (pxke_x402/exceptions.py, signer.py, client.py) and rebuild.
"""

from __future__ import annotations

from typing import Any

'''

    exceptions_body = _strip_module_docstring_and_relative_imports(
        (PKG / "exceptions.py").read_text(), drop_stdlib_reimports=[]
    )
    # exceptions.py's own `from typing import Any` is already in the header above.
    exceptions_body = re.sub(r"^from typing import Any\n+", "", exceptions_body, count=1, flags=re.MULTILINE)

    signer_body = _strip_module_docstring_and_relative_imports(
        (PKG / "signer.py").read_text(), drop_stdlib_reimports=[]
    )

    client_body = _strip_module_docstring_and_relative_imports(
        (PKG / "client.py").read_text(), drop_stdlib_reimports=["algosdk", "base64", "msgpack"]
    )
    # client.py's own `from typing import Any` / `from urllib.parse import quote` /
    # `import requests` stay -- only the relative imports and the duplicated stdlib
    # imports already pulled in by signer.py above are stripped.
    client_body = re.sub(r"^from typing import Any\n+", "", client_body, count=1, flags=re.MULTILINE)

    return "\n\n\n".join([header.rstrip("\n"), exceptions_body, signer_body, client_body]) + "\n"


if __name__ == "__main__":
    import sys

    sys.stdout.write(build())
