"""Shared x402 resource server + facilitator client, built from settings.

Kept process-wide and initialized lazily (first use, not import time): building
it calls the facilitator's `/supported` endpoint over the network, and this
module must stay import-safe even when X402_ENABLED=false (main.py always
imports consumer modules, it only conditionally registers their routes).
"""

from __future__ import annotations

import threading

from x402.http.facilitator_client import HTTPFacilitatorClientSync
from x402.http.facilitator_client_base import FacilitatorConfig
from x402.mechanisms.avm.exact.register import register_exact_avm_server
from x402.server import x402ResourceServerSync

from app.core.config import settings

_lock = threading.Lock()
_server: x402ResourceServerSync | None = None


def get_resource_server() -> x402ResourceServerSync:
    """The shared, initialized x402ResourceServerSync (built once per process)."""
    global _server
    if _server is not None:
        return _server
    with _lock:
        if _server is None:
            facilitator = HTTPFacilitatorClientSync(
                FacilitatorConfig(url=settings.x402_facilitator_url)
            )
            server = x402ResourceServerSync(facilitator)
            register_exact_avm_server(server, networks=settings.x402_network)
            server.initialize()  # fetches facilitator /supported once
            _server = server
        return _server
