"""Source-type router: root domains are static profiles, deep paths are news."""

from app.modules.ai.mistral_compose import is_static_landing_page


def test_root_domain_is_static() -> None:
    assert is_static_landing_page("https://tinyman.org")
    assert is_static_landing_page("https://defly.app/")
    assert is_static_landing_page("https://perawallet.app/about")


def test_deep_path_is_not_static() -> None:
    assert not is_static_landing_page("https://medium.com/algorand/tinyman-v2-launch")
    assert not is_static_landing_page("https://tinyman.org/blog/v2-is-live")
    assert not is_static_landing_page("https://x.com/algofoundation/status/123")


def test_malformed_url_is_safe() -> None:
    assert not is_static_landing_page("")
