"""Phase-1 investigative-journalism tools for the Mistral agent.

Every tool is a stateless external lookup (no new database; results may be
persisted to Cassandra by the caller). All handlers are timeout-bounded and
failure-tolerant: on any error they return {"error": ...} so a tool failure
never aborts the article. Optional API keys are read from env when present.

Provenance -> Identity -> Assets -> History -> Network.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

logger = logging.getLogger(__name__)

_UA = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"
_TIMEOUT = 12.0


def _get(url: str, *, headers: dict | None = None, params: dict | None = None) -> Any:  # noqa: ANN401 -- returns parsed JSON (dict/list) or plain text depending on content-type
    from app.core.net_guard import guarded_get

    h = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    r = guarded_get(url, headers=h, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    ct = r.headers.get("content-type", "")
    return r.json() if "json" in ct else r.text


def _guarded_get(
    url: str, *, headers: dict | None = None, params: dict | None = None, timeout: float = _TIMEOUT
) -> Any:  # noqa: ANN401 -- httpx.Response, kept loosely typed to avoid an import just for a type hint
    """Like _get but returns the raw response without raise_for_status -- for callers that need to branch on status_code (e.g. a 404 meaning "not registered" rather than a real failure)."""
    from app.core.net_guard import guarded_get

    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    return guarded_get(url, headers=h, params=params, timeout=timeout)


def _resolve_ips(host: str, timeout: float = 5.0) -> list[str]:
    """Bounded DNS resolution. socket.getaddrinfo has no native timeout and can block for tens of seconds on a slow resolver — which would violate this module's timeout-bounded contract — so run it in a thread we can abandon."""
    import concurrent.futures

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(lambda: sorted({ai[4][0] for ai in socket.getaddrinfo(host, None)}))
        return fut.result(timeout=timeout)
    finally:
        ex.shutdown(wait=False)


# --- Provenance ------------------------------------------------------------
# fetch_archive_text (below) used to be three separate schemas --
# lookup_wayback_snapshots (research_tools.py), fetch_archive_snapshot and
# fetch_archive_text (both here) -- added ~6 weeks apart. Merged 2026-08-27:
# they're one Wayback Machine workflow (find the coverage window -> find a
# snapshot near a date -> read that snapshot's text), not independent
# capabilities, and the writer routinely could not tell which of the three to
# reach for (the "wayback"/"archive" suggest_tool alias could only ever point
# at one). `action` picks the step; each step's return shape is unchanged
# from its original standalone tool.
_ARCHIVE_ACTIONS = ("dates", "snapshot", "text")


def _wayback_capture_date(resp: Any) -> str | None:  # noqa: ANN401 -- httpx.Response, kept loose to avoid an import just for a type hint
    """The capture date (YYYY-MM-DD) from a CDX API response's single data row, or None — row 0 is always a header, not data."""
    try:
        rows = resp.json()
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    ts = rows[1][1] if isinstance(rows[1], list) and len(rows[1]) > 1 else None
    if not isinstance(ts, str) or len(ts) < 8:
        return None
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"


def _archive_dates(url: str) -> dict[str, Any]:
    """action='dates': first and most recent known Internet Archive snapshot DATES for a URL (a coverage window, not content), via the Wayback Machine's CDX API (free, no key). Use to check how long a site has actually existed, or whether its content changed recently, instead of trusting a fetch_url's current state as the whole history — root-caused 2026-08-06: a compose tried to fetch archive.ph directly for exactly this kind of check and hit a 429, with no fallback. Was the standalone lookup_wayback_snapshots tool before the 2026-08-27 merge."""
    from app.core.net_guard import guarded_get

    raw = (url or "").strip()
    if not raw:
        return {"error": "url is required"}
    try:
        first_resp = guarded_get(
            "https://web.archive.org/cdx/search/cdx",
            headers={"User-Agent": _UA},
            params={"url": raw, "output": "json", "limit": "1"},
            timeout=20.0,
        )
        last_resp = guarded_get(
            "https://web.archive.org/cdx/search/cdx",
            headers={"User-Agent": _UA},
            params={"url": raw, "output": "json", "limit": "-1"},
            timeout=20.0,
        )
    except Exception as exc:
        return {"url": raw, "error": str(exc)[:200]}
    if first_resp.status_code != 200 or last_resp.status_code != 200:
        return {
            "url": raw,
            "error": f"wayback CDX {first_resp.status_code}/{last_resp.status_code}",
        }
    first_seen = _wayback_capture_date(first_resp)
    last_seen = _wayback_capture_date(last_resp)
    if first_seen is None and last_seen is None:
        return {"url": raw, "found": False, "error": "no archive.org snapshots found"}
    return {"url": raw, "found": True, "first_seen": first_seen, "last_seen": last_seen}


def _archive_snapshot(url: str, target_date: str = "") -> dict[str, Any]:
    """action='snapshot': Wayback Machine closest snapshot — proves a page existed/said something on a date, even if later edited or deleted. target_date: YYYYMMDD. Returns ONE snapshot's archive_url/timestamp, not a coverage history. Was the standalone fetch_archive_snapshot tool before the 2026-08-27 merge."""
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


def _archive_text(url: str, target_date: str = "", max_chars: int = 6000) -> dict[str, Any]:
    """action='text' (default): read the actual TEXT of a Wayback Machine snapshot — not just prove it existed, but extract the archived page's title and body so you can quote titles/dates/content from a deleted or rewritten page. target_date: YYYYMMDD (closest snapshot on/near it). Was the standalone fetch_archive_text tool's body before the 2026-08-27 merge (same name kept for the merged tool)."""
    from app.core.net_guard import guarded_get
    from app.modules.scraper.core.web_fetch import html_to_plain_text

    snap = _archive_snapshot(url, target_date)
    if snap.get("error") or not snap.get("found"):
        return snap if snap.get("error") else {"found": False, "url": url}
    archive_url = snap.get("archive_url") or ""
    # Insert the `id_` flag after the 14-digit timestamp to fetch the RAW capture
    # (no Wayback toolbar/banner injected into the HTML).
    import re

    raw_url = re.sub(r"(/web/\d{14})/", r"\1id_/", archive_url, count=1) or archive_url
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


def fetch_archive_text(
    url: str, action: str = "text", target_date: str = "", max_chars: int = 6000
) -> dict[str, Any]:
    """Internet Archive (Wayback Machine) lookups for a URL — one tool, three related jobs picked by `action` (merged 2026-08-27 from three near-duplicate tools; see the module comment above the ``action`` docstrings for why).

    action='dates': first/last known snapshot DATES (a coverage window, not
    content) — how long a site has existed, or whether it changed recently.
    action='snapshot': the CLOSEST single snapshot to target_date (YYYYMMDD,
    optional — omit for the latest capture) — proves a page existed/said
    something on/near a date without reading it; archive_url/timestamp/status
    only.
    action='text' (default): that closest snapshot's actual TEXT (title +
    body) — quote titles/dates/content from a deleted or rewritten page,
    not just prove it was captured. Use after action='snapshot' when you
    need what the page SAID, not just that it was captured.
    """
    act = (action or "text").strip().lower()
    if act == "dates":
        return _archive_dates(url)
    if act == "snapshot":
        return _archive_snapshot(url, target_date)
    if act == "text":
        return _archive_text(url, target_date, max_chars)
    return {"error": f"action must be one of {_ARCHIVE_ACTIONS}, got {action!r}"}


def extract_document_metadata(file_url: str) -> dict[str, Any]:
    """EXIF (images) / document properties (PDF) from a leaked file URL: author, creation time, GPS, producing software."""
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
                logger.debug("no ExifIFD sub-tags for %s", file_url, exc_info=True)
            try:
                gps = exif.get_ifd(0x8825)  # GPSInfo
                if gps:
                    out["GPS"] = {GPSTAGS.get(k, str(k)): str(v) for k, v in gps.items()}
            except Exception:
                logger.debug("no GPSInfo sub-tags for %s", file_url, exc_info=True)
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
def _rdap_registrar_name(entities: list[Any]) -> str | None:
    for e in entities:
        if not isinstance(e, dict) or "registrar" not in (e.get("roles") or []):
            continue
        for field in e.get("vcardArray") or []:
            if not isinstance(field, list) or len(field) != 2:
                continue
            for entry in field[1:]:
                if isinstance(entry, list) and len(entry) == 4 and entry[0] == "fn":
                    return entry[3]
    return None


def _normalize_domain(domain: str) -> str:
    import re

    raw = (domain or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw).split("/")[0]
    return re.sub(r"^www\.", "", raw)


def _hosting_lookup(host: str) -> dict[str, Any]:
    """DNS A records + the first IP's geolocation/hosting org, tolerant of either step failing independently."""
    out: dict[str, Any] = {}
    try:
        ips = _resolve_ips(host)
    except Exception as exc:
        out["dns_error"] = str(exc)
        return out
    out["ip_addresses"] = ips
    if not ips:
        return out
    try:
        geo = _get(f"http://ip-api.com/json/{ips[0]}")
        out["host"] = {
            "country": geo.get("country"),
            "org": geo.get("org"),
            "isp": geo.get("isp"),
            "city": geo.get("city"),
        }
    except Exception:
        logger.debug("ip geolocation lookup failed for %s", ips[0], exc_info=True)
    return out


def _rdap_lookup(host: str, *, include_hosting: bool) -> dict[str, Any]:
    """Registration fields (found/error, or registered_at/expires_at/last_changed_at/registrar), plus nameservers when include_hosting."""
    try:
        resp = _guarded_get(f"https://rdap.org/domain/{host}", timeout=15.0)
    except Exception as exc:
        return {"found": False, "error": str(exc)[:200]}
    if resp.status_code == 404:
        return {
            "found": False,
            "error": "no RDAP record (unregistered, or a ccTLD RDAP.org doesn't route)",
        }
    if resp.status_code != 200:
        return {"found": False, "error": f"RDAP {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:
        return {"found": False, "error": "unexpected RDAP response"}
    events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events") or []}
    out: dict[str, Any] = {
        "found": True,
        "registered_at": events.get("registration"),
        "expires_at": events.get("expiration"),
        "last_changed_at": events.get("last changed"),
        "registrar": _rdap_registrar_name(data.get("entities") or []),
    }
    if include_hosting:
        out["nameservers"] = [ns.get("ldhName") for ns in (data.get("nameservers") or [])]
    return out


def resolve_domain_infrastructure(domain: str, include_hosting: bool = False) -> dict[str, Any]:
    """RDAP domain registration (date/expiry/registrar), optionally plus WHOIS-equivalent hosting detail: DNS A records + IP geolocation/host + nameservers. Merged 2026-08-27 from two tools that both hit rdap.org for the same registration data via separate code paths (the former standalone lookup_domain_registration in research_tools.py, and this tool's own registration lookup) -- ``include_hosting`` now picks the depth instead of picking a different tool name.

    include_hosting=False (default): the lightweight, registration-only
    check -- is a project's site brand-new or established -- one RDAP call,
    no DNS/IP lookups.
    include_hosting=True: additionally resolves DNS A records, the first
    IP's geolocation/hosting org/ISP, and the domain's nameservers -- who
    actually runs the physical infrastructure behind it, not just who
    registered the name.
    """
    host = _normalize_domain(domain)
    if not host or "." not in host:
        return {"error": "a valid domain is required, e.g. example.com"}
    out: dict[str, Any] = {"domain": host}
    if include_hosting:
        out.update(_hosting_lookup(host))
    out.update(_rdap_lookup(host, include_hosting=include_hosting))
    return out


def screen_sanctions_and_pep(person_name: str, dob: str = "") -> dict[str, Any]:
    """OpenSanctions: flag PEPs, sanctioned entities, watchlist hits. Uses OPENSANCTIONS_API_KEY when set."""
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

    Uses OPENCORPORATES_API_TOKEN when set.
    """
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


def query_uk_companies_house(company_name: str = "", company_number: str = "") -> dict[str, Any]:
    """UK Companies House: incorporation date, status, registered office, SIC codes for a UK-registered company.

    query_corporate_registry (OpenCorporates) covers ~140 jurisdictions
    including the UK, but is disabled entirely without a paid
    OPENCORPORATES_API_TOKEN. Companies House's own API is free and
    UK-specific, so it's worth having as a dedicated tool rather than only
    reachable through OpenCorporates' aggregation — useful whenever a story
    claims a project is "a registered UK company" or names a UK entity.

    Pass company_number for a direct profile lookup (exact, richer detail);
    company_name for a name search (returns candidates, no company_number
    guessing). Uses COMPANIES_HOUSE_API_KEY (HTTP Basic auth, username=key,
    no password) — free to obtain from Companies House's developer hub.
    """
    import base64

    from app.core.net_guard import guarded_get

    key = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
    if not key:
        return {"error": "COMPANIES_HOUSE_API_KEY not configured"}
    number = (company_number or "").strip()
    name = (company_name or "").strip()
    if not number and not name:
        return {"error": "company_name or company_number required"}

    # HTTP Basic auth, username=key, no password -- guarded_get has no auth=
    # kwarg (SSRF-guarded GET, header-only), so build the header directly.
    basic = base64.b64encode(f"{key}:".encode()).decode()
    headers = {"User-Agent": _UA, "Accept": "application/json", "Authorization": f"Basic {basic}"}
    try:
        if number:
            resp = guarded_get(
                f"https://api.company-information.service.gov.uk/company/{number}",
                headers=headers,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 404:
                return {"company_number": number, "error": "no company with this number"}
            resp.raise_for_status()
            c = resp.json()
            return {
                "name": c.get("company_name"),
                "number": c.get("company_number"),
                "status": c.get("company_status"),
                "type": c.get("type"),
                "incorporated": c.get("date_of_creation"),
                "sic_codes": c.get("sic_codes"),
                "registered_office": c.get("registered_office_address"),
            }
        resp = guarded_get(
            "https://api.company-information.service.gov.uk/search/companies",
            headers=headers,
            params={"q": name, "items_per_page": 5},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        out = [
            {
                "name": item.get("title"),
                "number": item.get("company_number"),
                "status": item.get("company_status"),
                "incorporated": item.get("date_of_creation"),
                "address": item.get("address_snippet"),
            }
            for item in (data.get("items") or [])[:5]
        ]
        return {"query": name, "matches": len(out), "companies": out}
    except Exception as exc:
        return {"error": str(exc)}


# --- History ---------------------------------------------------------------
def query_court_dockets(entity_name: str) -> dict[str, Any]:
    """CourtListener: civil/criminal cases, bankruptcies. Token optional via COURTLISTENER_TOKEN."""
    try:
        token = os.getenv("COURTLISTENER_TOKEN", "").strip()
        headers = {"Authorization": f"Token {token}"} if token else None
        data = _get(
            "https://www.courtlistener.com/api/rest/v4/search/",
            params={"q": entity_name, "order_by": "score desc"},
            headers=headers,
        )
        results = [
            {
                "case": r.get("caseName"),
                "court": r.get("court"),
                "date": r.get("dateFiled"),
                "docket": r.get("docketNumber"),
            }
            for r in (data.get("results") or [])[:5]
        ]
        return {"query": entity_name, "count": data.get("count"), "results": results}
    except Exception as exc:
        return {"error": str(exc)}


def search_leak_databases(entity_name: str) -> dict[str, Any]:
    """ICIJ Offshore Leaks (Panama/Pandora/Paradise Papers). Best-effort HTML search; no official API."""
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
        rows = [a.get_text(" ").strip() for a in soup.select("table a[href*='/nodes/']")[:6]]
        return {"query": entity_name, "hits": len(rows), "entities": rows}
    except Exception as exc:
        return {"error": str(exc), "note": "ICIJ has no official API; best-effort"}


# Archive/infrastructure tools: useful on ANY story (verify what a page said,
# who runs a domain), so they register unconditionally.
ARCHIVE_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "fetch_archive_text",
            "description": (
                "Internet Archive (Wayback Machine) lookups for a URL, one of three "
                "jobs picked by `action` (merged 2026-08-27 from three "
                "near-duplicate tools -- lookup_wayback_snapshots, "
                "fetch_archive_snapshot, fetch_archive_text -- into this single "
                "schema): action='dates' for first/last known snapshot DATES (a "
                "coverage window, not content) -- how long a site has existed, or "
                "whether it changed recently; action='snapshot' for the CLOSEST "
                "single snapshot to target_date (YYYYMMDD, optional) -- proves a "
                "page existed/said something on/near a date, archive_url/"
                "timestamp/status only, no content; action='text' (default) to "
                "read that closest snapshot's actual TEXT (title + body) -- quote "
                "titles/dates/content from a deleted or rewritten page, not just "
                "prove it was captured."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "action": {
                        "type": "string",
                        "enum": ["dates", "snapshot", "text"],
                        "description": "'dates' (coverage window), 'snapshot' (metadata near a date), or 'text' (default -- read that snapshot's content)",
                    },
                    "target_date": {
                        "type": "string",
                        "description": "YYYYMMDD, optional -- used by action='snapshot'/'text' only",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "cap on returned text, default 6000 -- used by action='text' only",
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
                "EXIF/PDF metadata (author, timestamps, GPS, software) from a leaked file URL."
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
                "Domain registration date/expiry/registrar via RDAP (WHOIS "
                "successor, no key needed) -- the default, lightweight check for "
                "whether a project's site is brand-new or established. Merged "
                "2026-08-27 from two tools that both hit rdap.org for the same "
                "registration data (the former lookup_domain_registration is "
                "now this tool's default, include_hosting=false behavior). Pass "
                "include_hosting=true to additionally resolve DNS A records, the "
                "server IP's geolocation/hosting org/ISP, and the domain's "
                "nameservers -- who actually runs the physical infrastructure "
                "behind it, not just who registered the name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string"},
                    "include_hosting": {
                        "type": "boolean",
                        "description": "true for DNS/IP/geo/nameservers on top of registration data, default false",
                    },
                },
                "required": ["domain"],
            },
        },
    },
]

# Entity-background OSINT (sanctions, corporate registries, court dockets,
# offshore leaks): only relevant when a story investigates a person/company —
# scam alerts and editorial assignments. Prod transcripts showed generic stories
# never call these, so keeping them out of that lane saves schema budget and
# keeps the tool list scannable for the model.
ENTITY_OSINT_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "screen_sanctions_and_pep",
            "description": (
                "OpenSanctions check: is a person/entity a PEP, sanctioned, or on a watchlist?"
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
            "name": "query_uk_companies_house",
            "description": (
                "UK Companies House: incorporation date, status, registered "
                "office, SIC codes for a UK-registered company. Free, "
                "UK-specific alternative to query_corporate_registry for "
                "verifying a 'registered UK company' claim. Pass "
                "company_number for an exact profile, or company_name to "
                "search for candidates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company_name": {"type": "string"},
                    "company_number": {"type": "string"},
                },
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

ARCHIVE_HANDLERS: dict[str, Any] = {
    "fetch_archive_text": fetch_archive_text,
    "extract_document_metadata": extract_document_metadata,
    "resolve_domain_infrastructure": resolve_domain_infrastructure,
}

ENTITY_OSINT_HANDLERS: dict[str, Any] = {
    "screen_sanctions_and_pep": screen_sanctions_and_pep,
    "query_corporate_registry": query_corporate_registry,
    "query_uk_companies_house": query_uk_companies_house,
    "query_court_dockets": query_court_dockets,
    "search_leak_databases": search_leak_databases,
}


def investigative_tools(
    *, include_entity_osint: bool
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Investigative toolset as (schemas, handlers).

    Archive/infrastructure tools always register. Entity-background OSINT joins
    only for investigative lanes, and query_corporate_registry only when
    OPENCORPORATES_API_TOKEN is set (without it OpenCorporates returns a
    permanent 401, so registering it just burns a writer turn) — same reasoning
    for query_uk_companies_house and COMPANIES_HOUSE_API_KEY.
    """
    schemas = list(ARCHIVE_SCHEMAS)
    handlers = dict(ARCHIVE_HANDLERS)
    if include_entity_osint:
        has_oc_token = bool(os.getenv("OPENCORPORATES_API_TOKEN", "").strip())
        has_ch_key = bool(os.getenv("COMPANIES_HOUSE_API_KEY", "").strip())
        skip_unconfigured = {
            "query_corporate_registry": not has_oc_token,
            "query_uk_companies_house": not has_ch_key,
        }
        for schema in ENTITY_OSINT_SCHEMAS:
            name = schema["function"]["name"]
            if skip_unconfigured.get(name):
                continue
            schemas.append(schema)
            handlers[name] = ENTITY_OSINT_HANDLERS[name]
    return schemas, handlers
