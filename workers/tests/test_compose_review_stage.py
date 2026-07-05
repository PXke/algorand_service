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
        {"grade": 5.0, "issues": ["structure — Formatting Deserts: 6 prose blocks"]},  # initial
        {"grade": 8.0, "issues": []},             # post-revision recheck
    ])
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: next(grades),
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
