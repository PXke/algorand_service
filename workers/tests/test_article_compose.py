from app.modules.newspaper.article_compose import compose_article


def test_compose_article_editorial_title_not_service_colon() -> None:
    title, _summary, body = compose_article(
        service_name="Algorand Foundation",
        source_url="https://example.com",
        page_title="Example Domain",
        page_text="Example Domain This domain is for use in illustrative examples.",
        txid="A" * 52,
        round_num=1,
        diff=None,
        is_first_snapshot=True,
    )
    assert "Algorand Foundation: Example Domain" not in title
    assert "Example Domain" in title or "site update" in title
    assert "Trigger transaction" not in body
    assert "Round:" not in body
    assert "## In brief" in body


def test_compose_article_mentions_diff_without_raw_block() -> None:
    _, _, body = compose_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Home",
        page_text="content",
        txid="B" * 52,
        round_num=2,
        diff="+added line",
        is_first_snapshot=False,
    )
    assert "```diff" not in body
    assert "## What changed" in body
