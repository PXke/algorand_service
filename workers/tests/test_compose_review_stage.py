"""Two-stage compose Stage 3+4: deterministic grade, then one revision if weak.

The warm generation pass has no tools, so the model can't call review_draft
itself — `_review_and_revise` must run the grader for it and revise once.
"""

from app.modules.ai.mistral_compose import _parse_article_fields, _review_and_revise


class _FakeMistral:
    def __init__(self, revised: dict) -> None:
        self._revised = revised
        self.calls = 0

    def chat_json_object(self, messages, temperature=None):
        self.calls += 1
        return self._revised


def test_low_grade_triggers_one_revision(monkeypatch) -> None:
    grades = iter([
        {"grade": 5.0, "issues": ["structure — Formatting Deserts: 6 prose blocks"]},
        {"grade": 8.0, "issues": []},
    ])
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "a much longer grounded body", "summary": "s"})

    out = _review_and_revise(
        fake, {"title": "T", "body": "short"}, system="sys", gen_user="u", trace=trace
    )

    assert fake.calls == 1  # exactly one revision pass, never more
    assert out["body"] == "a much longer grounded body"
    reviews = [e for e in trace if e["tool"] == "review_draft"]
    assert len(reviews) == 2  # initial grade + recheck both recorded
    # The floor-gate downstream reads the POST-revision grade, not the stale one.
    assert out["_heuristic_grade"]["grade"] == 8.0


def test_high_grade_keeps_draft_untouched(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: {"grade": 9.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "X", "body": "Y"})
    payload = {"title": "T", "body": "good body"}

    out = _review_and_revise(fake, payload, system="s", gen_user="u", trace=trace)

    assert fake.calls == 0  # no revision when grade clears the bar with no issues
    assert out is payload
    assert len([e for e in trace if e["tool"] == "review_draft"]) == 1
    # No fixable issues -> early return, but the grade must still be attached.
    assert out["_heuristic_grade"]["grade"] == 9.0


def test_review_turns_land_in_debug_transcript(monkeypatch) -> None:
    # The Sessions view renders debug["messages"]; the deterministic review must
    # appear there as a review_draft tool call (it isn't captured by the loop).
    grades = iter([
        {"grade": 4.0, "issues": ["structure — Buried Metrics: 4 in one paragraph"]},
        {"grade": 8.0, "issues": []},
    ])
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    debug: dict = {"messages": []}
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "revised grounded body"})

    _review_and_revise(
        fake, {"title": "T", "body": "short"},
        system="s", gen_user="u", trace=trace, debug=debug,
    )

    tool_names = [
        (tc.get("function") or {}).get("name")
        for m in debug["messages"]
        for tc in (m.get("tool_calls") or [])
    ]
    assert tool_names.count("review_draft") == 2  # initial grade + recheck


class _FailingMistral:
    def chat_json_object(self, messages, temperature=None):
        raise RuntimeError("Mistral API 429 after 5 attempts")


def test_failed_revision_is_surfaced_not_silent(monkeypatch) -> None:
    # A rate-limited/failed revision must record WHY (so it isn't invisible) and
    # keep the original draft rather than crashing the compose.
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: {"grade": 4.0, "issues": ["too long (3000 words) — cut padding"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T", "body": "short draft"}

    out = _review_and_revise(
        _FailingMistral(), payload, system="s", gen_user="u", trace=trace,
    )

    assert out is payload  # original kept
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 1
    assert "429" in failures[0]["result"]["error"]
    # A failed revision must not lose the grade the floor-gate depends on.
    assert out["_heuristic_grade"]["grade"] == 4.0


def test_parse_article_fields_threads_heuristic_grade() -> None:
    # publish_tasks._quality_floor_fails reads composed.heuristic_grade — the
    # grade must survive from the payload dict onto the dataclass.
    payload = {
        "title": "T", "summary": "S", "body": "B",
        "_heuristic_grade": {"grade": 5.5, "issues": ["stale"]},
    }
    fields = _parse_article_fields(payload)
    assert fields.heuristic_grade == {"grade": 5.5, "issues": ["stale"]}


def test_parse_article_fields_grade_defaults_none() -> None:
    fields = _parse_article_fields({"title": "T", "summary": "S", "body": "B"})
    assert fields.heuristic_grade is None


def test_low_quality_llm_triggers_revision(monkeypatch) -> None:
    # Quality mock never improves — with WRITER_REVISION_MAX_PASSES=2 (default)
    # this should genuinely attempt a SECOND revision instead of giving up
    # after one, then stop once the revision budget is spent.
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: {"grade": 10.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {
            "narrative_synthesis": 2,
            "technical_depth": 2,
            "issues": ["technical depth scored 2/5 — explain layer-1 mechanics"],
        },
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "deeper revised body with more detail"})

    out = _review_and_revise(
        fake, {"title": "T", "body": "short generic press release body"},
        system="sys", gen_user="u", trace=trace,
    )

    assert fake.calls == 2  # spent both revision passes since quality never clears
    assert out["body"] == "deeper revised body with more detail"
    reviews = [e for e in trace if e["tool"] == "review_draft"]
    assert len(reviews) == 3  # initial grade + 2 rechecks


def test_revision_stops_once_max_passes_reached(monkeypatch) -> None:
    # Bound it to 1 revision explicitly and confirm the loop respects it even
    # though the mock quality never improves — no unbounded looping.
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "WRITER_REVISION_MAX_PASSES", 1)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: {"grade": 10.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {
            "narrative_synthesis": 2,
            "technical_depth": 2,
            "issues": ["technical depth scored 2/5 — explain layer-1 mechanics"],
        },
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "deeper revised body with more detail"})

    _review_and_revise(
        fake, {"title": "T", "body": "short generic press release body"},
        system="sys", gen_user="u", trace=trace,
    )

    assert fake.calls == 1


def test_second_revision_only_fires_when_first_still_fixable(monkeypatch) -> None:
    # First revision clears the bar -> the loop must NOT spend a second
    # revision call it doesn't need, even though up to 2 are allowed.
    grades = iter([
        {"grade": 5.0, "issues": ["structure — Formatting Deserts: 6 prose blocks"]},
        {"grade": 8.0, "issues": []},
    ])
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "a much longer grounded body"})

    out = _review_and_revise(
        fake, {"title": "T", "body": "short"}, system="sys", gen_user="u", trace=trace
    )

    assert fake.calls == 1
    assert out["_heuristic_grade"]["grade"] == 8.0


class _SequenceMistral:
    """Returns a different revised draft on each successive call, and records
    the exact revise_user text sent each time (needed to check carry-forward
    memory reaches the prompt, not just the return value)."""

    def __init__(self, revisions: list[dict]) -> None:
        self._revisions = list(revisions)
        self.calls = 0
        self.sent_users: list[str] = []

    def chat_json_object(self, messages, temperature=None):
        self.sent_users.append(messages[-1]["content"])
        out = self._revisions[self.calls]
        self.calls += 1
        return out


def test_best_of_n_returns_highest_scoring_pass_not_last(monkeypatch) -> None:
    """Regression-pin the real 2026-07-14 CompX incident: pass 2 (grade 8.6)
    fixed a headline issue but not yet its own new one; pass 3 (grade 7.3,
    the LAST pass) fixed that but re-broke structure pass 2 had already
    cleaned up. The loop must not just return whatever pass happened to run
    last — it must return the best-scoring draft it ever produced."""
    grades = iter([
        {"grade": 5.0, "issues": ["structure — Buried Metrics: 5 metrics in one paragraph"]},
        {"grade": 8.6, "issues": ["headline — colon-label title"]},
        {"grade": 7.3, "issues": []},  # clean, but scores lower than pass 2
    ])
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _SequenceMistral([
        {"title": "T2", "body": "pass two body — the best draft"},
        {"title": "T3", "body": "pass three body — regressed but graded last"},
    ])

    out = _review_and_revise(
        fake, {"title": "T1", "body": "pass one body"},
        system="sys", gen_user="u", trace=trace,
    )

    assert fake.calls == 2
    assert out["body"] == "pass two body — the best draft"
    assert out["_heuristic_grade"]["grade"] == 8.6


def test_carry_forward_tells_revision_not_to_undo_earlier_fix(monkeypatch) -> None:
    """An issue resolved in pass 1 (dropped from pass 2's issue list) must be
    named explicitly in pass 2's revision prompt as 'already fixed — do not
    reintroduce', so the model doesn't trade it away while fixing the new
    issue pass 2 raised."""
    grades = iter([
        {"grade": 5.0, "issues": ["structure — Buried Metrics: 5 metrics in one paragraph"]},
        {"grade": 6.0, "issues": ["headline — colon-label title"]},
        {"grade": 8.0, "issues": []},
    ])
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _SequenceMistral([
        {"title": "T2", "body": "pass two body"},
        {"title": "T3", "body": "pass three body"},
    ])

    _review_and_revise(
        fake, {"title": "T1", "body": "pass one body"},
        system="sys", gen_user="u", trace=trace,
    )

    assert fake.calls == 2
    # First revision prompt has nothing to carry forward yet.
    assert "already fixed" not in fake.sent_users[0]
    # Second revision prompt must name the structure issue pass 1 already
    # fixed, since pass 2's own issue list no longer includes it.
    assert "already fixed" in fake.sent_users[1]
    assert "Buried Metrics" in fake.sent_users[1]


def test_quality_rubric_uses_research_client_not_writer_client(monkeypatch) -> None:
    """LLM rubric grading is a judgment task, not generation — it must run on
    the research (Small) client, not the Large writer client passed in for
    Stage 2 generation/revision. grade_article_quality_llm's own docstring
    calls itself a 'Fast Small-tier rubric', but that only ever applied to
    its unused default — the actual call site was silently passing the
    writer's Large client until this was fixed 2026-07-15."""
    import app.modules.ai.mistral_compose as mc

    seen_clients: list[object] = []

    def _fake_grade_quality(*, title, body, client=None):
        seen_clients.append(client)
        return {"narrative_synthesis": 4, "technical_depth": 4, "issues": []}

    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: {"grade": 9.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        _fake_grade_quality,
    )
    research_client = object()
    monkeypatch.setattr(mc, "get_mistral_research_client", lambda: research_client)

    writer_client = _FakeMistral({"title": "X", "body": "Y"})
    _review_and_revise(
        writer_client, {"title": "T", "body": "good body"}, system="s", gen_user="u", trace=[]
    )

    assert seen_clients == [research_client]
    assert seen_clients[0] is not writer_client


def test_disabled_skips_review(monkeypatch) -> None:
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "WRITER_REVIEW_ENABLED", False)
    trace: list[dict] = []
    fake = _FakeMistral({"title": "X", "body": "Y"})
    payload = {"title": "T", "body": "b"}

    out = _review_and_revise(fake, payload, system="s", gen_user="u", trace=trace)

    assert out is payload
    assert fake.calls == 0
    assert trace == []
