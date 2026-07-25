"""The standard drain's pre-compose vetoes are a uniform, ordered gate list (_PRE_COMPOSE_GATES) rather than four hand-rolled if/continue blocks. These tests pin the extraction's contract: first-match-wins order, exact status names, and which gates move a row out of the pending lane. (The individual checks have their own tests, e.g. test_queue_drain_novelty.py.)."""

from types import SimpleNamespace
from typing import Never

import pytest

from app.modules.newspaper.publish_queue_store import QueuedPublishRow
from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _row() -> SimpleNamespace:
    return SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com",
        payload={"page_title": "t", "page_text": "x"},
    )


def test_gate_order_and_names() -> None:
    """Pins the pre-compose gate list's exact order and names."""
    assert [g.name for g in qdt._PRE_COMPOSE_GATES] == [
        "brief_archived",
        "domain_capped",
        "domain_cooldown",
        "service_cooldown",
        "novelty_collapsed",
    ]


def test_mark_status_only_for_cap_and_novelty() -> None:
    """Only the brief_archived, domain_capped, and novelty_collapsed gates set a mark_status."""
    marks = {g.name: g.mark_status for g in qdt._PRE_COMPOSE_GATES}
    assert marks == {
        "brief_archived": "expired",
        "domain_capped": "deferred",
        "domain_cooldown": None,
        "service_cooldown": None,
        "novelty_collapsed": "expired",
    }


def test_gates_wrap_the_real_checks() -> None:
    """Each gate's check callable is the real underlying check function, not a copy."""
    checks = [g.check for g in qdt._PRE_COMPOSE_GATES]
    assert checks == [
        qdt._brief_archived,
        qdt._domain_capped,
        qdt._domain_in_cooldown,
        qdt._service_in_cooldown,
        qdt._novelty_collapsed,
    ]


def _assignment_row(
    _status: str, *, brief_id: str = "b1", source_kind: str = "editorial_assignment"
) -> SimpleNamespace:
    return SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url=f"editorial://brief/{brief_id}",
        payload={"source_kind": source_kind, "brief_id": brief_id},
    )


def test_brief_archived_vetoes_archived_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vetoes an editorial-assignment row whose brief has been archived."""
    monkeypatch.setattr(
        "app.modules.newspaper.editorial_assignment.get_brief",
        lambda bid: SimpleNamespace(brief_id=bid, status="archived"),
    )
    assert qdt._brief_archived(_assignment_row("archived")) is True


def test_brief_archived_allows_active_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not veto an editorial-assignment row whose brief is still active."""
    monkeypatch.setattr(
        "app.modules.newspaper.editorial_assignment.get_brief",
        lambda bid: SimpleNamespace(brief_id=bid, status="active"),
    )
    assert qdt._brief_archived(_assignment_row("active")) is False


def test_brief_archived_ignores_non_editorial_rows() -> None:
    """A normal web/service row has no brief, so the brief_archived gate never fires for it."""
    # a normal web/service row has no brief — never gated by this check
    assert qdt._brief_archived(_row()) is False


def test_brief_archived_fails_open_on_lookup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A brief-lookup error fails open — never veto the row just because the lookup errored."""

    def _boom(_bid: str) -> Never:
        raise RuntimeError("cassandra blip")

    monkeypatch.setattr("app.modules.newspaper.editorial_assignment.get_brief", _boom)
    assert qdt._brief_archived(_assignment_row("archived")) is False


def test_all_pass_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns None when every gate check passes (nothing vetoes the row)."""
    monkeypatch.setattr(
        qdt,
        "_PRE_COMPOSE_GATES",
        (qdt._DrainGate("a", lambda _r: False), qdt._DrainGate("b", lambda _r: False)),
    )
    assert qdt._run_pre_compose_gates(_row()) is None


def test_first_match_wins_and_later_gates_do_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stops at the first firing gate and never evaluates later gates in the list."""

    def _must_not_run(_row: QueuedPublishRow) -> Never:
        raise AssertionError("later gate must not be evaluated")

    monkeypatch.setattr(
        qdt,
        "_PRE_COMPOSE_GATES",
        (
            qdt._DrainGate("first", lambda _r: True, mark_status="deferred"),
            qdt._DrainGate("second", _must_not_run, mark_status="expired"),
        ),
    )
    fired = qdt._run_pre_compose_gates(_row())
    assert fired is not None
    assert fired.name == "first"
    assert fired.mark_status == "deferred"


def test_late_gate_fires_after_earlier_ones_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later gate fires and is reported once all earlier gates in the list pass."""
    monkeypatch.setattr(
        qdt,
        "_PRE_COMPOSE_GATES",
        (
            qdt._DrainGate("first", lambda _r: False),
            qdt._DrainGate("last", lambda _r: True, mark_status="expired"),
        ),
    )
    fired = qdt._run_pre_compose_gates(_row())
    assert fired is not None
    assert fired.name == "last"
    assert fired.mark_status == "expired"
