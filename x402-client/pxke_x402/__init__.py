"""pxke-x402-client: a thin client for PXke's x402 marketplace.

    from pxke_x402 import PxkeClient

    # Free-only agent
    client = PxkeClient()
    print(client.catalog())

    # Paying agent (Algorand mainnet, real money -- see README)
    client = PxkeClient(mnemonic="word1 word2 ... word25")
    print(client.ping())
"""

from __future__ import annotations

from .client import BASE_URL, PxkeClient
from .exceptions import PxkeConfigError, PxkeError, PxkeHTTPError, PxkePaymentError

__all__ = [
    "BASE_URL",
    "PxkeClient",
    "PxkeError",
    "PxkeConfigError",
    "PxkeHTTPError",
    "PxkePaymentError",
]

__version__ = "0.5.0"
