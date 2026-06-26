from __future__ import annotations

from app.modules.news.services.article_compose import compose_article


def test_compose_article_editorial_without_ops_metadata() -> None:
    title, summary, body = compose_article(
        service_name="Example DApp",
        source_url="https://example.com",
        page_title="Home",
        page_text="Welcome to our dApp.",
        txid="T" * 52,
        round=99,
        diff="+new line",
        is_first_snapshot=False,
    )
    assert "Example DApp: Home" not in title
    assert "round 99" not in summary.lower() or "updated" in summary.lower()
    assert "```diff" not in body
    assert "Trigger transaction" not in body
    assert "+new line" not in body
