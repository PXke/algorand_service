"""Probe a service's primary domain for liveness before composing."""

from __future__ import annotations

import logging
import ssl
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_UA = "algorand-platform-enrichment/1.0"


def probe_domain(domain: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """Safe surface check: HTTPS reachability + response headers (no page JS execution).

    WHOIS / company registry → phase 3 (external API).
    """
    host = domain.strip().lower().removeprefix("www.")
    if not host or " " in host:
        return {"domain": domain, "error": "invalid_host"}

    url = f"https://{host}/"
    result: dict[str, Any] = {"domain": host, "url": url}
    try:
        from app.core.net_guard import guarded_get, guarded_request

        # Hop-checked: follow_redirects=True on the shared client would let a
        # public host 302 into RFC1918 / cloud metadata after the first-URL
        # assert_public_url had already passed.
        req_headers = {"User-Agent": _UA}
        response = guarded_request("HEAD", url, headers=req_headers, timeout=timeout)
        if response.status_code >= 400:
            response = guarded_get(url, headers=req_headers, timeout=timeout)
        result["status_code"] = response.status_code
        result["final_url"] = str(response.url)
        headers = {
            k.lower(): v
            for k, v in response.headers.items()
            if k.lower()
            in (
                "server",
                "strict-transport-security",
                "content-security-policy",
                "x-frame-options",
                "cf-ray",
            )
        }
        result["headers"] = headers
        result["https"] = str(response.url).startswith("https://")
        result["hsts"] = "strict-transport-security" in headers
    except ssl.SSLError as exc:
        result["https"] = False
        result["error"] = f"tls_error: {exc}"
    except Exception as exc:
        result["https"] = False
        result["error"] = str(exc)[:200]

    result["safety_hint"] = _safety_hint(result)
    return result


def primary_domain_from_source(source_url: str, page_text: str) -> str:
    """Resolve the primary domain from the source URL, falling back to the page text."""
    if source_url.startswith("http"):
        try:
            return urlparse(source_url).netloc.lower().removeprefix("www.")
        except Exception:
            logger.debug("failed to parse netloc from %s", source_url, exc_info=True)
    from app.modules.newspaper.scam_enrichment import extract_domains_and_urls

    _urls, domains = extract_domains_and_urls(page_text)
    return domains[0] if domains else ""


def _safety_hint(probe: dict[str, Any]) -> str:
    if probe.get("error"):
        return "unreachable_or_tls_issue"
    if not probe.get("https"):
        return "no_https"
    if probe.get("status_code", 0) >= 400:
        return "http_error"
    if probe.get("hsts"):
        return "https_with_hsts"
    return "https_basic"
