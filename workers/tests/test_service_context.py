from app.modules.ai.mistral_client import _parse_json_object
from app.modules.newspaper.service_context import ContextPage, build_service_context


def _pages() -> list[ContextPage]:
    return [
        ContextPage(url="https://ex.io/zeta", title="Zeta", body="zeta body words"),
        ContextPage(url="https://ex.io/alpha", title="Alpha", body="alpha body words"),
    ]


def test_aggregate_entry_first_then_url_order() -> None:
    out = build_service_context(
        service_id="ex-io",
        display_name="Example",
        entry_url="https://ex.io",
        entry_title="Example home",
        entry_text="home text",
        pages=_pages(),
    )
    home = out.index("PAGE: https://ex.io\n")
    alpha = out.index("PAGE: https://ex.io/alpha")
    zeta = out.index("PAGE: https://ex.io/zeta")
    assert home < alpha < zeta  # entry first, harvest in URL order
    assert out.startswith("# SERVICE WATCH: Example")


def test_aggregate_is_stable_across_page_input_order() -> None:
    a = build_service_context(
        service_id="s", display_name="S", entry_url="https://s.io",
        entry_title="t", entry_text="x", pages=_pages(),
    )
    b = build_service_context(
        service_id="s", display_name="S", entry_url="https://s.io",
        entry_title="t", entry_text="x", pages=list(reversed(_pages())),
    )
    assert a == b


def test_aggregate_respects_total_cap(monkeypatch) -> None:
    import app.core.config as config

    monkeypatch.setattr(config, "SERVICE_CONTEXT_MAX_CHARS", 300)
    pages = [
        ContextPage(url=f"https://ex.io/p{i}", title=f"P{i}", body="w " * 500)
        for i in range(10)
    ]
    out = build_service_context(
        service_id="s", display_name="S", entry_url="https://s.io",
        entry_title="t", entry_text="entry", pages=pages,
    )
    assert len(out) <= 320  # cap plus joiners


def test_aggregate_falls_back_to_entry_only() -> None:
    out = build_service_context(
        service_id="s", display_name="S", entry_url="https://s.io",
        entry_title="Home", entry_text="only the entry", pages=[],
    )
    assert "only the entry" in out
    assert out.count("## PAGE:") == 1


def test_fair_share_selection_across_hosts() -> None:
    from datetime import UTC, datetime, timedelta

    from app.modules.newspaper.service_context import _fair_share_by_host

    now = datetime.now(tz=UTC)
    busy = [
        (now - timedelta(minutes=i), f"id-b{i}", f"https://blog.x.io/p{i}", f"B{i}")
        for i in range(10)
    ]
    quiet = [
        (now - timedelta(days=5), "id-f", "https://forum.x.io/latest", "Forum"),
        (now - timedelta(days=9), "id-d", "https://docs.x.io/guide", "Docs"),
    ]
    picked = _fair_share_by_host(busy + quiet, max_pages=6)
    hosts = {u.split("/")[2] for _, _, u, _ in picked}
    # The quiet hosts must be represented despite the busy host's recency.
    assert "forum.x.io" in hosts and "docs.x.io" in hosts
    assert len(picked) == 6


def test_parse_json_object_salvages_fences_and_prose() -> None:
    assert _parse_json_object('{"a": 1}') == {"a": 1}
    assert _parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json_object('Here is the result:\n{"a": {"b": 2}} hope it helps') == {
        "a": {"b": 2}
    }
    assert _parse_json_object("no json here") is None
    assert _parse_json_object('[1, 2]') is None  # root must be an object
