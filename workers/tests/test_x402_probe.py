"""x402 probe / monitoring beat: offer parsing, SSRF guard, store-before-mark, badge set/clear, per-URL isolation.

Fully offline: the HTTP fetch is faked at probe_url's `fetch` seam, storage
at the ProbeRepository seam, and Redis (single_flight) at get_redis.
"""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import ClassVar

import pytest
from algorand_shared.x402_statements import PROBE_QUEUE_NEVER_PROBED
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

    def mark_probed(self, listing: ListingRow, at: datetime) -> None:
        self.events.append(("mark_probed", listing.url_hash, at))

    def set_verified(self, listing: ListingRow, wallet: str, at: datetime | None) -> None:
        self.events.append(("verify", listing.url_hash, wallet, at))

    def extend_term(self, listing: ListingRow, until: datetime) -> None:
        self.events.append(("extend", listing.url_hash, until))


class _FakeQueueRepo:
    """In-memory model of x402_probe_queue's real semantics: ASC-by-last-probed-at, LIMITed, self-pruning of expired rows, reposition on every probe attempt.

    Used to prove the FAIRNESS CONTRACT run_probe_sweep now depends on --
    every live listing gets probed within one full pass regardless of total
    count -- without needing a real Cassandra session. The real
    CassandraProbeRepository's CQL is exercised separately (see the
    Cassandra-repository tests below); this fake proves the ALGORITHM the
    real reads/writes implement is actually fair.
    """

    def __init__(self, listings: list[ListingRow]) -> None:
        # Every listing starts "never probed" (queue_position defaults to
        # PROBE_QUEUE_NEVER_PROBED), exactly like a freshly-seeded row.
        self._by_hash: dict[str, ListingRow] = {item.url_hash: item for item in listings}
        self.probed_order: list[str] = []
        self.term_ends: dict[str, datetime] = {item.url_hash: item.term_end for item in listings}

    def list_live_listings(self, *, now: datetime, limit: int) -> list[ListingRow]:
        live = [
            replace(item, term_end=self.term_ends[item.url_hash])
            for item in self._by_hash.values()
            if self.term_ends[item.url_hash] > now
        ]
        live.sort(key=lambda item: (item.queue_position, item.url_hash))
        return live[:limit]

    def record_result(self, url_hash: str, result: ProbeResult) -> None:  # noqa: ARG002 -- Protocol shape
        self.probed_order.append(url_hash)

    def mark_probed(self, listing: ListingRow, at: datetime) -> None:
        self._by_hash[listing.url_hash] = replace(listing, queue_position=at)

    def set_verified(self, listing: ListingRow, wallet: str, at: datetime | None) -> None:
        pass

    def extend_term(self, listing: ListingRow, until: datetime) -> None:
        self.term_ends[listing.url_hash] = until


class _PointResult:
    """Fakes a Cassandra ResultSet's `.one()` for a single-row (or empty) point read."""

    def __init__(self, row: object | None) -> None:
        self._row = row

    def one(self) -> object | None:
        return self._row


class _FakeCassandraSession:
    """A faithful two-TABLE fake: x402_probe_queue and x402_listings' term_end are genuinely separate dicts, exactly like the real Cassandra tables.

    Deliberately NOT the same trick as _FakeQueueRepo above (which fakes the
    whole ProbeRepository Protocol to prove run_probe_sweep's round-robin
    ALGORITHM is fair): this fake instead sits one layer down, faking the
    Cassandra session itself, so the REAL CassandraProbeRepository code runs
    against it -- list_live_listings(), mark_probed(), and extend_term() all
    execute their actual CQL dispatch. A regression that reads
    x402_probe_queue.term_end for an expiry decision, or that forgets to
    write the canonical x402_listings.term_end on a healthy probe, fails a
    test built on this fixture -- the exact gap the second same-night
    adversarial review found in the prior round's test suite (a fake that
    synthesized a fresh term_end into every queue read from a map extend_term
    conveniently also wrote, which the real repository does not do).
    """

    def __init__(self) -> None:
        # x402_probe_queue: (last_probed_at, url_hash) -> row. Ordering only.
        self.queue: dict[tuple[datetime, str], SimpleNamespace] = {}
        # x402_listings.term_end: url_hash -> term_end. The ONLY canonical
        # field this fixture needs, since it's the only one under test here.
        self.listing_term_end: dict[str, datetime] = {}

    def seed_listing(self, listing: ListingRow) -> None:
        """Seed both tables the way backend's create()/relist() does: a queue row at PROBE_QUEUE_NEVER_PROBED, and the canonical term_end."""
        self.queue[(PROBE_QUEUE_NEVER_PROBED, listing.url_hash)] = SimpleNamespace(
            url_hash=listing.url_hash,
            url=listing.url,
            created_at=listing.created_at,
            tags=set(listing.tags),
            term_end=listing.term_end,
            payer=listing.payer,
            verified_wallet=listing.verified_wallet,
            category=listing.category,
            last_probed_at=PROBE_QUEUE_NEVER_PROBED,
        )
        self.listing_term_end[listing.url_hash] = listing.term_end

    def _execute_queue(self, stmt: str, params: tuple) -> object:
        if stmt.startswith("SELECT"):
            _directory, limit = params
            rows = sorted(self.queue.values(), key=lambda r: (r.last_probed_at, r.url_hash))
            return rows[:limit]
        if stmt.startswith("DELETE"):
            _directory, last_probed_at, url_hash = params
            self.queue.pop((last_probed_at, url_hash), None)
            return []
        # INSERT (seed or reposition).
        (
            _directory,
            last_probed_at,
            url_hash,
            url,
            created_at,
            tags,
            term_end,
            payer,
            verified_wallet,
            category,
        ) = params
        self.queue[(last_probed_at, url_hash)] = SimpleNamespace(
            url_hash=url_hash,
            url=url,
            created_at=created_at,
            tags=tags,
            term_end=term_end,
            payer=payer,
            verified_wallet=verified_wallet,
            category=category,
            last_probed_at=last_probed_at,
        )
        return []

    def _execute_canonical_listings(self, stmt: str, params: tuple) -> object:
        if stmt.startswith("SELECT"):
            (url_hash,) = params
            term_end = self.listing_term_end.get(url_hash)
            return _PointResult(None if term_end is None else SimpleNamespace(term_end=term_end))
        if stmt.startswith("UPDATE") and "SET term_end" in stmt:
            until, url_hash = params
            if url_hash in self.listing_term_end:
                self.listing_term_end[url_hash] = until
            return []
        return []  # e.g. SET_VERIFIED_LISTING -- badge writes, not modeled here

    def execute(self, stmt: str, params: tuple) -> object:
        if "x402_probe_queue" in stmt:
            return self._execute_queue(stmt, params)
        if "x402_listings_by_recency" in stmt or "x402_listings_by_tag" in stmt:
            return []  # projections not modeled: irrelevant to term_end sourcing
        if "x402_listings" in stmt:
            return self._execute_canonical_listings(stmt, params)
        if "x402_probe_results" in stmt or "x402_probe_latest" in stmt:
            return []
        raise AssertionError(f"_FakeCassandraSession: unexpected statement: {stmt}")

    def execute_parallel_with_args(self, stmt: object, params_list: list, **_kw: object) -> list:
        """Fakes app.core.cassandra.execute_parallel_with_args by fanning params_list through this same session's execute() -- same dispatch table as the primary reads/writes, never a second, independently-mutated copy of the data."""
        return [(True, self.execute(stmt, params)) for params in params_list]


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
def test_sweep_records_the_probe_before_marking_verified_and_extending_term() -> None:
    """The probe result is stored first, then the term_end extension, then the badge (store before mark)."""
    repo = _FakeRepo([_listing()])
    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(), body=b""),
        now=NOW,
    )
    assert [e[0] for e in repo.events] == ["record", "mark_probed", "extend", "verify"]
    assert repo.events[3][2:] == (PAYER, NOW)
    assert summary["verified"] == 1
    assert summary["extended"] == 1
    assert summary["probed"] == 1


def test_sweep_clears_a_badge_when_payto_changes() -> None:
    """A verified listing advertising a different payTo gets its badge cleared (wallet "" / at None), and term_end still extends since the probe itself is healthy."""
    repo = _FakeRepo([_listing(verified=PAYER)])
    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(OTHER), body=b""),
        now=NOW,
    )
    assert [e[0] for e in repo.events] == ["record", "mark_probed", "extend", "verify"]
    assert repo.events[-1] == ("verify", "h-https://api.example.com/q", "", None)
    assert summary["cleared"] == 1
    assert summary["extended"] == 1


# --------------------------------------------------------------------------- #
# Probe-fed keep-alive (2026-09-06 pricing-model change, migration 114):
# a HEALTHY probe extends term_end; an UNHEALTHY one changes nothing.
# --------------------------------------------------------------------------- #
def test_a_healthy_probe_extends_term_end_forward_by_the_configured_window() -> None:
    """Reachable AND served_valid_402 pushes term_end to now + X402_LISTING_TERM_DAYS."""
    listing = _listing()
    repo = _FakeRepo([listing])

    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(), body=b""),
        now=NOW,
    )

    extends = [e for e in repo.events if e[0] == "extend"]
    assert len(extends) == 1
    assert extends[0] == (
        "extend",
        listing.url_hash,
        NOW + timedelta(days=config.X402_LISTING_TERM_DAYS),
    )
    assert summary["extended"] == 1


def test_an_unreachable_probe_never_extends_term_end() -> None:
    """An outage must not extend survival -- the same 'transient outage changes nothing' rule the badge already follows."""
    repo = _FakeRepo([_listing()])

    def _unreachable(_url: str) -> RawResponse:
        raise TimeoutError("connection timed out")

    summary = run_probe_sweep(repo=repo, fetch=_unreachable, now=NOW)

    assert [e for e in repo.events if e[0] == "extend"] == []
    assert summary["extended"] == 0
    assert summary["reachable"] == 0


def test_a_reachable_but_invalid_402_never_extends_term_end() -> None:
    """Reachable but NOT a valid x402 challenge is not something a payer could transact against -- must not extend term_end, mirroring probe_leaderboard()'s own 'healthy' definition."""
    repo = _FakeRepo([_listing()])

    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=200, headers={}, body=b"not a 402"),
        now=NOW,
    )

    assert [e for e in repo.events if e[0] == "extend"] == []
    assert summary["extended"] == 0
    assert summary["reachable"] == 1
    assert summary["valid_402"] == 0


def test_extend_term_never_moves_term_end_backward() -> None:
    """A listing whose term_end is already further out than now + the configured window is left alone -- extend_term is only ever called when it would move things forward (run_probe_sweep's own `target > listing.term_end` guard)."""
    far_future = NOW + timedelta(days=config.X402_LISTING_TERM_DAYS + 100)
    listing = replace(_listing(), term_end=far_future)
    repo = _FakeRepo([listing])

    summary = run_probe_sweep(
        repo=repo,
        fetch=lambda _u: RawResponse(status=402, headers=_offer_header(), body=b""),
        now=NOW,
    )

    assert [e for e in repo.events if e[0] == "extend"] == []
    assert summary["extended"] == 0


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
    assert all(e[0] in ("record", "mark_probed", "extend", "verify") for e in repo.events)


# --------------------------------------------------------------------------- #
# Fairness queue: no listing is permanently stranded outside a naive LIMIT
# window regardless of total listing count (finding #3, migration 118)
# --------------------------------------------------------------------------- #
def test_every_listing_is_eventually_probed_and_kept_alive_regardless_of_total_count() -> None:
    """A directory with far more listings than any single sweep's LIMIT still gets every listing probed, and kept alive, within one full round-robin pass -- not just the newest ones.

    Root cause this guards against: the OLD read (newest-created-first,
    LIMITed, never advancing) meant anything past position `limit` was
    NEVER read again once the directory grew past that many rows, so an
    old-but-healthy listing's term_end silently stopped being extended and
    it expired on schedule. The fairness queue (least-recently-probed
    first, repositioned after every attempt) makes the sweep cover
    everyone within ceil(total / limit) runs, independent of creation order
    or total count.
    """
    total_listings = 237
    per_run_limit = 50
    # Long enough to survive every listing's wait for its turn in the
    # round-robin (7 runs x 30 min = 3.5h here) without expiring first --
    # this test is about COVERAGE ordering, not about racing term_end.
    short_term = NOW + timedelta(days=1)
    listings = [
        ListingRow(
            url_hash=f"h{i}",
            url=f"https://svc{i}.example.com/x",
            created_at=NOW - timedelta(days=total_listings - i),  # i=0 is the OLDEST listing
            tags=(),
            term_end=short_term,
            payer=PAYER,
            verified_wallet="",
        )
        for i in range(total_listings)
    ]
    repo = _FakeQueueRepo(listings)

    def _fetch(_url: str) -> RawResponse:
        return RawResponse(status=402, headers=_offer_header(), body=b"")

    # One full round-robin pass takes ceil(total/limit) runs; run a couple
    # extra to prove it does not merely happen to land exactly on the
    # boundary.
    runs = -(-total_listings // per_run_limit) + 2
    moment = NOW
    for _ in range(runs):
        moment += timedelta(minutes=30)
        run_probe_sweep(repo=repo, fetch=_fetch, now=moment, limit=per_run_limit)

    # Every listing -- including h0, the OLDEST one, which the old
    # newest-first LIMITed read would never have reached once the directory
    # passed `per_run_limit` rows -- was actually probed at least once.
    assert set(repo.probed_order) == {item.url_hash for item in listings}
    # And every listing is still alive: each healthy probe pushed term_end
    # forward, so nothing that started with a term expiring in an hour is
    # still sitting at that original short_term after several 30-minute
    # sweeps.
    assert all(end > short_term for end in repo.term_ends.values())


def test_an_unhealthy_listing_cannot_camp_at_the_front_of_the_queue() -> None:
    """A listing that never passes its probe still gets repositioned (mark_probed runs regardless of health), so it cannot permanently hog the front of the least-recently-probed ordering and starve everyone else."""
    always_broken = ListingRow(
        url_hash="broken",
        url="https://broken.example.com/x",
        created_at=NOW,
        tags=(),
        term_end=NOW + timedelta(days=30),
        payer=PAYER,
        verified_wallet="",
    )
    healthy = [
        ListingRow(
            url_hash=f"ok{i}",
            url=f"https://ok{i}.example.com/x",
            created_at=NOW,
            tags=(),
            # Long enough that an unprobed one waiting its turn never expires
            # mid-test (3 runs x 30 min here) -- this test is about ORDERING
            # fairness, not about racing term_end.
            term_end=NOW + timedelta(days=1),
            payer=PAYER,
            verified_wallet="",
        )
        for i in range(5)
    ]
    repo = _FakeQueueRepo([always_broken, *healthy])

    def _fetch(url: str) -> RawResponse:
        if "broken" in url:
            raise TimeoutError("connect timed out")
        return RawResponse(status=402, headers=_offer_header(), body=b"")

    moment = NOW
    for _ in range(3):
        moment += timedelta(minutes=30)
        run_probe_sweep(repo=repo, fetch=_fetch, now=moment, limit=2)

    # Every healthy listing got probed even though "broken" is always first
    # alphabetically/by-hash and never succeeds -- it did not get to camp at
    # the front of every single run.
    assert {item.url_hash for item in healthy}.issubset(set(repo.probed_order))


# --------------------------------------------------------------------------- #
# Cassandra repository: statement ordering + expiry filter
# --------------------------------------------------------------------------- #
def test_repository_filters_expired_listings_and_writes_history_before_latest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIST reads x402_probe_queue for ordering, judges expiry off a CANONICAL term_end read (not the queue row's own, possibly-stale copy), prunes an expired row with a DELETE instead of carrying it forward; record_result writes x402_probe_results then x402_probe_latest; set_verified touches canonical, recency, then every tag row including the reserved category row.

    "live"'s queue row carries a deliberately WRONG/stale term_end (100 days
    in the past -- exactly what the second same-night adversarial-review
    regression would have judged expired) while its CANONICAL term_end is
    healthy; "dead"'s queue row carries the opposite lie (100 days in the
    future) while its canonical term_end has actually lapsed. Only the
    canonical value may decide either listing's fate -- this is what proves
    list_live_listings() no longer trusts the queue's own denormalized copy.
    """
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> list:
            executed.append((stmt, params))
            if "x402_probe_queue" in stmt and stmt.startswith("SELECT"):
                return [
                    SimpleNamespace(
                        url_hash="live",
                        url="https://l.example.com",
                        created_at=NOW,
                        tags={"fx"},
                        term_end=NOW - timedelta(days=100),
                        payer=PAYER,
                        verified_wallet=None,
                        category="finance",
                        last_probed_at=NOW - timedelta(days=2),
                    ),
                    SimpleNamespace(
                        url_hash="dead",
                        url="https://d.example.com",
                        created_at=NOW,
                        tags=set(),
                        term_end=NOW + timedelta(days=100),
                        payer=PAYER,
                        verified_wallet=None,
                        category=None,
                        last_probed_at=NOW - timedelta(days=5),
                    ),
                ]
            if (
                stmt.startswith("SELECT")
                and "x402_listings" in stmt
                and "x402_listings_by" not in stmt
            ):
                (url_hash,) = params
                canonical = {"live": NOW + timedelta(days=1), "dead": NOW - timedelta(days=1)}
                return _PointResult(SimpleNamespace(term_end=canonical[url_hash]))
            return []

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = _Session()
    monkeypatch.setattr("app.modules.x402_probe.service.get_cassandra_session", lambda: session)
    monkeypatch.setattr(
        "app.modules.x402_probe.service.execute_parallel_with_args",
        lambda stmt, params_list, **_kw: [(True, session.execute(stmt, p)) for p in params_list],
    )
    repo = CassandraProbeRepository()
    listings = repo.list_live_listings(now=NOW, limit=50)
    assert [item.url_hash for item in listings] == ["live"]
    assert executed[0][1] == ("default", 50)
    assert "category" in executed[0][0]
    assert listings[0].category == "finance"
    assert listings[0].queue_position == NOW - timedelta(days=2)
    # The kept listing's term_end is the CANONICAL value, not the queue
    # row's own (deliberately wrong, deliberately stale) copy.
    assert listings[0].term_end == NOW + timedelta(days=1)
    # The expired "dead" row is pruned outright, not merely skipped, so it
    # cannot keep occupying a slot in every future LIMITed read -- even
    # though ITS queue snapshot optimistically claimed 100 more days.
    deletes = [(s, p) for s, p in executed if s.startswith("DELETE")]
    assert len(deletes) == 1
    stmt, params = deletes[0]
    assert "x402_probe_queue" in stmt
    assert params == ("default", NOW - timedelta(days=5), "dead")

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


def test_extend_term_touches_canonical_recency_and_every_tag_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """extend_term() writes term_end to the canonical row, the recency projection, and every by-tag row (including the reserved category row) -- same shape and IF EXISTS guard as set_verified()."""
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
        tags=("fx",),
        term_end=NOW + timedelta(days=1),
        payer=PAYER,
        verified_wallet="",
        category="finance",
    )
    until = NOW + timedelta(days=30)

    CassandraProbeRepository().extend_term(listing, until)

    tables = [s.split(" ")[1] for s, _ in executed]
    assert tables == [
        "algorand_platform.x402_listings",
        "algorand_platform.x402_listings_by_recency",
        "algorand_platform.x402_listings_by_tag",
        "algorand_platform.x402_listings_by_tag",
    ]
    assert all("IF EXISTS" in s for s, _ in executed)
    assert executed[0][1] == (until, "h")
    assert executed[1][1] == (until, "default", NOW, "h")
    assert executed[2][1] == (until, "fx", NOW, "h")
    assert executed[3][1] == (until, "category:finance", NOW, "h")


def test_mark_probed_deletes_the_old_queue_position_and_inserts_the_new_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """mark_probed() deletes the row at the listing's OLD queue_position, then inserts a fresh row at the new timestamp -- the reposition that makes the queue round-robin (migration 118)."""
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> list:
            executed.append((stmt, params))
            return []

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr("app.modules.x402_probe.service.get_cassandra_session", lambda: _Session())
    old_position = NOW - timedelta(days=3)
    listing = ListingRow(
        url_hash="h",
        url="https://l.example.com",
        created_at=NOW - timedelta(days=10),
        tags=("fx",),
        term_end=NOW + timedelta(days=1),
        payer=PAYER,
        verified_wallet="",
        category="finance",
        queue_position=old_position,
    )

    CassandraProbeRepository().mark_probed(listing, NOW)

    assert len(executed) == 2
    delete_stmt, delete_params = executed[0]
    assert delete_stmt.startswith("DELETE")
    assert "x402_probe_queue" in delete_stmt
    assert delete_params == ("default", old_position, "h")
    insert_stmt, insert_params = executed[1]
    assert insert_stmt.startswith("INSERT")
    assert "x402_probe_queue" in insert_stmt
    assert insert_params == (
        "default",
        NOW,
        "h",
        "https://l.example.com",
        listing.created_at,
        {"fx"},
        listing.term_end,
        PAYER,
        "",
        "finance",
    )


# --------------------------------------------------------------------------- #
# Regression (second same-night adversarial-review finding, 2026-09-06): a
# healthy listing must survive indefinitely, never dropped from the queue
# purely because of elapsed wall-clock time since its last extension.
# --------------------------------------------------------------------------- #
def test_a_healthy_listing_survives_many_sweep_cycles_past_the_term_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listing that keeps passing its probe is never pruned from the queue merely because X402_LISTING_TERM_DAYS has elapsed since it was created/last relisted.

    Root cause this guards against: x402_probe_queue.term_end is written
    once at seed/reposition time and never refreshed by extend_term(),
    which only ever writes the canonical x402_listings row (and its
    recency/by-tag projections). Judging expiry off the queue's own copy
    (the bug this test exists to catch) means a listing gets pruned and
    permanently stops being reprobed after X402_LISTING_TERM_DAYS from
    creation, no matter how many times extend_term() has since pushed the
    REAL term_end forward -- worse than the LIMIT-window bug migration 118
    itself fixed, since it hits every listing at any scale. This runs the
    REAL CassandraProbeRepository (via run_probe_sweep) against
    _FakeCassandraSession, which keeps the queue and the canonical
    x402_listings term_end as genuinely separate dicts -- a reintroduction
    of the bug (list_live_listings trusting the queue's own term_end, or
    extend_term failing to write the canonical one) fails this test.
    """
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    session = _FakeCassandraSession()
    monkeypatch.setattr("app.modules.x402_probe.service.get_cassandra_session", lambda: session)
    monkeypatch.setattr(
        "app.modules.x402_probe.service.execute_parallel_with_args",
        session.execute_parallel_with_args,
    )

    created_at = NOW - timedelta(days=1)
    listing = ListingRow(
        url_hash="h-durable",
        url="https://durable.example.com/x",
        created_at=created_at,
        tags=("fx",),
        term_end=created_at + timedelta(days=config.X402_LISTING_TERM_DAYS),
        payer=PAYER,
        verified_wallet="",
    )
    session.seed_listing(listing)

    repo = CassandraProbeRepository()

    def _fetch(_url: str) -> RawResponse:
        # served_valid_402=True (healthy) with a payTo that is not a valid
        # Algorand address, so payto_seen == "" and badge_decision() never
        # fires (keeps this fixture's dispatch table to only what the fix
        # under test needs: queue ordering + canonical term_end).
        return RawResponse(status=402, headers=_offer_header("not-an-address"), body=b"")

    # Sweep well past X402_LISTING_TERM_DAYS worth of elapsed wall-clock
    # time since creation -- always healthy, one sweep per simulated day --
    # exactly the scenario that silently failed before this fix.
    moment = NOW
    cycles = config.X402_LISTING_TERM_DAYS + 15
    for _ in range(cycles):
        moment += timedelta(days=1)
        summary = run_probe_sweep(repo=repo, fetch=_fetch, now=moment, limit=50)
        assert summary["failed"] == 0
        assert summary["probed"] == 1

    # Still in the fairness queue (never pruned) and its canonical term_end
    # keeps getting pushed forward -- still comfortably alive past `moment`.
    assert any(row.url_hash == "h-durable" for row in session.queue.values())
    assert session.listing_term_end["h-durable"] > moment

    # One more sweep proves it is still actually being reached by
    # list_live_listings(), not merely sitting unpruned but unreachable.
    moment += timedelta(minutes=1)
    final = run_probe_sweep(repo=repo, fetch=_fetch, now=moment, limit=50)
    assert final["listings"] == 1
    assert final["probed"] == 1


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
