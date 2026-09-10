"""pxke-x402-client: a thin client for PXke's x402 marketplace.

Free-only agent::

    from pxke_x402 import PxkeClient

    client = PxkeClient()
    print(client.catalog())
    print(client.news(limit=5))

Paying agent (Algorand mainnet, real money -- see README)::

    client = PxkeClient(mnemonic="word1 word2 ... word25")
    print(client.scan_url("https://example.com/file.zip")["risk"])
"""

from __future__ import annotations

from .client import BASE_URL, PxkeClient
from .exceptions import (
    PxkeConfigError,
    PxkeError,
    PxkeHTTPError,
    PxkeOfferValidationError,
    PxkePaymentError,
)

__all__ = [
    "BASE_URL",
    "PxkeClient",
    "PxkeConfigError",
    "PxkeError",
    "PxkeHTTPError",
    "PxkeOfferValidationError",
    "PxkePaymentError",
]

__version__ = "0.7.0"
