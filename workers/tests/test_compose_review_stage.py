"""Two-stage compose Stage 3+4: deterministic grade, then one revision if weak.

The warm generation pass has no tools, so the model can't call review_draft
itself — `_review_and_revise` must run the grader for it and revise once.
"""

from typing import Any, Never

import pytest

from app.modules.ai.llm_compose import (
    _parse_article_fields,
    _review_and_revise,
    _revision_length_rule,
)


class TestRevisionLengthRule:
    """Root-caused 2026-08-04 (Humanitarian Network recompose #2): the needs_depth revision branch had no length floor and told the model to 'CUT the later restatements,' which a real revision pass used to rewrite a 2,471-word draft down to 1,044 then 1,020 words. Owner directive: remove length limitation/targeting from revision entirely (not just for special editions) -- no numeric word-count target anywhere except the too_long case, which legitimately asks for trimming."""

    def test_needs_depth_has_no_shrink_language_and_no_numeric_floor(self) -> None:
        """The 'improve depth' branch must forbid net shrinkage without a numeric floor -- this is the exact branch that fired during the incident."""
        rule = _revision_length_rule(too_long=False, needs_depth=True)
        assert "NO length limit and NO target word count" in rule
        assert "do not shorten" in rule.lower()
        assert "MUST stay above" not in rule
        assert "%" not in rule

    def test_reorganize_only_has_no_shrink_language_and_no_numeric_floor(self) -> None:
        """The reorganize-only branch (not too_long, not needs_depth) used to enforce an 80%-of-draft-words floor with a threat ('or it will be rejected') that nothing downstream actually checked anymore (the real guard was removed 2026-08-03) -- replaced with the same no-target-word-count rule."""
        rule = _revision_length_rule(too_long=False, needs_depth=False)
        assert "NO length limit and NO target word count" in rule
        assert "REORGANIZE" in rule
        assert "MUST stay above" not in rule
        assert "will be rejected" not in rule
        assert "%" not in rule

    def test_too_long_still_asks_for_trimming(self) -> None:
        """too_long is the one legitimate case for shrinking -- e.g. a grader-flagged 'too long' issue for a non-special-edition article -- and must still ask for it."""
        rule = _revision_length_rule(too_long=True, needs_depth=False)
        assert "Trim padding/filler" in rule
        assert "NO length limit" not in rule


class _FakeMistral:
    def __init__(self, revised: dict) -> None:
        self._revised = revised
        self.calls = 0

    def chat_json_object(self, _messages: list[dict], temperature: float | None = None) -> dict:  # noqa: ARG002 -- name must match the real callee's keyword arg
        self.calls += 1
        return self._revised


def test_low_grade_triggers_one_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revises once when the initial grade is low, and returns the post-revision grade, not the stale one."""
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — Formatting Deserts: 6 prose blocks"]},
            {"grade": 8.0, "issues": []},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
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


def test_high_grade_keeps_draft_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns the original draft unchanged, with no revision call, when the grade clears the bar with no issues."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 9.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
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


def test_review_turns_land_in_debug_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """Records the deterministic review as review_draft tool calls in the debug transcript the Sessions view renders."""
    # The Sessions view renders debug["messages"]; the deterministic review must
    # appear there as a review_draft tool call (it isn't captured by the loop).
    grades = iter(
        [
            {"grade": 4.0, "issues": ["structure — Buried Metrics: 4 in one paragraph"]},
            {"grade": 8.0, "issues": []},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    debug: dict = {"messages": []}
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "revised grounded body"})

    _review_and_revise(
        fake,
        {"title": "T", "body": "short"},
        system="s",
        gen_user="u",
        trace=trace,
        debug=debug,
    )

    tool_names = [
        (tc.get("function") or {}).get("name")
        for m in debug["messages"]
        for tc in (m.get("tool_calls") or [])
    ]
    assert tool_names.count("review_draft") == 2  # initial grade + recheck


def test_review_turns_have_matching_tool_call_id_pairing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-caused 2026-08-15: the synthetic review_draft debug turn built its assistant tool_calls entry with no id and its paired tool-role message with no tool_call_id at all -- neither matched the other, and neither got backfilled by the later merge-point fixup (which only ever touched the assistant side). Once this synthetic pair is later merged into a revision-pass request and replayed through a stricter provider, the mismatch surfaces as "messages with role 'tool' must have a 'tool_call_id'" (confirmed live, GPT-5.6-luna). Every debug-transcript review_draft turn must carry a real, matching id on both sides from the moment it's created."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 8.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    debug: dict = {"messages": []}
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "revised grounded body"})

    _review_and_revise(
        fake,
        {"title": "T", "body": "short"},
        system="s",
        gen_user="u",
        trace=trace,
        debug=debug,
    )

    for i, m in enumerate(debug["messages"]):
        tcs = m.get("tool_calls")
        if not tcs:
            continue
        call_id = tcs[0].get("id")
        assert call_id, f"message {i}: synthetic tool_calls entry missing id"
        assert tcs[0].get("type") == "function"
        tool_msg = debug["messages"][i + 1]
        assert tool_msg["role"] == "tool"
        assert tool_msg.get("tool_call_id") == call_id, (
            f"message {i + 1}: tool_call_id does not match paired assistant tool_calls[0].id"
        )


class _FailingMistral:
    def chat_json_object(self, _messages: list[dict], temperature: float | None = None) -> Never:  # noqa: ARG002 -- name must match the real callee's keyword arg
        raise RuntimeError("Mistral API 429 after 5 attempts")


def test_failed_revision_is_surfaced_not_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the original draft and records the failure reason in the trace when a revision call errors out."""
    # A rate-limited/failed revision must record WHY (so it isn't invisible) and
    # keep the original draft rather than crashing the compose.
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["too long (3000 words) — cut padding"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T", "body": "short draft"}

    out = _review_and_revise(
        _FailingMistral(),
        payload,
        system="s",
        gen_user="u",
        trace=trace,
    )

    assert out is payload  # original kept
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 1
    assert "429" in failures[0]["result"]["error"]
    # A failed revision must not lose the grade the floor-gate depends on.
    assert out["_heuristic_grade"]["grade"] == 4.0


def test_parse_article_fields_threads_heuristic_grade() -> None:
    """Carries the heuristic grade from the payload dict onto the parsed ArticleComposeResult dataclass."""
    # publish_tasks._quality_floor_fails reads composed.heuristic_grade — the
    # grade must survive from the payload dict onto the dataclass.
    payload = {
        "title": "T",
        "summary": "S",
        "body": "B",
        "_heuristic_grade": {"grade": 5.5, "issues": ["stale"]},
    }
    fields = _parse_article_fields(payload)
    assert fields.heuristic_grade == {"grade": 5.5, "issues": ["stale"]}


def test_parse_article_fields_grade_defaults_none() -> None:
    """Defaults heuristic_grade to None when the payload carries no grade."""
    fields = _parse_article_fields({"title": "T", "summary": "S", "body": "B"})
    assert fields.heuristic_grade is None


def test_low_repetition_score_triggers_revision_with_cut_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repetition-only failure (all other rubric dimensions fine) must still trigger a revision pass, and that pass's prompt must explicitly tell the model to CUT restated points rather than just vaguely 'improve' the draft — root-caused 2026-07-15 on a real NFT-marketplace article that restated 'fees are undisclosed' five times across sections."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 10.0, "issues": []},
    )
    quality_results = iter(
        [
            {
                "narrative_synthesis": 5,
                "technical_depth": 5,
                "critical_distance": 5,
                "repetition": 2,
                "issues": [
                    "repetition scored 2/5 — a specific fact is restated in more than one section"
                ],
            },
            {
                "narrative_synthesis": 5,
                "technical_depth": 5,
                "critical_distance": 5,
                "repetition": 5,
                "issues": [],
            },
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: next(quality_results),
    )
    seq = _SequenceMistral(
        [{"title": "T2", "body": "the fact stated once and a tightened rest of the section"}]
    )

    out = _review_and_revise(
        seq,
        {"title": "T", "body": "the same fact restated in every section of the draft"},
        system="sys",
        gen_user="u",
        trace=[],
    )

    assert seq.calls == 1
    assert "Apply the NO REPETITION rule" in seq.sent_users[0]
    assert out["body"] == "the fact stated once and a tightened rest of the section"


def test_low_quality_llm_triggers_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spends both available revision passes and returns the last draft when quality-LLM score never clears the bar."""
    # Quality mock never improves — with WRITER_REVISION_MAX_PASSES=2 (default)
    # this should genuinely attempt a SECOND revision instead of giving up
    # after one, then stop once the revision budget is spent.
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 10.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {
            "narrative_synthesis": 2,
            "technical_depth": 2,
            "issues": ["technical depth scored 2/5 — explain layer-1 mechanics"],
        },
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "deeper revised body with more detail"})

    out = _review_and_revise(
        fake,
        {"title": "T", "body": "short generic press release body"},
        system="sys",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 2  # spent both revision passes since quality never clears
    assert out["body"] == "deeper revised body with more detail"
    reviews = [e for e in trace if e["tool"] == "review_draft"]
    assert len(reviews) == 3  # initial grade + 2 rechecks


def test_revision_stops_once_max_passes_reached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stops after WRITER_REVISION_MAX_PASSES revisions even though quality never clears the bar."""
    # Bound it to 1 revision explicitly and confirm the loop respects it even
    # though the mock quality never improves — no unbounded looping.
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "WRITER_REVISION_MAX_PASSES", 1)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 10.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {
            "narrative_synthesis": 2,
            "technical_depth": 2,
            "issues": ["technical depth scored 2/5 — explain layer-1 mechanics"],
        },
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "deeper revised body with more detail"})

    _review_and_revise(
        fake,
        {"title": "T", "body": "short generic press release body"},
        system="sys",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 1


def test_second_revision_only_fires_when_first_still_fixable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Does not spend a second, unneeded revision call once the first revision already clears the bar."""
    # First revision clears the bar -> the loop must NOT spend a second
    # revision call it doesn't need, even though up to 2 are allowed.
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — Formatting Deserts: 6 prose blocks"]},
            {"grade": 8.0, "issues": []},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "body": "a much longer grounded body"})

    out = _review_and_revise(
        fake, {"title": "T", "body": "short"}, system="sys", gen_user="u", trace=trace
    )

    assert fake.calls == 1
    assert out["_heuristic_grade"]["grade"] == 8.0


class _SequenceMistral:
    """Returns a different revised draft on each successive call, and records the exact revise_user text sent each time (needed to check carry-forward memory reaches the prompt, not just the return value)."""

    def __init__(self, revisions: list[dict]) -> None:
        self._revisions = list(revisions)
        self.calls = 0
        self.sent_users: list[str] = []

    def chat_json_object(self, messages: list[dict], temperature: float | None = None) -> dict:  # noqa: ARG002 -- name must match the real callee's keyword arg
        self.sent_users.append(messages[-1]["content"])
        out = self._revisions[self.calls]
        self.calls += 1
        return out


def test_best_of_n_returns_highest_scoring_pass_not_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression-pin the real 2026-07-14 CompX incident: pass 2 (grade 8.6) fixed a headline issue but not yet its own new one; pass 3 (grade 7.3, the LAST pass) fixed that but re-broke structure pass 2 had already cleaned up. The loop must not just return whatever pass happened to run last — it must return the best-scoring draft it ever produced."""
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — Buried Metrics: 5 metrics in one paragraph"]},
            {"grade": 8.6, "issues": ["headline — colon-label title"]},
            {"grade": 7.3, "issues": []},  # clean, but scores lower than pass 2
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _SequenceMistral(
        [
            {"title": "T2", "body": "pass two body — the best draft"},
            {"title": "T3", "body": "pass three body — regressed but graded last"},
        ]
    )

    out = _review_and_revise(
        fake,
        {"title": "T1", "body": "pass one body"},
        system="sys",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 2
    assert out["body"] == "pass two body — the best draft"
    assert out["_heuristic_grade"]["grade"] == 8.6


def test_carry_forward_tells_revision_not_to_undo_earlier_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An issue resolved in pass 1 (dropped from pass 2's issue list) must be named explicitly in pass 2's revision prompt as 'already fixed — do not reintroduce', so the model doesn't trade it away while fixing the new issue pass 2 raised."""
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — Buried Metrics: 5 metrics in one paragraph"]},
            {"grade": 6.0, "issues": ["headline — colon-label title"]},
            {"grade": 8.0, "issues": []},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    fake = _SequenceMistral(
        [
            {"title": "T2", "body": "pass two body"},
            {"title": "T3", "body": "pass three body"},
        ]
    )

    _review_and_revise(
        fake,
        {"title": "T1", "body": "pass one body"},
        system="sys",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 2
    # First revision prompt has nothing to carry forward yet.
    assert "already fixed" not in fake.sent_users[0]
    # Second revision prompt must name the structure issue pass 1 already
    # fixed, since pass 2's own issue list no longer includes it.
    assert "already fixed" in fake.sent_users[1]
    assert "Buried Metrics" in fake.sent_users[1]


def test_quality_rubric_uses_rubric_client_not_writer_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM rubric grading is a judgment task, not generation — it must run on its own dedicated rubric client, not the Large writer client passed in for Stage 2 generation/revision. grade_article_quality_llm's own docstring calls itself a 'Fast Small-tier rubric', but that only ever applied to its unused default — the actual call site was silently passing the writer's Large client until fixed 2026-07-15. The rubric client was split out from the research client entirely 2026-08-06 (its own LLM_PROVIDER_RUBRIC), so a compose can route research and rubric grading to different providers independently."""
    import app.modules.ai.llm_compose as mc

    seen_clients: list[object] = []

    def _fake_grade_quality(*, title: str, body: str, client: Any = None) -> dict:  # noqa: ARG001, ANN401 -- name must match the real callee's keyword arg
        seen_clients.append(client)
        return {"narrative_synthesis": 4, "technical_depth": 4, "issues": []}

    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 9.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        _fake_grade_quality,
    )
    rubric_client = object()
    monkeypatch.setattr(mc, "get_llm_rubric_client", lambda: rubric_client)

    writer_client = _FakeMistral({"title": "X", "body": "Y"})
    _review_and_revise(
        writer_client, {"title": "T", "body": "good body"}, system="s", gen_user="u", trace=[]
    )

    assert seen_clients == [rubric_client]
    assert seen_clients[0] is not writer_client


def test_disabled_skips_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips grading and revision entirely, returning the payload untouched, when review is disabled."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg, "WRITER_REVIEW_ENABLED", False)
    trace: list[dict] = []
    fake = _FakeMistral({"title": "X", "body": "Y"})
    payload = {"title": "T", "body": "b"}

    out = _review_and_revise(fake, payload, system="s", gen_user="u", trace=trace)

    assert out is payload
    assert fake.calls == 0
    assert trace == []


def test_dead_link_feedback_forces_revision_naming_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner request 2026-07-16: a dead cited link must be surfaced to the WRITER during revision ('your link X is unreachable — find an alternative'), not just silently delinked by the post-hoc gate. The dead url must appear verbatim in the revision instructions so the model knows exactly which citation to replace."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 9.0, "issues": []},  # otherwise clean draft
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    monkeypatch.setattr("app.core.config.LINK_GATE_ENABLED", True, raising=False)
    dead_results = iter([["https://downbad.art/"], []])  # fixed after revision
    monkeypatch.setattr(
        "app.modules.newspaper.link_gate.dead_untraced_links",
        lambda _body, _trace, checked=None: next(dead_results),  # noqa: ARG005 -- name must match the real callee's keyword arg
    )

    class _CapturingMistral:
        def __init__(self) -> None:
            self.calls = 0
            self.last_messages = None

        def chat_json_object(self, messages: list[dict], temperature: float | None = None) -> dict:  # noqa: ARG002 -- name must match the real callee's keyword arg
            self.calls += 1
            self.last_messages = messages
            return {
                "title": "T",
                "body": "see [Downbad](https://downbad.farm/) instead",
                "summary": "s",
            }

    fake = _CapturingMistral()
    trace: list[dict] = []
    out = _review_and_revise(
        fake,
        {"title": "T", "body": "see [Downbad](https://downbad.art/)"},
        system="s",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 1  # the dead link alone forced the revision
    prompt_text = str(fake.last_messages)
    assert "https://downbad.art/" in prompt_text
    assert "unreachable" in prompt_text
    assert out["body"] == "see [Downbad](https://downbad.farm/) instead"
