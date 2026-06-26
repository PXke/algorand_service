from __future__ import annotations

from app.modules.admin.stores.cassandra import _rank_reviews


def _item(url: str, score: float) -> dict:
    return {"url": url, "storage_score": score}


def test_orders_by_promise_score_desc() -> None:
    items = [
        _item("https://a.com/1", 0.2),
        _item("https://b.com/1", 0.9),
        _item("https://c.com/1", 0.5),
    ]
    ranked = _rank_reviews(items, limit=10)
    assert [r["url"] for r in ranked] == [
        "https://b.com/1",
        "https://c.com/1",
        "https://a.com/1",
    ]


def test_caps_items_per_source() -> None:
    # One source floods with high-scoring pages; cap keeps it from dominating
    # the window while other sources still appear.
    flood = [_item(f"https://flood.com/{i}", 0.9 - i * 0.01) for i in range(20)]
    others = [_item("https://other.com/1", 0.4), _item("https://misc.org/1", 0.3)]
    ranked = _rank_reviews(flood + others, limit=10, per_source=3)
    flood_count = sum(1 for r in ranked if "flood.com" in r["url"])
    assert flood_count == 3
    urls = {r["url"] for r in ranked}
    assert "https://other.com/1" in urls
    assert "https://misc.org/1" in urls


def test_www_prefix_treated_as_same_source() -> None:
    items = [_item(f"https://www.flood.com/{i}", 0.9) for i in range(5)]
    items += [_item(f"https://flood.com/x{i}", 0.9) for i in range(5)]
    ranked = _rank_reviews(items, limit=10, per_source=3)
    assert len(ranked) == 3


def test_single_source_window_stays_capped() -> None:
    # Only one source available: the window is intentionally short rather than
    # showing more than the cap from that one source. The rest stay pending.
    items = [_item(f"https://solo.com/{i}", 0.9 - i * 0.01) for i in range(10)]
    ranked = _rank_reviews(items, limit=5, per_source=3)
    assert len(ranked) == 3
    assert [r["url"] for r in ranked] == [f"https://solo.com/{i}" for i in range(3)]
