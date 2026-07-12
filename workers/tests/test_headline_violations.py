from app.modules.newspaper.article_grader import grade_article_schema, headline_violations


def test_colon_label_flagged() -> None:
    for title in (
        "Nodely: The Global Backbone for Algorand's Developer Ecosystem",
        "Downbad.farm: Atomic Trading and Creator Tools for Algorand NFTs",
        "VibeKit: The Agentic Stack for Algorand Builders",
    ):
        issues = headline_violations(title)
        assert any(i.startswith("headline — colon-label") for i in issues), title


def test_claim_style_passes() -> None:
    for title in (
        "Nodely's free tier now carries 115M Algorand API calls a day",
        "HesabPay processes 30% of Afghanistan's electricity bills on Algorand",
        "Algorand Returns to U.S. with Delaware HQ and New Board",
    ):
        assert headline_violations(title) == [], title


def test_late_colon_and_times_not_flagged() -> None:
    # ": " past the 48-char label window, and no colon+space at all.
    ok = "Folks Finance doubles cross-chain volume after upgrade: what changed"
    assert not any("colon-label" in i for i in headline_violations(ok))
    assert headline_violations("ALGO holds a 3:1 buy-sell ratio through the dip") == []


def test_length_and_marketing_verbs() -> None:
    long_title = "Algorand ecosystem partners with a consortium to deliver innovative solutions across many verticals soon"
    assert any("chars" in i for i in headline_violations(long_title))
    assert any("marketing verb" in i.lower() for i in headline_violations(
        "AlgoVoi unveils multi-chain payment gateway"
    ))


def test_headline_issues_flow_into_schema_grade() -> None:
    result = grade_article_schema(
        title="Nodely: The Global Backbone",
        summary="s",
        body="## H\n\nBody text here.\n\nMore text.",
    )
    assert any(i.startswith("headline — colon-label") for i in result["issues"])
