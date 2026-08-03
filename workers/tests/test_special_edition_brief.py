"""Special Edition briefs: an is_special_edition flag on editorial briefs that requests a longer, multi-angle compose pass and tags the resulting article for a reader-facing badge, instead of the standard length-scaled-to-substance treatment every other brief gets."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.modules.ai.mistral_compose as mc
import app.modules.newspaper.article_composer as ac
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic


def test_special_edition_appends_depth_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_special_edition=True appends the depth-instructions block to the user prompt."""
    captured = {}

    def _fake_via_tools(**kwargs: object) -> mc.MistralArticleFields:
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_assignment_article_mistral(
        brief_title="State of Algorand DeFi",
        brief_body="Cover TVL trends, top protocols.",
        keywords="defi, tvl",
        brief_id="brief-1",
        is_special_edition=True,
        client=SimpleNamespace(),
    )
    assert "SPECIAL EDITION" in captured["user"]
    assert "investigative journalist" in captured["user"]
    assert "no target length" in captured["user"]
    assert "1,800" not in captured["user"]


def test_standard_brief_has_no_depth_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    """is_special_edition=False (the default) leaves the standard prompt untouched."""
    captured = {}

    def _fake_via_tools(**kwargs: object) -> mc.MistralArticleFields:
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_assignment_article_mistral(
        brief_title="Wallet roundup",
        brief_body="Cover download links.",
        keywords="wallet",
        brief_id="brief-2",
        client=SimpleNamespace(),
    )
    assert "SPECIAL EDITION" not in captured["user"]


def test_two_stage_compose_forwards_max_rounds_to_stage1(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_two_stage_compose passes its max_rounds straight through to stage-1's chat_with_tools call."""
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False)
    monkeypatch.setattr(mc, "_synthesize_research_digest", lambda **_kw: "digest")
    monkeypatch.setattr(
        mc, "_review_and_revise", lambda *_a, **_kw: {"title": "t", "summary": "s", "body": "b"}
    )

    captured = {}

    class _FakeResearchClient:
        def chat_with_tools(self, *_args: object, **kwargs: object) -> str:
            captured.update(kwargs)
            return ""

        def usage_totals(self) -> dict[str, int]:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    class _FakeWriteClient:
        def chat_json_object(self, *_args: object, **_kw: object) -> dict:
            return {"title": "t", "summary": "s", "body": "b"}

        def usage_totals(self) -> dict[str, int]:
            return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    mc._run_two_stage_compose(
        research_mistral=_FakeResearchClient(),
        mistral=_FakeWriteClient(),
        system="sys",
        user="usr",
        research_user=None,
        tool_schemas=[],
        tool_handlers={},
        trace=[],
        debug={},
        checkpoint=lambda _stage: None,
        max_rounds=96,
    )
    assert captured["max_rounds"] == 96


def test_compose_scrape_article_tags_special_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    """compose_scrape_article injects the special-edition tag when the brief was flagged, on top of whatever tags the model itself returned."""
    monkeypatch.setattr(ac, "mistral_configured", lambda: True)
    monkeypatch.setattr(
        ac,
        "compose_assignment_article_mistral",
        lambda **_kw: mc.MistralArticleFields(
            title="t", summary="s", body="b", tags=("defi", "tvl")
        ),
    )
    result = ac.compose_scrape_article(
        service_name="editorial",
        source_url="editorial://brief/brief-1",
        page_title="State of Algorand DeFi",
        page_text="Cover TVL trends.",
        txid="",
        round_num=0,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        publish_topic=PublishTopic.EDITORIAL_ASSIGNMENT,
        is_special_edition=True,
    )
    assert result.extra_tags == ("defi", "tvl", "special-edition")


def test_compose_scrape_article_no_tag_for_standard_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    """A standard (non-special-edition) brief's article tags are passed through unchanged."""
    monkeypatch.setattr(ac, "mistral_configured", lambda: True)
    monkeypatch.setattr(
        ac,
        "compose_assignment_article_mistral",
        lambda **_kw: mc.MistralArticleFields(title="t", summary="s", body="b", tags=("wallet",)),
    )
    result = ac.compose_scrape_article(
        service_name="editorial",
        source_url="editorial://brief/brief-2",
        page_title="Wallet roundup",
        page_text="Cover download links.",
        txid="",
        round_num=0,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        publish_topic=PublishTopic.EDITORIAL_ASSIGNMENT,
    )
    assert result.extra_tags == ("wallet",)
