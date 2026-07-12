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


def test_stale_compose_loop_ignores_unknown_research_user_kwarg(monkeypatch):
    """Mid-deploy workers may lack ``research_user`` on the compose loop."""

    def _legacy_loop(*, system, user, source_url, mistral, topic=""):
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _legacy_loop)
    monkeypatch.setattr(
        "app.core.config.MISTRAL_RESEARCH_SOURCE_CHARS", 1000, raising=False
    )

    fields = mc.compose_scrape_article_mistral(
        service_name="svc",
        source_url="https://example.com/page",
        page_title="Page",
        page_text="Algorand update paragraph. " * 400,
        txid="tx",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        client=SimpleNamespace(),
    )
    assert fields.title == "t"


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


def test_first_coverage_forces_introduction_framing(monkeypatch):
    """A diff-driven update on a never-published service must compose as an
    introduction (FIRST COVERAGE MODE), not an evolution/what-changed story."""
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_scrape_article_mistral(
        service_name="Blockshake",
        source_url="http://blockshake.io/",
        page_title="blockshake.io",
        page_text="Algorand tooling company page " * 30,
        txid="tx",
        round_num=1,
        diff="+++ a\n+ x\n+ y\n+ z\n",
        is_first_snapshot=False,
        first_coverage=True,
        client=SimpleNamespace(),
    )
    assert "FIRST COVERAGE MODE" in captured["system"]
    # Evolution framing must be suppressed even though a diff exists.
    assert "WHAT CHANGED since we last looked" not in captured["user"]


def test_known_service_keeps_evolution_framing(monkeypatch):
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_scrape_article_mistral(
        service_name="Tinyman",
        source_url="https://tinyman.org/",
        page_title="tinyman",
        page_text="Algorand AMM " * 30,
        txid="tx",
        round_num=1,
        diff="+++ a\n+ x\n+ y\n+ z\n",
        is_first_snapshot=False,
        first_coverage=False,
        client=SimpleNamespace(),
    )
    assert "FIRST COVERAGE MODE" not in captured["system"]
    assert "WHAT CHANGED since we last looked" in captured["user"]


def test_model_tier_split_large_writes_small_does_mechanics():
    """Owner policy (2026-07-12): Large writes reader-facing prose (writer,
    digest); Small does the mechanical work (tool-loop research, translate)."""
    from app.core.config import (
        MISTRAL_MODEL_DIGEST,
        MISTRAL_MODEL_RESEARCH,
        MISTRAL_MODEL_TRANSLATE,
        MISTRAL_MODEL_WRITER,
    )
    from app.modules.ai.mistral_client import (
        get_mistral_digest_client,
        get_mistral_research_client,
    )

    assert "small" in MISTRAL_MODEL_RESEARCH
    assert "small" in MISTRAL_MODEL_TRANSLATE
    assert "large" in MISTRAL_MODEL_WRITER
    assert "large" in MISTRAL_MODEL_DIGEST
    assert get_mistral_research_client()._model == MISTRAL_MODEL_RESEARCH
    assert get_mistral_digest_client()._model == MISTRAL_MODEL_DIGEST


def test_two_stage_compose_routes_research_to_small_tier(monkeypatch):
    """Stage-1 tool loop + digest synthesis use the research client; generation
    stays on the writer (Large) client."""
    calls: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, tier: str, model: str):
            self._tier = tier
            self._model = model

        def chat_with_tools(self, *_a, **_kw):
            calls.append(("tools", self._tier))
            return '{"title":"t","summary":"s","body":"b","tags":["algo"]}'

        def chat_json_object(self, *_a, **_kw):
            calls.append(("json", self._tier))
            return {"title": "t", "summary": "s", "body": "b", "tags": ["algo"]}

        def chat_completion(self, *_a, **_kw):
            calls.append(("completion", self._tier))
            return "## Research Digest\n\n### Verified Facts\n- fact [src](https://x)"

    writer = _FakeClient("writer", "mistral-medium-latest")
    research = _FakeClient("research", "mistral-small-latest")

    monkeypatch.setattr(mc, "get_mistral_research_client", lambda: research)
    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: research)
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_TWO_STAGE", True, raising=False)
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", False, raising=False)
    monkeypatch.setattr(
        "app.modules.ai.writer_tools.all_tools",
        lambda **_kw: ([], {}),
    )
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.new_session_ref",
        lambda: ("sid", 0.0),
    )
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_compose_session",
        lambda **_kw: None,
    )
    # Digest synthesis only runs when the formatted trace is non-empty.
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    mc._compose_via_writer_tools(
        system="sys",
        user="user prompt",
        source_url="https://example.com/",
        mistral=writer,
    )

    assert ("tools", "research") in calls
    assert ("completion", "research") in calls
    assert ("json", "writer") in calls
    assert ("tools", "writer") not in calls


def test_digest_gap_triggers_one_bounded_research_pass(monkeypatch):
    """When digest synthesis flags an Unresolved Gap, one extra bounded
    research pass runs (capped via DIGEST_GAP_FILL_MAX_ROUNDS) before the
    digest is re-synthesized and handed to the writer — the fix for the
    nf.domains incident, where the writer invented sales data instead of the
    model getting a real second chance to look for it."""
    calls: list[tuple[str, str, dict]] = []
    digest_calls = {"n": 0}

    class _FakeClient:
        def __init__(self, tier: str, model: str):
            self._tier = tier
            self._model = model

        def chat_with_tools(self, *_a, **kw):
            calls.append(("tools", self._tier, kw))
            return '{"title":"t","summary":"s","body":"b","tags":["algo"]}'

        def chat_json_object(self, *_a, **_kw):
            calls.append(("json", self._tier, {}))
            return {"title": "t", "summary": "s", "body": "b", "tags": ["algo"]}

        def chat_completion(self, *_a, **_kw):
            digest_calls["n"] += 1
            calls.append(("completion", self._tier, {}))
            if digest_calls["n"] == 1:
                return (
                    "## Research Digest\n\n### Verified Facts\n- fact [src](https://x)\n\n"
                    "### Unresolved Gaps\n- no real recent sale price found; try the "
                    "marketplace's sales-history page\n"
                )
            return "## Research Digest\n\n### Verified Facts\n- fact [src](https://x)\n"

    writer = _FakeClient("writer", "mistral-medium-latest")
    research = _FakeClient("research", "mistral-small-latest")

    monkeypatch.setattr(mc, "get_mistral_research_client", lambda: research)
    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: research)
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_TWO_STAGE", True, raising=False)
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_MAX_ROUNDS", 3, raising=False)
    monkeypatch.setattr(
        "app.modules.ai.writer_tools.all_tools",
        lambda **_kw: ([], {}),
    )
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.new_session_ref",
        lambda: ("sid", 0.0),
    )
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_compose_session",
        lambda **_kw: None,
    )
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    mc._compose_via_writer_tools(
        system="sys",
        user="user prompt",
        source_url="https://example.com/",
        mistral=writer,
    )

    tool_calls = [c for c in calls if c[0] == "tools"]
    completion_calls = [c for c in calls if c[0] == "completion"]
    # Initial research round + one gap-fill round.
    assert len(tool_calls) == 2
    assert len(completion_calls) == 2  # initial digest + re-synthesis after gap-fill
    gap_fill_kwargs = tool_calls[1][2]
    assert gap_fill_kwargs.get("max_rounds") == 3


def test_digest_with_no_gaps_skips_extra_research_pass(monkeypatch):
    calls: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, tier: str):
            self._tier = tier

        def chat_with_tools(self, *_a, **_kw):
            calls.append(("tools", self._tier))
            return '{"title":"t","summary":"s","body":"b","tags":["algo"]}'

        def chat_json_object(self, *_a, **_kw):
            calls.append(("json", self._tier))
            return {"title": "t", "summary": "s", "body": "b", "tags": ["algo"]}

        def chat_completion(self, *_a, **_kw):
            calls.append(("completion", self._tier))
            return "## Research Digest\n\n### Verified Facts\n- fact\n\n### Unresolved Gaps\n- None\n"

    writer = _FakeClient("writer")
    research = _FakeClient("research")

    monkeypatch.setattr(mc, "get_mistral_research_client", lambda: research)
    monkeypatch.setattr(mc, "get_mistral_digest_client", lambda: research)
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_TWO_STAGE", True, raising=False)
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", True, raising=False)
    monkeypatch.setattr(
        "app.modules.ai.writer_tools.all_tools",
        lambda **_kw: ([], {}),
    )
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.new_session_ref",
        lambda: ("sid", 0.0),
    )
    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_compose_session",
        lambda **_kw: None,
    )
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    mc._compose_via_writer_tools(
        system="sys",
        user="user prompt",
        source_url="https://example.com/",
        mistral=writer,
    )

    assert len([c for c in calls if c[0] == "tools"]) == 1
    assert len([c for c in calls if c[0] == "completion"]) == 1
