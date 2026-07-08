"""Cost controls on the Mistral lanes: translations run on the Small tier, and
stage-1 research rounds get a slimmer source clip than the single stage-2
generation call (the research prompt is re-sent on every tool round)."""

from types import SimpleNamespace

from app.modules.ai import mistral_compose as mc


def test_translations_use_small_tier(monkeypatch):
    captured = {}

    def _fake_get_client(*, model=None):
        captured["model"] = model
        return SimpleNamespace(
            chat_json_object=lambda *_a, **_kw: {
                "title": "t",
                "summary": "s",
                "body": "b",
            }
        )

    monkeypatch.setattr(mc, "get_mistral_client", _fake_get_client)
    monkeypatch.setattr(
        "app.core.config.MISTRAL_MODEL_TRANSLATE", "mistral-small-latest", raising=False
    )
    out = mc.translate_article_mistral(
        english_title="Title",
        english_summary="Summary",
        english_body="Body",
        target_language="fr",
    )
    assert captured["model"] == "mistral-small-latest"
    assert out["title"] == "t"


def test_research_rounds_get_slimmer_source_than_generation(monkeypatch):
    """The scrape compose passes a research_user with a smaller source clip
    into the shared writer loop; the full user (48k clip) is reserved for the
    single stage-2 generation call."""
    monkeypatch.setattr(
        "app.core.config.MISTRAL_RESEARCH_SOURCE_CHARS", 1000, raising=False
    )
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    big_source = "Algorand update paragraph. " * 400  # ~10k chars

    mc.compose_scrape_article_mistral(
        service_name="svc",
        source_url="https://example.com/page",
        page_title="Page",
        page_text=big_source,
        txid="tx",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        client=SimpleNamespace(),
    )
    assert "research_user" in captured
    assert len(captured["research_user"]) < len(captured["user"])
    # Full source rides in the generation prompt.
    assert big_source[:500] in captured["user"]


def test_small_source_reuses_full_prompt_for_research(monkeypatch):
    """No pointless second prompt when the source already fits the research
    clip — research_user must be the SAME object as user."""
    monkeypatch.setattr(
        "app.core.config.MISTRAL_RESEARCH_SOURCE_CHARS", 16_000, raising=False
    )
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_scrape_article_mistral(
        service_name="svc",
        source_url="https://example.com/page",
        page_title="Page",
        page_text="short source",
        txid="tx",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        client=SimpleNamespace(),
    )
    assert captured["research_user"] is captured["user"]
