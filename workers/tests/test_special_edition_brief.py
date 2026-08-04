"""Special Edition briefs: an is_special_edition flag on editorial briefs that requests a longer, multi-angle compose pass and tags the resulting article for a reader-facing badge, instead of the standard length-scaled-to-substance treatment every other brief gets."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

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


def test_two_stage_compose_forwards_is_special_edition_to_review_and_revise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_two_stage_compose passes is_special_edition through to _review_and_revise, so the grader knows to skip the length constraint (root-caused 2026-08-04: the grader's "too long ... cut padding/filler" issue directly contradicted the special-edition prompt's "never cut a real finding short")."""
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False)
    # This test predates the entity-enumeration/outline deepening pass and is
    # only about is_special_edition reaching _review_and_revise -- without
    # this, is_special_edition=True below now also triggers the real
    # deepening pipeline, which calls the real (network-blocked) digest
    # client instead of a fake one.
    monkeypatch.setattr("app.core.config.SPECIAL_EDITION_OUTLINE_ENABLED", False, raising=False)
    monkeypatch.setattr(mc, "_synthesize_research_digest", lambda **_kw: "digest")

    captured = {}

    def _fake_review_and_revise(*_args: object, **kwargs: object) -> dict:
        captured.update(kwargs)
        return {"title": "t", "summary": "s", "body": "b"}

    monkeypatch.setattr(mc, "_review_and_revise", _fake_review_and_revise)

    class _FakeResearchClient:
        def chat_with_tools(self, *_args: object, **_kwargs: object) -> str:
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
        is_special_edition=True,
    )
    assert captured["is_special_edition"] is True


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


def test_run_entity_enumeration_empty_trace_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """No formatted trace means nothing to enumerate from -- returns "" without calling the digest client at all."""
    calls = []
    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: calls.append("called"))
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "")

    result = mc._run_entity_enumeration(trace=[], digest="")

    assert result == ""
    assert calls == []


def test_run_entity_enumeration_returns_synthesized_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful call returns the digest client's stripped output."""
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    class _FakeDigestClient:
        def chat_completion(self, *_a: object, **_kw: object) -> str:
            return "  ## Entity Enumeration\n\n### People\n- Jane Doe  \n"

    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: _FakeDigestClient())

    result = mc._run_entity_enumeration(trace=[{"tool": "fetch_url"}], digest="digest text")

    assert result == "## Entity Enumeration\n\n### People\n- Jane Doe"


def test_run_entity_enumeration_swallows_client_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A digest-client exception degrades to "" rather than blocking the compose."""
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    class _BrokenDigestClient:
        def chat_completion(self, *_a: object, **_kw: object) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: _BrokenDigestClient())

    result = mc._run_entity_enumeration(trace=[{"tool": "fetch_url"}], digest="digest text")

    assert result == ""


def test_run_narrative_outline_returns_empty_when_nothing_to_plan_from() -> None:
    """Empty digest AND empty enumeration means there is nothing to outline -- returns "" without touching the client."""
    assert mc._run_narrative_outline(digest="", enumeration="") == ""


def test_run_narrative_outline_returns_synthesized_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful call returns the digest client's stripped output."""

    class _FakeDigestClient:
        def chat_completion(self, *_a: object, **_kw: object) -> str:
            return "  ## Narrative Outline\n\n### Throughline\n- the piece is about X  \n"

    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: _FakeDigestClient())

    result = mc._run_narrative_outline(digest="digest text", enumeration="## Entity Enumeration")

    assert result == "## Narrative Outline\n\n### Throughline\n- the piece is about X"


def test_run_narrative_outline_swallows_client_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A digest-client exception degrades to "" rather than blocking the compose."""

    class _BrokenDigestClient:
        def chat_completion(self, *_a: object, **_kw: object) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: _BrokenDigestClient())

    result = mc._run_narrative_outline(digest="digest text", enumeration="")

    assert result == ""


def test_run_enumeration_gap_fill_calls_chat_with_tools_with_the_nudge_and_round_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enumeration-driven gap-fill pass sends the gaps nudge as the user turn and caps rounds at SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS."""
    monkeypatch.setattr(
        "app.core.config.SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS", 5, raising=False
    )
    client = MagicMock()

    mc._run_enumeration_gap_fill(
        client,
        "sys",
        "stage1 user",
        [],
        {},
        [],
        {},
        "- the launch date is unconfirmed",
    )

    client.chat_with_tools.assert_called_once()
    _args, kwargs = client.chat_with_tools.call_args
    messages = _args[0]
    assert "the launch date is unconfirmed" in messages[1]["content"]
    assert kwargs["max_rounds"] == 5
    assert kwargs["require_tool"] is None
    assert kwargs["finalize_on_exhaustion"] is False


def test_special_edition_deepening_returns_digest_unchanged_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SPECIAL_EDITION_OUTLINE_ENABLED=False skips the whole deepening pass and returns the original digest with empty enumeration/outline."""
    monkeypatch.setattr("app.core.config.SPECIAL_EDITION_OUTLINE_ENABLED", False, raising=False)

    digest, enumeration, outline = mc._run_special_edition_deepening(
        MagicMock(), "sys", "user", [], {}, [], {}, "original digest"
    )

    assert (digest, enumeration, outline) == ("original digest", "", "")


def test_special_edition_deepening_skips_gap_fill_when_no_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No Coverage Gaps in the enumeration means no second research pass and no digest re-synthesis -- the outline still runs against the original digest."""
    monkeypatch.setattr("app.core.config.SPECIAL_EDITION_OUTLINE_ENABLED", True, raising=False)
    monkeypatch.setattr(
        mc, "_run_entity_enumeration", lambda **_kw: "## Entity Enumeration\n\n### Coverage Gaps\n- None"
    )
    gap_fill_calls = []
    monkeypatch.setattr(
        mc, "_run_enumeration_gap_fill", lambda *_a, **_kw: gap_fill_calls.append(1)
    )
    resynth_calls = []
    monkeypatch.setattr(
        mc, "_synthesize_research_digest", lambda **_kw: resynth_calls.append(1) or "new digest"
    )
    monkeypatch.setattr(mc, "_run_narrative_outline", lambda **_kw: "## Narrative Outline")

    digest, enumeration, outline = mc._run_special_edition_deepening(
        MagicMock(), "sys", "user", [], {}, [], {}, "original digest"
    )

    assert gap_fill_calls == []
    assert resynth_calls == []
    assert digest == "original digest"
    assert "Coverage Gaps" in enumeration
    assert outline == "## Narrative Outline"


def test_special_edition_deepening_runs_gap_fill_then_resynthesizes_then_outlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the enumeration flags real gaps: gap-fill runs, the digest is re-synthesized off the (now richer) trace, and the outline is built from the NEW digest, in that order."""
    monkeypatch.setattr("app.core.config.SPECIAL_EDITION_OUTLINE_ENABLED", True, raising=False)
    call_order = []
    monkeypatch.setattr(
        mc,
        "_run_entity_enumeration",
        lambda **_kw: "## Entity Enumeration\n\n### Coverage Gaps\n- the launch date is unconfirmed",
    )
    monkeypatch.setattr(
        mc, "_run_enumeration_gap_fill", lambda *_a, **_kw: call_order.append("gap_fill")
    )

    def _fake_resynth(**_kw: object) -> str:
        call_order.append("resynth")
        return "new digest"

    monkeypatch.setattr(mc, "_synthesize_research_digest", _fake_resynth)

    def _fake_outline(**kwargs: object) -> str:
        call_order.append("outline")
        assert kwargs["digest"] == "new digest"
        return "## Narrative Outline"

    monkeypatch.setattr(mc, "_run_narrative_outline", _fake_outline)

    digest, _enumeration, outline = mc._run_special_edition_deepening(
        MagicMock(), "sys", "user", [], {}, [], {}, "original digest"
    )

    assert call_order == ["gap_fill", "resynth", "outline"]
    assert digest == "new digest"
    assert outline == "## Narrative Outline"


def test_two_stage_compose_runs_deepening_only_for_special_editions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_two_stage_compose calls _run_special_edition_deepening when is_special_edition=True, and forwards its (digest, enumeration, outline) into the Stage-2 prompt via _build_stage2_user."""
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False)
    monkeypatch.setattr(mc, "_synthesize_research_digest", lambda **_kw: "digest")
    monkeypatch.setattr(
        mc, "_review_and_revise", lambda *_a, **_kw: {"title": "t", "summary": "s", "body": "b"}
    )
    deepening_calls = []

    def _fake_deepening(*_a: object, **_kw: object) -> tuple[str, str, str]:
        deepening_calls.append(1)
        return "deepened digest", "## Entity Enumeration", "## Narrative Outline"

    monkeypatch.setattr(mc, "_run_special_edition_deepening", _fake_deepening)

    captured_stage2_user = {}
    original_build_stage2_user = mc._build_stage2_user

    def _spy_build_stage2_user(**kwargs: object) -> str:
        captured_stage2_user.update(kwargs)
        return original_build_stage2_user(**kwargs)

    monkeypatch.setattr(mc, "_build_stage2_user", _spy_build_stage2_user)

    class _FakeResearchClient:
        def chat_with_tools(self, *_args: object, **_kwargs: object) -> str:
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
        is_special_edition=True,
    )

    assert deepening_calls == [1]
    assert captured_stage2_user["digest"] == "deepened digest"
    assert captured_stage2_user["enumeration"] == "## Entity Enumeration"
    assert captured_stage2_user["outline"] == "## Narrative Outline"


def test_two_stage_compose_skips_deepening_for_standard_articles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-special-edition compose never calls _run_special_edition_deepening -- the deepening pass is exclusively a special-edition cost."""
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False)
    monkeypatch.setattr(mc, "_synthesize_research_digest", lambda **_kw: "digest")
    monkeypatch.setattr(
        mc, "_review_and_revise", lambda *_a, **_kw: {"title": "t", "summary": "s", "body": "b"}
    )
    deepening_calls = []
    monkeypatch.setattr(
        mc, "_run_special_edition_deepening", lambda *_a, **_kw: deepening_calls.append(1)
    )

    class _FakeResearchClient:
        def chat_with_tools(self, *_args: object, **_kwargs: object) -> str:
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
    )

    assert deepening_calls == []


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
