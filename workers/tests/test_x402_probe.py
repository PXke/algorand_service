"""x402 probe / monitoring beat: offer parsing, SSRF guard, store-before-mark, badge set/clear, per-URL isolation.

Fully offline: the HTTP fetch is faked at probe_url's `fetch` seam, storage
at the ProbeRepository seam, and Redis (single_flight) at get_redis.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

import pytest
from celery.exceptions import SoftTimeLimitExceeded

import app.celery_app as celery_app
from app.core import config
from app.core import redis_lock as redis_lock_module
from app.core.net_guard import UnsafeUrlError, assert_public_url
from app.modules.x402_probe import probe as probe_module
from app.modules.x402_probe.probe import (
    PROBE_USER_AGENT,
    ProbeDeadlineExceeded,
    ProbeResult,
    RawResponse,
    parse_offer,
    probe_url,
)
from app.modules.x402_probe.service import (
    CassandraProbeRepository,
    ListingRow,
    badge_decision,
    run_probe_sweep,
)
from app.modules.x402_probe.tasks import probe_tasks

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
# Real (checksum-valid) Algorand addresses: parse_offer only lets those through.
PAYER = "OBYYVU3ECOL7CEFWMB3DCEN5V4HJV472KENDIZHOI6ASVVV5BUNMEIA6ZE"
OTHER = "D2JBLXHWIUBIB2SZUGZF2FIUSL6MT3VBX623VADSMX5I6NKIAGHTHZYEAI"


def _offer_header(payto: str | None = PAYER, *, accepts: list | None = None) -> dict[str, str]:
    body = {"x402Version": 2, "accepts": accepts if accepts is not None else [{"payTo": payto}]}
    return {"PAYMENT-REQUIRED": base64.b64encode(json.dumps(body).encode()).decode()}


def _listing(
    *, verified: str = "", payer: str = PAYER, url: str = "https://api.example.com/q"
) -> ListingRow:
    return ListingRow(
        url_hash="h-" + url,
        url=url,
        created_at=NOW - timedelta(days=1),
        tags=("fx",),
        term_end=NOW + timedelta(days=10),
        payer=payer,
        verified_wallet=verified,
    )


class _FakeRepo:
    def __init__(self, listings: list[ListingRow]) -> None:
        self.listings = listings
        self.events: list[tuple] = []

    def list_live_listings(self, *, now: datetime, limit: int) -> list[ListingRow]:  # noqa: ARG002 -- name must match the Protocol
        return self.listings[:limit]

    def record_result(self, url_hash: str, result: ProbeResult) -> None:
        self.events.append(("record", url_hash, result))

    def set_verified(self, listing: ListingRow, wallet: str, at: datetime | None) -> None:
        self.events.append(("verify", listing.url_hash, wallet, at))


# --------------------------------------------------------------------------- #
# Offer parsing
# --------------------------------------------------------------------------- #
def test_parse_offer_reads_payto_from_the_base64_header() -> None:
    """A 402 with a base64 JSON PAYMENT-REQUIRED header carrying accepts[] is valid and yields payTo."""
    raw = RawResponse(status=402, headers=_offer_header(), body=b"")
    assert parse_offer(raw) == (True, PAYER)


def test_parse_offer_falls_back_to_a_json_body() -> None:
    """A v1-style 402 with the offer in the JSON body (no header) still parses."""
    raw = RawResponse(
        status=402, headers={}, body=json.dumps({"accepts": [{"payTo": OTHER}]}).encode()
    )
    assert parse_offer(raw) == (True, OTHER)


@pytest.mark.parametrize(
    ("status", "headers", "body"),
    [
        (200, _offer_header(), b""),
        (402, {}, b"not json"),
        (402, {"PAYMENT-REQUIRED": "!!!not-base64!!!"}, b""),
        (402, _offer_header(accepts=[]), b""),
        (402, _offer_header(accepts=["nope"]), b""),
        (402, {}, json.dumps({"other": 1}).encode()),
    ],
)
def test_parse_offer_rejects_malformed_offers(status: int, headers: dict, body: bytes) -> None:
    """Anything that is not a 402 with a parsable accepts[] list is not a valid offer."""
    assert parse_offer(RawResponse(status=status, headers=headers, body=body)) == (False, "")


@pytest.mark.parametrize(
    "payto",
    ["A" * 58, "0x" + "ab" * 20, "<script>alert(1)</script>", "x" * 5000, PAYER[:-1] + "B"],
)
def test_parse_offer_stores_an_empty_payto_when_it_is_not_an_algorand_address(
    payto: str,
) -> None:
    """A well-formed offer whose payTo is not a checksum-valid Algorand address is still a valid 402, but payTo is recorded as "" (it is served verbatim on free routes)."""
    raw = RawResponse(status=402, headers=_offer_header(payto), body=b"")
    assert parse_offer(raw) == (True, "")


# --------------------------------------------------------------------------- #
# probe_url: never raises, records what it saw
# --------------------------------------------------------------------------- #
class _SlowStream:
    """A fake httpx streaming response whose body arrives one byte per `step` fake seconds."""

    status_code = 402
    headers: ClassVar[dict[str, str]] = {}
    is_redirect = False
    url = "https://tarpit.example.com/q"

    def __init__(self, clock: list[float], step: float) -> None:
        self._clock = clock
        self._step = step

    def __enter__(self) -> _SlowStream:
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def iter_bytes(self):  # noqa: ANN202 -- generator faking httpx.Response.iter_bytes
        while True:
            self._clock[0] += self._step
            yield b"."


def test_fetch_unpaid_gives_up_on_a_tarpit_body_at_the_wall_clock_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A body trickling in under httpx's per-read timeout is cut at 2 x the probe timeout instead of running for hours."""
    monkeypatch.setattr(probe_module, "assert_public_url", lambda url: url)
    monkeypatch.setattr(probe_module, "X402_PROBE_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr(probe_module, "X402_PROBE_MAX_BODY_BYTES", 1_000_000)
    clock = [1000.0]
    monkeypatch.setattr(probe_module, "_monotonic", lambda: clock[0])
    stream = _SlowStream(clock, step=4.0)
    monkeypatch.setattr(
        probe_module,
        "get_http_client",
        lambda **_kw: SimpleNamespace(stream=lambda *_a, **_k: stream),
    )
    with pytest.raises(ProbeDeadlineExceeded):
        probe_module.fetch_unpaid("https://tarpit.example.com/q")
    # 5 s timeout x factor 2 = 10 s budget; at 4 s/byte the third chunk trips it.
    assert clock[0] - 1000.0 <= 12.0


def test_fetch_unpaid_gives_up_on_slow_redirect_hops_at_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redirect hops share the same wall-clock budget: a slow 302 chain stops at the deadline, not at the redirect cap."""
    monkeypatch.setattr(probe_module, "assert_public_url", lambda url: url)
    monkeypatch.setattr(probe_module, "X402_PROBE_TIMEOUT_SECONDS", 5.0)
    clock = [0.0]
    monkeypatch.setattr(probe_module, "_monotonic", lambda: clock[0])
    hops: list[str] = []

    class _Redirect:
        status_code = 302
        headers: ClassVar[dict[str, str]] = {"location": "/next"}
        is_redirect = True
        url = "https://slow.example.com/start"

        def __enter__(self) -> _Redirect:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def _stream(_method: str, url: str, **_k: object) -> _Redirect:
        hops.append(url)
        clock[0] += 6.0
        return _Redirect()

    monkeypatch.setattr(
        probe_module, "get_http_client", lambda **_kw: SimpleNamespace(stream=_stream)
    )
    with pytest.raises(ProbeDeadlineExceeded):
        probe_module.fetch_unpaid("https://slow.example.com/start")
    assert len(hops) == 2  # 0 s and 6 s are within budget; 12 s is past the 10 s deadline


def test_probe_url_records_a_deadline_as_reachable_with_error_deadline() -> None:
    """A tarpit is recorded (reachable=True, error="deadline") and never granted a valid offer; the sweep moves on."""

    def _tarpit(_url: str) -> RawResponse:
        raise ProbeDeadlineExceeded("probe deadline exceeded")

    result = probe_url("https://tarpit.example.com/q", fetch=_tarpit, now=NOW)
    assert result.reachable is True
    assert result.served_valid_402 is False
    assert result.payto_seen == ""
    assert result.error == "deadline"


def test_probe_url_records_a_valid_402() -> None:
    """A reachable endpoint serving a valid offer is recorded with its status and payTo."""
    result = probe_url(
        "https://api.example.com/q",
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(), body=b""),
        now=NOW,
    )
    assert result.reachable is True
    assert result.http_status == 402
    assert result.served_valid_402 is True
    assert result.payto_seen == PAYER
    assert result.error == ""
    assert result.probed_at == NOW


def test_probe_url_turns_an_ssrf_rejection_into_unreachable() -> None:
    """An SSRF-guard rejection is a stored failure, not an exception out of the sweep."""

    def _blocked(_url: str) -> RawResponse:
        raise UnsafeUrlError("non-public IP host: 127.0.0.1")

    result = probe_url("http://127.0.0.1/admin", fetch=_blocked, now=NOW)
    assert result.reachable is False
    assert result.served_valid_402 is False
    assert "UnsafeUrlError" in result.error


def test_fetch_unpaid_refuses_private_targets_before_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch_unpaid runs the SSRF guard before opening a connection: a loopback URL never reaches httpx."""
    calls: list[str] = []

    def _client(**_kw: object) -> object:
        calls.append("client-built")
        return SimpleNamespace(stream=lambda *_a, **_k: pytest.fail("must not fetch"))

    monkeypatch.setattr(probe_module, "get_http_client", _client)
    with pytest.raises(UnsafeUrlError):
        probe_module.fetch_unpaid("http://127.0.0.1:9042/")
    with pytest.raises(UnsafeUrlError):
        probe_module.fetch_unpaid("ftp://example.com/x")


def test_fetch_unpaid_sends_no_payment_header_and_labels_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe request carries the probe User-Agent and no payment header, and the body read is capped."""
    monkeypatch.setattr(probe_module, "assert_public_url", lambda url: url)
    monkeypatch.setattr(probe_module, "X402_PROBE_MAX_BODY_BYTES", 8)
    seen: dict[str, object] = {}

    class _Stream:
        status_code = 402
        headers = _offer_header()
        is_redirect = False
        url = "https://api.example.com/q"

        def __enter__(self) -> _Stream:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def iter_bytes(self):  # noqa: ANN202 -- generator faking httpx.Response.iter_bytes
            yield b"0123456789"
            yield b"more"

    def _stream(method: str, url: str, *, headers: dict, content: bytes | None) -> _Stream:
        seen.update(method=method, url=url, headers=headers, content=content)
        return _Stream()

    monkeypatch.setattr(
        probe_module, "get_http_client", lambda **_kw: SimpleNamespace(stream=_stream)
    )
    raw = probe_module.fetch_unpaid("https://api.example.com/q")
    assert seen["method"] == "GET"
    assert seen["headers"]["User-Agent"] == PROBE_USER_AGENT
    assert not any(k.lower() in ("x-payment", "payment-signature") for k in seen["headers"])
    assert raw.body == b"01234567"
    assert raw.status == 402


# --------------------------------------------------------------------------- #
# Badge decision
# --------------------------------------------------------------------------- #
def _result(*, valid: bool, payto: str) -> ProbeResult:
    return ProbeResult(
        url="u",
        probed_at=NOW,
        reachable=valid,
        http_status=402 if valid else 0,
        latency_ms=1,
        served_valid_402=valid,
        payto_seen=payto,
        error="",
    )


def test_badge_is_granted_when_payto_matches_the_payer() -> None:
    """An unverified listing whose endpoint advertises the payer's wallet gets the badge."""
    assert badge_decision(_listing(), _result(valid=True, payto=PAYER)) == PAYER


def test_badge_is_left_alone_when_already_verified_and_unchanged() -> None:
    """A verified listing still advertising the same wallet is not rewritten."""
    assert badge_decision(_listing(verified=PAYER), _result(valid=True, payto=PAYER)) is None


def test_badge_is_cleared_when_a_verified_listing_changes_payto() -> None:
    """A verified listing whose endpoint now advertises a different wallet loses the badge."""
    assert badge_decision(_listing(verified=PAYER), _result(valid=True, payto=OTHER)) == ""


def test_badge_is_not_touched_by_an_unreachable_or_malformed_probe() -> None:
    """An outage or a broken offer neither grants nor revokes anything."""
    assert badge_decision(_listing(verified=PAYER), _result(valid=False, payto="")) is None
    assert badge_decision(_listing(), _result(valid=False, payto="")) is None


def test_badge_is_not_granted_to_an_unowned_listing() -> None:
    """A listing with no payer can never be verified, whatever payTo is advertised."""
    assert badge_decision(_listing(payer=""), _result(valid=True, payto=PAYER)) is None


# --------------------------------------------------------------------------- #
# The sweep: store before mark, per-URL isolation
# --------------------------------------------------------------------------- #
def test_sweep_records_the_probe_before_marking_verified() -> None:
    """The probe result is stored first, the badge written second (store before mark)."""
    repo = _FakeRepo([_listing()])
    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(), body=b""),
        now=NOW,
    )
    assert [e[0] for e in repo.events] == ["record", "verify"]
    assert repo.events[1][2:] == (PAYER, NOW)
    assert summary["verified"] == 1
    assert summary["probed"] == 1


def test_sweep_clears_a_badge_when_payto_changes() -> None:
    """A verified listing advertising a different payTo gets its badge cleared (wallet "" / at None)."""
    repo = _FakeRepo([_listing(verified=PAYER)])
    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(OTHER), body=b""),
        now=NOW,
    )
    assert repo.events[-1] == ("verify", "h-https://api.example.com/q", "", None)
    assert summary["cleared"] == 1


def test_sweep_survives_a_failing_listing_and_probes_the_rest() -> None:
    """One listing whose store write raises is counted as failed; the next listing is still probed."""
    bad = _listing(url="https://bad.example.com/x")
    good = _listing(url="https://good.example.com/x")

    class _Repo(_FakeRepo):
        def record_result(self, url_hash: str, result: ProbeResult) -> None:
            if "bad" in url_hash:
                raise RuntimeError("cassandra hiccup")
            super().record_result(url_hash, result)

    repo = _Repo([bad, good])
    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(), body=b""),
        now=NOW,
    )
    assert summary["failed"] == 1
    assert summary["verified"] == 1
    assert [e[1] for e in repo.events if e[0] == "record"] == ["h-https://good.example.com/x"]


def test_sweep_reraises_the_soft_time_limit_instead_of_sweeping_on() -> None:
    """Celery's SoftTimeLimitExceeded (an Exception subclass) is not swallowed as a per-URL failure: the sweep stops and the task ends at the soft limit (invariant 6)."""
    listings = [_listing(url=f"https://{i}.example.com/x") for i in range(3)]
    fetched: list[str] = []

    def _fetch(url: str) -> RawResponse:
        fetched.append(url)
        if len(fetched) == 2:
            raise SoftTimeLimitExceeded
        return RawResponse(status=402, headers=_offer_header(), body=b"")

    repo = _FakeRepo(listings)
    with pytest.raises(SoftTimeLimitExceeded):
        run_probe_sweep(repo=repo, fetch=_fetch, now=NOW)
    assert len(fetched) == 2
    assert [e[1] for e in repo.events if e[0] == "record"] == ["h-https://0.example.com/x"]


def test_task_releases_the_lock_when_the_sweep_hits_the_soft_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single_flight lock is released (finally) when the sweep re-raises SoftTimeLimitExceeded."""
    monkeypatch.setattr(probe_tasks, "X402_PROBE_ENABLED", True)

    def _sweep() -> dict[str, object]:
        raise SoftTimeLimitExceeded

    monkeypatch.setattr(probe_tasks, "run_probe_sweep", _sweep)
    released: list[object] = []

    class _Redis:
        def set(self, *_a: object, **_k: object) -> bool:
            return True

        def eval(self, *args: object) -> int:
            released.append(args)
            return 1

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Redis())
    with pytest.raises(SoftTimeLimitExceeded):
        probe_tasks.probe_listed_endpoints()
    assert len(released) == 1


def test_sweep_never_pays_even_when_our_own_endpoint_is_listed() -> None:
    """The fetch is the only network call and it is unpaid: no payment header is ever built."""
    fetched: list[str] = []

    def _fetch(url: str) -> RawResponse:
        fetched.append(url)
        return RawResponse(status=402, headers=_offer_header(), body=b"")

    repo = _FakeRepo([_listing(url="https://algorand-api.pxke.me/api/v1/x402/list")])
    run_probe_sweep(repo=repo, fetch=_fetch, now=NOW)
    assert fetched == ["https://algorand-api.pxke.me/api/v1/x402/list"]
    assert all(e[0] in ("record", "verify") for e in repo.events)


# --------------------------------------------------------------------------- #
# Cassandra repository: statement ordering + expiry filter
# --------------------------------------------------------------------------- #
def test_repository_filters_expired_listings_and_writes_history_before_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIST drops expired rows; record_result writes x402_probe_results then x402_probe_latest; set_verified touches canonical, recency, then every tag row including the reserved category row."""
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> list:
            executed.append((stmt, params))
            if "x402_listings_by_recency" in stmt and stmt.startswith("SELECT"):
                return [
                    SimpleNamespace(
                        url_hash="live",
                        url="https://l.example.com",
                        created_at=NOW,
                        tags={"fx"},
                        term_end=NOW + timedelta(days=1),
                        payer=PAYER,
                        verified_wallet=None,
                        category="finance",
                    ),
                    SimpleNamespace(
                        url_hash="dead",
                        url="https://d.example.com",
                        created_at=NOW,
                        tags=set(),
                        term_end=NOW - timedelta(days=1),
                        payer=PAYER,
                        verified_wallet=None,
                        category=None,
                    ),
                ]
            return []

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr("app.modules.x402_probe.service.get_cassandra_session", lambda: _Session())
    repo = CassandraProbeRepository()
    listings = repo.list_live_listings(now=NOW, limit=50)
    assert [item.url_hash for item in listings] == ["live"]
    assert executed[0][1] == ("default", 50)
    assert "category" in executed[0][0]
    assert listings[0].category == "finance"

    executed.clear()
    repo.record_result("live", _result(valid=True, payto=PAYER))
    assert [s.split(" ")[2] for s, _ in executed] == [
        "algorand_platform.x402_probe_results",
        "algorand_platform.x402_probe_latest",
    ]

    executed.clear()
    repo.set_verified(listings[0], PAYER, NOW)
    tables = [s.split(" ")[1] for s, _ in executed]
    assert tables == [
        "algorand_platform.x402_listings",
        "algorand_platform.x402_listings_by_recency",
        "algorand_platform.x402_listings_by_tag",
        "algorand_platform.x402_listings_by_tag",
    ]
    assert all("IF EXISTS" in s for s, _ in executed)
    assert executed[2][1] == (PAYER, NOW, "fx", NOW, "live")
    # The badge also lands on the reserved category partition (099), which is
    # what GET /x402/search?category= reads.
    assert executed[3][1] == (PAYER, NOW, "category:finance", NOW, "live")


def test_set_verified_writes_the_default_category_row_for_a_pre_099_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listing whose category read back null is projected under category:other, and the badge write reaches that row."""
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> list:
            executed.append((stmt, params))
            return []

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr("app.modules.x402_probe.service.get_cassandra_session", lambda: _Session())
    listing = ListingRow(
        url_hash="h",
        url="https://l.example.com",
        created_at=NOW,
        tags=(),
        term_end=NOW + timedelta(days=1),
        payer=PAYER,
        verified_wallet="",
        category="",
    )
    CassandraProbeRepository().set_verified(listing, "", None)
    by_tag = [p for s, p in executed if "x402_listings_by_tag" in s]
    assert by_tag == [("", None, "category:other", NOW, "h")]


# --------------------------------------------------------------------------- #
# SSRF guard: CGNAT
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", ["100.64.0.1", "100.127.255.254", "192.0.0.8"])
def test_net_guard_rejects_cgnat_and_other_non_global_literals(host: str) -> None:
    """100.64.0.0/10 (CGNAT) is not caught by is_private; is_global rejects it (and the other IANA special-purpose ranges)."""
    with pytest.raises(UnsafeUrlError):
        assert_public_url(f"http://{host}/")


def test_net_guard_still_accepts_a_public_literal() -> None:
    """The is_global check does not over-reject: an ordinary public literal passes with no DNS."""
    assert assert_public_url("https://8.8.8.8/x") == "https://8.8.8.8/x"


# --------------------------------------------------------------------------- #
# Beat wiring + gate
# --------------------------------------------------------------------------- #
def test_beat_entry_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """X402_PROBE_ENABLED defaults to false and the beat entry is absent."""
    assert config.X402_PROBE_ENABLED is False
    monkeypatch.setattr(config, "X402_PROBE_ENABLED", False)
    assert "x402-probe-listed-endpoints" not in celery_app._build_beat_schedule()


def test_beat_entry_present_with_expires_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """When enabled the beat runs every X402_PROBE_INTERVAL_SECONDS with a matching expires."""
    monkeypatch.setattr(config, "X402_PROBE_ENABLED", True)
    monkeypatch.setattr(config, "X402_PROBE_INTERVAL_SECONDS", 1800)
    entry = celery_app._build_beat_schedule()["x402-probe-listed-endpoints"]
    assert entry["task"] == "app.tasks.x402_probe.probe_listed_endpoints"
    assert entry["schedule"] == 1800.0
    assert entry["options"]["expires"] == 1800.0


def test_task_skips_when_disabled_and_lock_ttl_covers_soft_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disabled probe task returns skipped without touching storage; the single_flight TTL is >= the soft time limit."""
    monkeypatch.setattr(probe_tasks, "X402_PROBE_ENABLED", False)
    monkeypatch.setattr(
        probe_tasks, "run_probe_sweep", lambda: pytest.fail("must not sweep when disabled")
    )
    acquired: list[tuple[str, int]] = []

    class _Redis:
        def set(self, key: str, _v: str, *, nx: bool, ex: int) -> bool:  # noqa: ARG002 -- name must match the real callee's keyword arg
            acquired.append((key, ex))
            return True

        def eval(self, *_a: object) -> int:
            return 1

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Redis())
    assert probe_tasks.probe_listed_endpoints() == {
        "status": "skipped",
        "reason": "x402_probe_disabled",
    }
    assert acquired[0][0] == "lock:x402_probe:sweep"
    assert acquired[0][1] >= celery_app.celery_app.conf.task_soft_time_limit


def test_task_fails_open_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis blip on the lock must not stop the sweep (invariant 9: fail open)."""
    monkeypatch.setattr(probe_tasks, "X402_PROBE_ENABLED", True)
    monkeypatch.setattr(probe_tasks, "run_probe_sweep", lambda: {"status": "ok"})

    class _Broken:
        def set(self, *_a: object, **_k: object) -> bool:
            raise ConnectionError("redis down")

        def eval(self, *_a: object) -> int:
            raise ConnectionError("redis down")

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Broken())
    assert probe_tasks.probe_listed_endpoints() == {"status": "ok"}
