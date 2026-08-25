"""Source-type router: root domains are static profiles, deep paths are news."""

from app.modules.ai.llm_compose import _recency_rule, is_static_landing_page


def test_root_domain_is_static() -> None:
    """Classifies a root or near-root domain path as a static landing page."""
    assert is_static_landing_page("https://tinyman.org")
    assert is_static_landing_page("https://defly.app/")
    assert is_static_landing_page("https://perawallet.app/about")


def test_deep_path_is_not_static() -> None:
    """Classifies a deep blog/social/post path as news content, not a static landing page."""
    assert not is_static_landing_page("https://medium.com/algorand/tinyman-v2-launch")
    assert not is_static_landing_page("https://tinyman.org/blog/v2-is-live")
    assert not is_static_landing_page("https://x.com/algofoundation/status/123")


def test_malformed_url_is_safe() -> None:
    """An empty URL is safely treated as not a static landing page."""
    assert not is_static_landing_page("")


def test_recency_rule_includes_temporal_anchoring() -> None:
    """The recency rule text anchors on the given date and mentions temporal/status-update/TVL guidance."""
    rule = _recency_rule("2026-06-29")
    assert "Temporal anchoring" in rule
    assert "Status Update" in rule
    assert "2026-06-29" in rule
    assert "TVL" in rule
