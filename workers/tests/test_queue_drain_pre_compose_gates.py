"""The standard drain's pre-compose vetoes are a uniform, ordered gate list
(_PRE_COMPOSE_GATES) rather than four hand-rolled if/continue blocks. These
tests pin the extraction's contract: first-match-wins order, exact status
names, and which gates move a row out of the pending lane. (The individual
checks have their own tests, e.g. test_queue_drain_novelty.py.)"""

from types import SimpleNamespace

from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _row():
    return SimpleNamespace(
        queue_id="q1", service_id="svc", scrape_url="https://example.com",
        payload={"page_title": "t", "page_text": "x"},
    )


def test_gate_order_and_names():
    assert [g.name for g in qdt._PRE_COMPOSE_GATES] == [
        "brief_archived",
        "domain_capped",
        "domain_cooldown",
        "service_cooldown",
        "novelty_collapsed",
    ]


def test_mark_status_only_for_cap_and_novelty():
    marks = {g.name: g.mark_status for g in qdt._PRE_COMPOSE_GATES}
    assert marks == {
        "brief_archived": "expired",
        "domain_capped": "deferred",
        "domain_cooldown": None,
        "service_cooldown": None,
        "novelty_collapsed": "expired",
    }


def test_gates_wrap_the_real_checks():
    checks = [g.check for g in qdt._PRE_COMPOSE_GATES]
    assert checks == [
        qdt._brief_archived,
        qdt._domain_capped,
        qdt._domain_in_cooldown,
        qdt._service_in_cooldown,
        qdt._novelty_collapsed,
    ]


def _assignment_row(status, *, brief_id="b1", source_kind="editorial_assignment"):
    return SimpleNamespace(
        queue_id="q1", service_id="svc", scrape_url=f"editorial://brief/{brief_id}",
        payload={"source_kind": source_kind, "brief_id": brief_id},
    )


def test_brief_archived_vetoes_archived_brief(monkeypatch):
    monkeypatch.setattr(
        "app.modules.newspaper.editorial_assignment.get_brief",
        lambda bid: SimpleNamespace(brief_id=bid, status="archived"),
    )
    assert qdt._brief_archived(_assignment_row("archived")) is True


def test_brief_archived_allows_active_brief(monkeypatch):
    monkeypatch.setattr(
        "app.modules.newspaper.editorial_assignment.get_brief",
        lambda bid: SimpleNamespace(brief_id=bid, status="active"),
    )
    assert qdt._brief_archived(_assignment_row("active")) is False


def test_brief_archived_ignores_non_editorial_rows():
    # a normal web/service row has no brief — never gated by this check
    assert qdt._brief_archived(_row()) is False


def test_brief_archived_fails_open_on_lookup_error(monkeypatch):
    def _boom(_bid):
        raise RuntimeError("cassandra blip")

    monkeypatch.setattr("app.modules.newspaper.editorial_assignment.get_brief", _boom)
    assert qdt._brief_archived(_assignment_row("archived")) is False


def test_all_pass_returns_none(monkeypatch):
    monkeypatch.setattr(
        qdt, "_PRE_COMPOSE_GATES",
        (qdt._DrainGate("a", lambda r: False), qdt._DrainGate("b", lambda r: False)),
    )
    assert qdt._run_pre_compose_gates(_row()) is None


def test_first_match_wins_and_later_gates_do_not_run(monkeypatch):
    def _must_not_run(_row):
        raise AssertionError("later gate must not be evaluated")

    monkeypatch.setattr(
        qdt, "_PRE_COMPOSE_GATES",
        (
            qdt._DrainGate("first", lambda r: True, mark_status="deferred"),
            qdt._DrainGate("second", _must_not_run, mark_status="expired"),
        ),
    )
    fired = qdt._run_pre_compose_gates(_row())
    assert fired is not None
    assert fired.name == "first"
    assert fired.mark_status == "deferred"


def test_late_gate_fires_after_earlier_ones_pass(monkeypatch):
    monkeypatch.setattr(
        qdt, "_PRE_COMPOSE_GATES",
        (
            qdt._DrainGate("first", lambda r: False),
            qdt._DrainGate("last", lambda r: True, mark_status="expired"),
        ),
    )
    fired = qdt._run_pre_compose_gates(_row())
    assert fired is not None
    assert fired.name == "last"
    assert fired.mark_status == "expired"
