from pathlib import Path

from app.modules.newspaper.article_edit_compose import compose_article_edit_template
from app.modules.newspaper.article_store import ArticleDetail

D13 = (Path(__file__).parent / "fixtures" / "algoblow_d13_alert.txt").read_text(encoding="utf-8")


def test_edit_template_appends_updated_section():
    existing = ArticleDetail(
        article_id="a1",
        service_id="scam",
        title="Breaking: algoblow scam",
        summary="Initial warning",
        body="## In brief\n\nDo not use algoblow.com.",
        published_at_epoch=1_700_000_000,
        trigger_txid="",
        trigger_round=0,
        source_url="push://1",
    )
    title, summary, body = compose_article_edit_template(
        existing=existing,
        new_page_text=D13,
        new_page_title="Victim accounts reported",
        diff="+ A43BSFDDZGPEVB2XUUX652OOHNHRA3OZVP4FNM7MF5TDOCUFZWGLS7MR6A",
    )
    assert title == existing.title
    assert "updated" in summary.lower()
    assert "## Updated" in body
    assert "A43BSFDDZGPEVB2XUUX652OOHNHRA3OZVP4FNM7MF5TDOCUFZWGLS7MR6A" in body
