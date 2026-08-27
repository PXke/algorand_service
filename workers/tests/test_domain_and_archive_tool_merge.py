"""Coverage for the 2026-08-27 tool consolidation.

Two pairs of near-duplicate research/investigative tools (flagged but
deliberately NOT merged in commit 8ae257c, pending a human decision)
collapsed into one schema each:

- lookup_domain_registration (research_tools.py) + resolve_domain_infrastructure
  (investigative_tools.py) -> one resolve_domain_infrastructure(domain,
  include_hosting=False) -- both hit rdap.org for the same registration data.
- lookup_wayback_snapshots (research_tools.py) + fetch_archive_snapshot +
  fetch_archive_text (investigative_tools.py, both) -> one
  fetch_archive_text(url, action='dates'|'snapshot'|'text') -- three related
  Wayback Machine jobs a writer session routinely confused.

Every test below proves the merged tool still does BOTH (or all three)
original jobs, not just that it "returns something".
"""

from __future__ import annotations

import httpx
import pytest

import app.modules.ai.investigative_tools as it
import app.modules.ai.research_tools as rt
import app.modules.ai.writer_tools as wt


def _json_resp(url: str, payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request("GET", url))


def _plain_resp(url: str, text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=text.encode(),
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", url),
    )


# --------------------------------------------------------- domain-registration


_RDAP_PAYLOAD = {
    "events": [
        {"eventAction": "registration", "eventDate": "2020-01-01T00:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2030-01-01T00:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2025-06-01T00:00:00Z"},
    ],
    "entities": [
        {
            "roles": ["registrar"],
            "vcardArray": [
                "vcard",
                [["version", {}, "text", "4.0"], ["fn", {}, "text", "Namecheap, Inc."]],
            ],
        }
    ],
    "nameservers": [{"ldhName": "ns1.example.com"}, {"ldhName": "ns2.example.com"}],
}


def test_default_is_the_old_lookup_domain_registration_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_hosting defaults False -- exact field set the old lookup_domain_registration returned, and no DNS/IP call is made."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        assert "rdap.org" in url
        return _json_resp(url, _RDAP_PAYLOAD)

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    dns_called = []
    monkeypatch.setattr(it, "_resolve_ips", lambda *_a, **_k: dns_called.append(1) or [])

    out = it.resolve_domain_infrastructure("example.com")

    assert not dns_called, "include_hosting=False must not touch DNS at all"
    assert out["domain"] == "example.com"
    assert out["found"] is True
    assert out["registered_at"] == "2020-01-01T00:00:00Z"
    assert out["expires_at"] == "2030-01-01T00:00:00Z"
    assert out["last_changed_at"] == "2025-06-01T00:00:00Z"
    assert out["registrar"] == "Namecheap, Inc."
    assert "nameservers" not in out
    assert "ip_addresses" not in out
    assert "host" not in out


def test_include_hosting_adds_the_old_resolve_domain_infrastructure_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """include_hosting=True layers DNS/IP/geo/nameservers on top of the same registration data -- the old resolve_domain_infrastructure's full capability."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if "rdap.org" in url:
            return _json_resp(url, _RDAP_PAYLOAD)
        if "ip-api.com" in url:
            return _json_resp(
                url,
                {"country": "United States", "org": "Cloudflare", "isp": "Cloudflare, Inc.", "city": "SF"},
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    monkeypatch.setattr(it, "_resolve_ips", lambda *_a, **_k: ["1.2.3.4"])

    out = it.resolve_domain_infrastructure("example.com", include_hosting=True)

    # Registration data (the "lightweight" contract) is still there...
    assert out["found"] is True
    assert out["registrar"] == "Namecheap, Inc."
    # ...plus everything the old resolve_domain_infrastructure added.
    assert out["ip_addresses"] == ["1.2.3.4"]
    assert out["host"] == {
        "country": "United States",
        "org": "Cloudflare",
        "isp": "Cloudflare, Inc.",
        "city": "SF",
    }
    assert out["nameservers"] == ["ns1.example.com", "ns2.example.com"]


def test_include_hosting_survives_a_dead_rdap_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The old resolve_domain_infrastructure tolerated RDAP failing independently of DNS/IP -- the merge must keep that tolerance, not turn a partial success into a total failure."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if "rdap.org" in url:
            return httpx.Response(404, request=httpx.Request("GET", url))
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    monkeypatch.setattr(it, "_resolve_ips", lambda *_a, **_k: ["9.9.9.9"])

    out = it.resolve_domain_infrastructure("dead-rdap.example", include_hosting=True)

    assert out["ip_addresses"] == ["9.9.9.9"], "DNS data must survive an RDAP failure"
    assert out["found"] is False
    assert "error" in out


def test_bad_domain_input_rejected_before_any_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """An invalid domain fails fast without hitting rdap.org, same as the old lookup_domain_registration."""

    def fail(*_a: object, **_k: object) -> None:
        raise AssertionError("must not call the network for invalid input")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fail)
    out = it.resolve_domain_infrastructure("not-a-domain")
    assert "error" in out


def test_only_one_domain_tool_name_is_registered() -> None:
    """lookup_domain_registration no longer exists as a separate tool anywhere in the registry."""
    inv_schemas, inv_handlers = it.investigative_tools(include_entity_osint=False)
    names = {s["function"]["name"] for s in inv_schemas}
    assert "resolve_domain_infrastructure" in names
    assert "resolve_domain_infrastructure" in inv_handlers
    assert "lookup_domain_registration" not in names
    assert "lookup_domain_registration" not in inv_handlers

    rt_schemas, rt_handlers = rt.research_tools()
    rt_names = {s["function"]["name"] for s in rt_schemas}
    assert "lookup_domain_registration" not in rt_names
    assert "lookup_domain_registration" not in rt_handlers
    assert not hasattr(rt, "_tool_lookup_domain_registration")


def test_domain_schema_exposes_include_hosting_param() -> None:
    """The unified schema advertises include_hosting as a boolean parameter."""
    schemas, _ = it.investigative_tools(include_entity_osint=False)
    schema = next(s for s in schemas if s["function"]["name"] == "resolve_domain_infrastructure")
    props = schema["function"]["parameters"]["properties"]
    assert "domain" in props
    assert "include_hosting" in props
    assert props["include_hosting"]["type"] == "boolean"


# ------------------------------------------------------------------- wayback


def test_action_dates_returns_first_and_last_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """action='dates' replaces the old standalone lookup_wayback_snapshots -- a coverage window, not content."""

    def fake_get(url: str, *, params: dict | None = None, **_kw: object) -> httpx.Response:
        assert "cdx/search/cdx" in url
        if (params or {}).get("limit") == "1":
            return httpx.Response(
                200,
                json=[["header"], ["urlkey", "20200101000000", "..."]],
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            json=[["header"], ["urlkey", "20260601000000", "..."]],
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    out = it.fetch_archive_text("https://example.com", action="dates")
    assert out["found"] is True
    assert out["first_seen"] == "2020-01-01"
    assert out["last_seen"] == "2026-06-01"
    # This action must never touch the availability/snapshot API.
    assert "archive_url" not in out


def test_action_dates_no_snapshots_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """No CDX rows at all -- a clean found=False, not a crash on an empty history."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        return httpx.Response(200, json=[["header"]], request=httpx.Request("GET", url))

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    out = it.fetch_archive_text("https://example.com", action="dates")
    assert out == {"url": "https://example.com", "found": False, "error": "no archive.org snapshots found"}


def test_action_snapshot_returns_metadata_only_no_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """action='snapshot' replaces the old standalone fetch_archive_snapshot -- proof of capture, no page content, no extra HTTP fetch."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        assert "wayback/available" in url
        return _json_resp(
            url,
            {
                "archived_snapshots": {
                    "closest": {
                        "url": "https://web.archive.org/web/20230615000000/https://example.com",
                        "timestamp": "20230615000000",
                        "status": "200",
                    }
                }
            },
        )

    calls = []
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **kw: (calls.append(url), fake_get(url, **kw))[1],
    )
    out = it.fetch_archive_text("https://example.com", action="snapshot", target_date="20230615")
    assert out["found"] is True
    assert out["archive_url"] == "https://web.archive.org/web/20230615000000/https://example.com"
    assert out["snapshot_timestamp"] == "20230615000000"
    assert out["status"] == "200"
    assert "text" not in out
    assert len(calls) == 1, "action='snapshot' must make exactly one HTTP call, not also fetch content"


def test_action_text_default_fetches_snapshot_then_reads_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """action='text' (the default, and the old standalone fetch_archive_text's exact job) chains snapshot-lookup -> raw content fetch -> text extraction."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if "wayback/available" in url:
            return _json_resp(
                url,
                {
                    "archived_snapshots": {
                        "closest": {
                            "url": "https://web.archive.org/web/20230615000000/https://example.com",
                            "timestamp": "20230615000000",
                            "status": "200",
                        }
                    }
                },
            )
        if "web.archive.org/web/20230615000000id_/" in url:
            return _plain_resp(url, "<html><head><title>Old Page</title></head><body>Hello archive</body></html>")
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)

    # Default action (omitted) must behave exactly like the old standalone tool.
    out = it.fetch_archive_text("https://example.com", target_date="20230615")
    assert out["found"] is True
    assert out["title"] == "Old Page"
    assert "Hello archive" in out["text"]
    assert out["archive_url"] == "https://web.archive.org/web/20230615000000/https://example.com"


def test_action_text_propagates_a_missing_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the availability API finds nothing, action='text' reports found=False instead of erroring on a missing archive_url."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        return _json_resp(url, {"archived_snapshots": {}})

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    out = it.fetch_archive_text("https://example.com")
    assert out == {"found": False, "url": "https://example.com"}


def test_unknown_action_is_a_clean_error_not_a_crash() -> None:
    """An unrecognized action returns a normal {"error": ...} dict, same fail-tolerant contract as every other tool here."""
    out = it.fetch_archive_text("https://example.com", action="bogus")
    assert "error" in out
    assert "bogus" in out["error"]


def test_only_one_wayback_tool_name_is_registered() -> None:
    """lookup_wayback_snapshots and fetch_archive_snapshot no longer exist as separate tools; fetch_archive_text is the sole survivor."""
    inv_schemas, inv_handlers = it.investigative_tools(include_entity_osint=False)
    names = {s["function"]["name"] for s in inv_schemas}
    assert names & {"fetch_archive_text"} == {"fetch_archive_text"}
    assert "fetch_archive_snapshot" not in names
    assert "fetch_archive_snapshot" not in inv_handlers
    assert not hasattr(it, "fetch_archive_snapshot")

    rt_schemas, rt_handlers = rt.research_tools()
    rt_names = {s["function"]["name"] for s in rt_schemas}
    assert "lookup_wayback_snapshots" not in rt_names
    assert "lookup_wayback_snapshots" not in rt_handlers
    assert not hasattr(rt, "_tool_lookup_wayback_snapshots")


def test_wayback_schema_exposes_action_enum() -> None:
    """The unified schema advertises all three actions in its enum."""
    schemas, _ = it.investigative_tools(include_entity_osint=False)
    schema = next(s for s in schemas if s["function"]["name"] == "fetch_archive_text")
    props = schema["function"]["parameters"]["properties"]
    assert set(props["action"]["enum"]) == {"dates", "snapshot", "text"}


def test_capability_alias_still_resolves_wayback_to_fetch_archive_text() -> None:
    """The pre-existing 'wayback'/'archive' -> fetch_archive_text alias (writer_tools.py _CAPABILITY_ALIASES) is unaffected by the merge -- this was the exact alias the original audit said the 3-way split was quietly papering over."""
    known = {"fetch_archive_text", "suggest_tool"}
    assert wt._match_existing_tool("wayback_machine_snapshot", known) == "fetch_archive_text"
    assert wt._match_existing_tool("archive_lookup", known) == "fetch_archive_text"


def test_all_tools_registers_the_merged_names_and_not_the_old_ones() -> None:
    """End-to-end through all_tools(): the full writer registry offers exactly the merged names."""
    schemas, handlers = wt.all_tools()
    names = {s["function"]["name"] for s in schemas}
    assert {"resolve_domain_infrastructure", "fetch_archive_text"} <= names
    for old in (
        "lookup_domain_registration",
        "lookup_wayback_snapshots",
        "fetch_archive_snapshot",
    ):
        assert old not in names
        assert old not in handlers
