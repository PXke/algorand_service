"""Phase-1 investigative-journalism tools for the Mistral agent.

Every tool is a stateless external lookup (no new database; results may be
persisted to Cassandra by the caller). All handlers are timeout-bounded and
failure-tolerant: on any error they return {"error": ...} so a tool failure
never aborts the article. Optional API keys are read from env when present.

Provenance -> Identity -> Assets -> History -> Network.
"""

from __future__ import annotations

import os
import socket
from typing import Any

_UA = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"
_TIMEOUT = 12.0


def _get(url: str, *, headers: dict | None = None, params: dict | None = None) -> Any:
    from app.core.net_guard import guarded_get

    h = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    r = guarded_get(url, headers=h, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    return r.json() if "json" in ct else r.text


def _resolve_ips(host: str, timeout: float = 5.0) -> list[str]:
    """Bounded DNS resolution. socket.getaddrinfo has no native timeout and can
    block for tens of seconds on a slow resolver — which would violate this
    module's timeout-bounded contract — so run it in a thread we can abandon."""
    import concurrent.futures

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(lambda: sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)}))
        return fut.result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)


# --- Provenance ------------------------------------------------------------
def fetch_archive_snapshot(url: str, target_date: str = "") -> dict[str, Any]:
    """Wayback Machine closest snapshot — proves a page existed/said something
    on a date, even if later edited or deleted. target_date: YYYYMMDD."""
    try:
        ts = "".join(ch for ch in target_date if ch.isdigit())[:8]
        data = _get(
            "http://archive.org/wayback/available",
            params={"url": url, **({"timestamp": ts} if ts else {})},
        )
        snap = (data or {}).get("archived_snapshots", {}).get("closest")
        if not snap:
            return {"found": False, "url": url}
        return {
            "found": True,
            "archive_url": snap.get("url"),
            "snapshot_timestamp": snap.get("timestamp"),
            "status": snap.get("status"),
        }
    except Exception as exc:
        return {"error": str(exc)}


def fetch_archive_text(url: str, target_date: str = "", max_chars: int = 6000) -> dict[str, Any]:
    """Read the actual TEXT of a Wayback Machine snapshot — not just prove it
    existed, but extract the archived page's title and body so you can quote
    titles/dates/content from a deleted or rewritten page. target_date: YYYYMMDD
    (closest snapshot on/near it). Use after fetch_archive_snapshot when you need
    what the page SAID, not just that it was captured."""
    from app.core.net_guard import guarded_get
    from app.modules.scraper.core.web_fetch import html_to_plain_text

    snap = fetch_archive_snapshot(url, target_date)
    if snap.get("error") or not snap.get("found"):
        return snap if snap.get("error") else {"found": False, "url": url}
    archive_url = snap.get("archive_url") or ""
    # Insert the `id_` flag after the 14-digit timestamp to fetch the RAW capture
    # (no Wayback toolbar/banner injected into the HTML).
    import re

    raw_url = re.sub(
        r"(/web/\d{14})/", r"\1id_/", archive_url, count=1
    ) or archive_url
    try:
        r = guarded_get(raw_url, headers={"User-Agent": _UA}, timeout=15.0)
        r.raise_for_status()
    except Exception as exc:
        return {"archive_url": archive_url, "error": str(exc)[:200]}
    title = ""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(r.text, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()[:200]
        text = html_to_plain_text(str(soup))
    except Exception:
        text = html_to_plain_text(r.text)
    cap = max(500, min(int(max_chars), 12000))
    return {
        "found": True,
        "archive_url": archive_url,
        "snapshot_timestamp": snap.get("snapshot_timestamp"),
        "title": title,
        "text": text[:cap],
        "chars": len(text),
        "truncated": len(text) > cap,
    }


def extract_document_metadata(file_url: str) -> dict[str, Any]:
    """EXIF (images) / document properties (PDF) from a leaked file URL:
    author, creation time, GPS, producing software."""
    try:
        from app.core.net_guard import guarded_get

        r = guarded_get(file_url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        r.raise_for_status()
        # Inspect the WHOLE file — truncating a PDF chops its xref/streams and makes
        # pypdf fail on otherwise-valid documents — but cap to avoid OOM on huge files.
        raw = r.content
        if len(raw) > 25_000_000:
            return {"error": "file too large to inspect (>25MB)", "bytes": len(raw)}
        ctype = r.headers.get("content-type", "").lower()
        if "pdf" in ctype or file_url.lower().endswith(".pdf"):
            from io import BytesIO

            from pypdf import PdfReader

            meta = PdfReader(BytesIO(raw)).metadata or {}
            return {"kind": "pdf", "metadata": {str(k): str(v) for k, v in meta.items()}}
        try:
            from io import BytesIO

            from PIL import Image
            from PIL.ExifTags import GPSTAGS, TAGS

            img = Image.open(BytesIO(raw))
            # getexif() is the supported API (Pillow 10+ deprecated _getexif); the
            # camera/EXIF and GPS details live in sub-IFDs reached via get_ifd().
            exif = img.getexif()
            out = {TAGS.get(k, str(k)): str(v)[:200] for k, v in exif.items()}
            try:
                for k, v in exif.get_ifd(0x8769).items():  # ExifIFD (software, timestamps)
                    out[TAGS.get(k, str(k))] = str(v)[:200]
            except Exception:
                pass
            try:
                gps = exif.get_ifd(0x8825)  # GPSInfo
                if gps:
                    out["GPS"] = {GPSTAGS.get(k, str(k)): str(v) for k, v in gps.items()}
            except Exception:
                pass
            return {"kind": "image", "format": img.format, "size": img.size, "exif": out}
        except Exception:
            return {
                "kind": "binary",
                "note": "no EXIF/PDF metadata extractable",
                "content_type": ctype,
            }
    except Exception as exc:
        return {"error": str(exc)}


# --- Identity --------------------------------------------------------------
def resolve_domain_infrastructure(domain: str) -> dict[str, Any]:
    """WHOIS-equivalent (RDAP) + DNS A records + IP geolocation/host. Finds the
    physical servers and registration behind a site."""
    out: dict[str, Any] = {"domain": domain}
    d = domain.strip().lower().removeprefix("http://").removeprefix("https://").split("/")[0]
    out["domain"] = d
    try:
        ips = _resolve_ips(d)
        out["ip_addresses"] = ips
        if ips:
            try:
                geo = _get(f"http://ip-api.com/json/{ips[0]}")
                out["host"] = {
                    "country": geo.get("country"),
                    "org": geo.get("org"),
                    "isp": geo.get("isp"),
                    "city": geo.get("city"),
                }
            except Exception:
                pass
    except Exception as exc:
        out["dns_error"] = str(exc)
    try:
        rdap = _get(f"https://rdap.org/domain/{d}")
        events = {e.get("eventAction"): e.get("eventDate") for e in (rdap.get("events") or [])}
        out["registration"] = {
            "registered": events.get("registration"),
            "expires": events.get("expiration"),
            "last_changed": events.get("last changed"),
            "nameservers": [ns.get("ldhName") for ns in (rdap.get("nameservers") or [])],
        }
    except Exception:
        out["rdap"] = "unavailable"
    return out


def screen_sanctions_and_pep(person_name: str, dob: str = "") -> dict[str, Any]:
    """OpenSanctions: flag PEPs, sanctioned entities, watchlist hits. Uses
    OPENSANCTIONS_API_KEY when set."""
    try:
        key = os.getenv("OPENSANCTIONS_API_KEY", "").strip()
        headers = {"Authorization": f"ApiKey {key}"} if key else None
        query = f"{person_name} {dob}".strip() if dob else person_name
        data = _get(
            "https://api.opensanctions.org/search/default",
            params={"q": query, "limit": 5},
            headers=headers,
        )
        results = []
        for r in (data.get("results") or [])[:5]:
            props = r.get("properties", {})
            results.append(
                {
                    "name": r.get("caption"),
                    "schema": r.get("schema"),
                    "topics": props.get("topics", []),
                    "datasets": r.get("datasets", [])[:4],
                }
            )
        return {"query": person_name, "hits": len(results), "results": results}
    except Exception as exc:
        return {"error": str(exc), "note": "OpenSanctions may require OPENSANCTIONS_API_KEY"}


def query_corporate_registry(company_name: str, jurisdiction: str = "") -> dict[str, Any]:
    """OpenCorporates: board members, incorporation date, registered address.
    Uses OPENCORPORATES_API_TOKEN when set."""
    try:
        token = os.getenv("OPENCORPORATES_API_TOKEN", "").strip()
        params = {"q": company_name, "per_page": 5}
        if jurisdiction:
            params["jurisdiction_code"] = jurisdiction
        if token:
            params["api_token"] = token
        data = _get("https://api.opencorporates.com/v0.4/companies/search", params=params)
        companies = (data.get("results", {}) or {}).get("companies", [])
        out = []
        for item in companies[:5]:
            c = item.get("company", {})
            out.append(
                {
                    "name": c.get("name"),
                    "number": c.get("company_number"),
                    "jurisdiction": c.get("jurisdiction_code"),
                    "incorporated": c.get("incorporation_date"),
                    "status": c.get("current_status"),
                    "address": c.get("registered_address_in_full"),
                }
            )
        return {"query": company_name, "matches": len(out), "companies": out}
    except Exception as exc:
        return {"error": str(exc), "note": "OpenCorporates may require OPENCORPORATES_API_TOKEN"}


# --- History ---------------------------------------------------------------
def query_court_dockets(entity_name: str) -> dict[str, Any]:
    """CourtListener: civil/criminal cases, bankruptcies. Token optional via
    COURTLISTENER_TOKEN."""
    try:
        token = os.getenv("COURTLISTENER_TOKEN", "").strip()
        headers = {"Authorization": f"Token {token}"} if token else None
        data = _get(
            "https://www.courtlistener.com/api/rest/v4/search/",
            params={"q": entity_name, "order_by": "score desc"},
            headers=headers,
        )
        results = []
        for r in (data.get("results") or [])[:5]:
            results.append(
                {
                    "case": r.get("caseName"),
                    "court": r.get("court"),
                    "date": r.get("dateFiled"),
                    "docket": r.get("docketNumber"),
                }
            )
        return {"query": entity_name, "count": data.get("count"), "results": results}
    except Exception as exc:
        return {"error": str(exc)}


def search_leak_databases(entity_name: str) -> dict[str, Any]:
    """ICIJ Offshore Leaks (Panama/Pandora/Paradise Papers). Best-effort HTML
    search; no official API."""
    try:
        html = _get(
            "https://offshoreleaks.icij.org/search",
            params={"q": entity_name},
            headers={"Accept": "text/html"},
        )
        if not isinstance(html, str):
            return {"query": entity_name, "note": "unexpected response"}
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for a in soup.select("table a[href*='/nodes/']")[:6]:
            rows.append(a.get_text(" ").strip())
        return {"query": entity_name, "hits": len(rows), "entities": rows}
    except Exception as exc:
        return {"error": str(exc), "note": "ICIJ has no official API; best-effort"}


INVESTIGATIVE_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fetch_archive_snapshot",
            "description": (
                "Wayback Machine snapshot of a URL near a date — proves what a page "
                "said before edits/deletion."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "target_date": {"type": "string", "description": "YYYYMMDD, optional"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_archive_text",
            "description": (
                "Read the TEXT of a Wayback snapshot (title + body) to quote "
                "titles/dates/content from a deleted or rewritten page — not just "
                "prove it existed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "target_date": {"type": "string", "description": "YYYYMMDD, optional"},
                    "max_chars": {
                        "type": "integer",
                        "description": "cap on returned text, default 6000",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_document_metadata",
            "description": (
                "EXIF/PDF metadata (author, timestamps, GPS, software) from a leaked "
                "file URL."
            ),
            "parameters": {
                "type": "object",
                "properties": {"file_url": {"type": "string"}},
                "required": ["file_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_domain_infrastructure",
            "description": (
                "WHOIS/RDAP + DNS + IP host/geo behind a domain — who registered it "
                "and where it is hosted."
            ),
            "parameters": {
                "type": "object",
                "properties": {"domain": {"type": "string"}},
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_sanctions_and_pep",
            "description": (
                "OpenSanctions check: is a person/entity a PEP, sanctioned, or on a "
                "watchlist?"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "person_name": {"type": "string"},
                    "dob": {"type": "string"},
                },
                "required": ["person_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_corporate_registry",
            "description": (
                "OpenCorporates: board, incorporation date, registered address, "
                "status of a company."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "jurisdiction": {"type": "string"},
                },
                "required": ["company_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_court_dockets",
            "description": (
                "CourtListener: lawsuits, bankruptcies, criminal cases against an entity."
            ),
            "parameters": {
                "type": "object",
                "properties": {"entity_name": {"type": "string"}},
                "required": ["entity_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_leak_databases",
            "description": "ICIJ Offshore Leaks (Panama/Pandora Papers) hits for an entity.",
            "parameters": {
                "type": "object",
                "properties": {"entity_name": {"type": "string"}},
                "required": ["entity_name"],
            },
        },
    },
]

INVESTIGATIVE_HANDLERS: dict[str, Any] = {
    "fetch_archive_snapshot": fetch_archive_snapshot,
    "fetch_archive_text": fetch_archive_text,
    "extract_document_metadata": extract_document_metadata,
    "resolve_domain_infrastructure": resolve_domain_infrastructure,
    "screen_sanctions_and_pep": screen_sanctions_and_pep,
    "query_corporate_registry": query_corporate_registry,
    "query_court_dockets": query_court_dockets,
    "search_leak_databases": search_leak_databases,
}
