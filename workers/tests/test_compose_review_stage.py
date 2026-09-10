"""Two-stage compose Stage 3+4: deterministic grade, then one revision if weak.

The warm generation pass has no tools, so the model can't call review_draft
itself — `_review_and_revise` must run the grader for it and revise once.
"""

from typing import Any, Never

import pytest

from app.modules.ai.llm_compose import (
    _build_revision_prompt,
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

    def test_needs_factual_fix_is_narrow_and_distinct_from_needs_depth(self) -> None:
        """Root-caused 2026-09-09 (design review before shipping): a WRONG factcheck verdict on an otherwise-clean draft must get a NARROW, surgical instruction, not needs_depth's broad "improve narrative synthesis/technical depth/critical distance" rewrite -- a one-sentence factual error must not license a whole-piece restructure it never asked for."""
        rule = _revision_length_rule(too_long=False, needs_depth=False, needs_factual_fix=True)
        assert "Correct or hedge EXACTLY" in rule
        assert "Change NOTHING else" in rule
        assert "Improve narrative synthesis" not in rule

    def test_needs_depth_wins_over_needs_factual_fix_when_both_true(self) -> None:
        """When the style rubric ALSO flagged this pass, the broad instruction already covers fixing everything flagged (factual concerns included) -- needs_depth takes precedence over needs_factual_fix, never the other way around."""
        rule = _revision_length_rule(too_long=False, needs_depth=True, needs_factual_fix=True)
        assert "Improve narrative synthesis" in rule
        assert "Correct or hedge EXACTLY" not in rule


class TestRevisionScopedIssueBlock:
    """Root-caused 2026-09-10 (Derova mempool piece, real compose).

    Fixing one flagged issue by fully regenerating the draft let an
    untouched-but-rewritten sentence drift into a NEW error -- round 2
    corrected one wrong fee-mechanics claim but introduced two more about the
    same topic. That same real compose's own grade_detail ALSO flagged a
    repetition issue in the same pass (the same judgment re-argued across
    four sections) -- a whole-piece pattern that genuinely needs cross-
    paragraph edits, unlike the one-sentence fee claim. A single blanket
    "touch only what's flagged" instruction would fight the repetition fix,
    so issues are scoped per KIND instead: localized issues (naming one
    specific claim) get a "redo only the paragraph containing it"
    instruction; document-wide issues (repetition, structure, narrative
    depth) keep the existing broader license to restructure across sections.
    Both can appear in the same prompt at once, which is exactly what
    regressed on the real compose.
    """

    def _prompt(self, fixable: list[str], localized_fixable: list[str], **kwargs: bool) -> str:
        return _build_revision_prompt(
            "gen_user text", fixable, [], localized_fixable=localized_fixable, **kwargs
        )

    def test_localized_issue_gets_redo_only_that_paragraph_instruction(self) -> None:
        """A factcheck-wrong-shaped issue (present in localized_fixable) gets the single-paragraph instruction, not license to touch the whole piece."""
        claim = 'factual concern (wrong): "congestion never raises the per-byte rate"'
        prompt = self._prompt([claim], [claim], too_long=False, needs_depth=False)
        assert claim in prompt
        assert "find the paragraph in the current draft that" in prompt
        assert "revise ONLY that paragraph" in prompt

    def test_document_wide_issue_keeps_broader_restructure_license(self) -> None:
        """A repetition/structure-shaped issue (absent from localized_fixable) keeps the existing cross-section license -- it must NOT get the single-paragraph instruction, which would fight a fix that inherently spans sections."""
        issue = "repetition scored 3/5 -- the same judgment restated across sections"
        prompt = self._prompt([issue], [], too_long=False, needs_depth=True)
        assert issue in prompt
        assert "restructure or rewrite across as many sections" in prompt
        assert "find the paragraph in the current draft that" not in prompt

    def test_mixed_localized_and_document_wide_issues_in_one_pass(self) -> None:
        """The exact shape of the real regression: a wrong factual claim AND a repetition issue flagged in the SAME pass. Each must get its own correctly-scoped instruction in the same prompt, not one blanket rule applied to both."""
        claim = 'factual concern (wrong): "congestion never raises the per-byte rate"'
        repetition = "repetition scored 3/5 -- the same judgment restated across sections"
        prompt = self._prompt([claim, repetition], [claim], too_long=False, needs_depth=True)
        assert claim in prompt
        assert repetition in prompt
        assert "revise ONLY that paragraph" in prompt
        assert "restructure or rewrite across as many sections" in prompt
        # needs_depth's own broader mandate is still present alongside the
        # per-issue scoping -- the two layers coexist, neither replaces the other.
        assert "Improve narrative synthesis" in prompt

    def test_truncation_to_top_ten_happens_before_scope_classification(self) -> None:
        """_revision_issues_block truncates fixable[:10] FIRST, then classifies what survives -- classifying before truncating could let a low-priority document-wide issue bump a high-priority localized one out of the top 10 instead of the other way around."""
        localized = ['factual concern (wrong): "the only real claim"']
        filler = [f"structure issue {i}" for i in range(12)]
        prompt = self._prompt(localized + filler, localized, too_long=False, needs_depth=False)
        assert "the only real claim" in prompt
        # Exactly 10 issues total should appear (1 localized + 9 filler) --
        # the 12th filler issue must be truncated away.
        assert "structure issue 8" in prompt
        assert "structure issue 9" not in prompt

    def test_no_localized_issues_omits_the_localized_block_entirely(self) -> None:
        """A pass with only document-wide issues must not mention paragraph-level scoping at all -- there's nothing for it to apply to."""
        issue = "structure — Formatting Deserts: 5 consecutive paragraphs"
        prompt = self._prompt([issue], [], too_long=False, needs_depth=False)
        assert "These specific claims need fixing" not in prompt
        assert "find the paragraph in the current draft that" not in prompt


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


def test_checkpoint_fires_once_per_grade_revise_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """checkpoint("writing", detail=...) fires once per pass through the grade/revise loop -- root-caused 2026-09-05: before this, the loop had NO checkpoints at all, so once Stage 2 generation finished, compose_sessions.duration_ms/status went stale for the ENTIRE grade/revise loop (up to WRITER_REVISION_MAX_PASSES full revision passes), and a slow rubric grade or revision looked identical to a hung compose in the admin Sessions view -- the "the status flag is bad" complaint this fixes."""
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
    checkpoints: list[tuple[str, str]] = []

    def _checkpoint(status: str, *, detail: str = "", digest: str = "") -> None:  # noqa: ARG001 -- digest unused, matches the real checkpoint signature
        checkpoints.append((status, detail))

    _review_and_revise(
        fake,
        {"title": "T", "body": "short"},
        system="sys",
        gen_user="u",
        trace=trace,
        checkpoint=_checkpoint,
    )

    # One checkpoint for the initial grade, one for the single revision pass
    # that follows (WRITER_REVISION_MAX_PASSES defaults to 3) -- each labeled
    # so an admin reading final_output mid-compose can tell them apart.
    assert checkpoints == [
        ("writing", "grading initial draft"),
        ("writing", "grade/revise pass 1 of 3"),
    ]


def test_revision_tool_loop_on_round_reaches_checkpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tool-enabled revision pass's own on_round callback reaches checkpoint("writing") for every round it runs, the same way the research stage's on_round already does -- root-caused 2026-09-05 alongside the per-pass checkpoint above: a revision that spends many rounds chasing a flagged issue (fetch_url, a chain lookup) previously refreshed compose_sessions.duration_ms/status ZERO times for the whole length of that tool loop."""
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — issue A"]},
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
    fake = _ToolFakeMistralWithRounds(['{"title": "T2", "body": "revised body", "summary": "s"}'])
    checkpoints: list[str] = []

    def _checkpoint(status: str, *, detail: str = "", digest: str = "") -> None:  # noqa: ARG001
        checkpoints.append(status)

    _review_and_revise(
        fake,
        {"title": "T1", "body": "original body"},
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
        checkpoint=_checkpoint,
    )

    # 2 per-pass checkpoints (initial grade + the one revision pass) plus one
    # more from the revision's own tool-loop round firing on_round.
    assert checkpoints.count("writing") == 3


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


def test_parse_article_fields_threads_regrade_unconfirmed_hold_reason() -> None:
    """Carries _regrade_unconfirmed_hold_reason from the payload dict onto the parsed dataclass, mirroring unsourced_hold_reason/broken_link_hold_reason -- publish_tasks._determine_review_divert reads it via ArticleComposeResult."""
    payload = {
        "title": "T",
        "summary": "S",
        "body": "B",
        "_regrade_unconfirmed_hold_reason": "revision regrade could not confirm the fix",
    }
    fields = _parse_article_fields(payload)
    assert fields.regrade_unconfirmed_hold_reason == "revision regrade could not confirm the fix"


def test_parse_article_fields_regrade_unconfirmed_hold_reason_defaults_empty() -> None:
    """Defaults to '' (not None) when no regrade-confirmation problem occurred -- publish_tasks treats a non-empty string as the hold trigger."""
    fields = _parse_article_fields({"title": "T", "summary": "S", "body": "B"})
    assert fields.regrade_unconfirmed_hold_reason == ""


def test_degraded_regrade_after_revision_holds_instead_of_silent_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bug: a revision attempted specifically because the LLM quality rubric flagged a fixable issue, followed by a regrade whose rubric call itself fails (production case: SoftTimeLimitExceeded mid-revision), must not be silently treated as a clean confirm-and-publish.

    Root cause: quality_needs_revision() reads a scoreless/errored quality
    dict as "nothing below threshold" (CLAUDE.md invariant 8 -- empty is not
    none-found), so the degraded regrade looks indistinguishable from a
    genuinely clean pass and the loop would return best_current as if the
    flagged narrative-synthesis issue had been confirmed fixed. It never was
    -- the regrade that was supposed to confirm it crashed. This must instead
    route to human review (CLAUDE.md invariant 1: never silently publish a
    finished-but-unconfirmed compose) via a non-empty
    _regrade_unconfirmed_hold_reason, the same wiring unsourced_hold_reason
    and broken_link_hold_reason already use end-to-end.
    """
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 7.0, "issues": []},
    )
    quality_calls = {"n": 0}

    def _quality(**_kw: object) -> dict:
        quality_calls["n"] += 1
        if quality_calls["n"] == 1:
            return {
                "narrative_synthesis": 2,
                "technical_depth": 4,
                "critical_distance": 4,
                "repetition": 4,
                "issues": ["narrative_synthesis scored 2/5 — weave the findings together"],
            }
        raise TimeoutError("SoftTimeLimitExceeded")  # the regrade's rubric call crashes

    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm", _quality
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "summary": "S", "body": "revised body attempting the fix"})

    out = _review_and_revise(
        fake,
        {"title": "T", "summary": "S", "body": "original weak body"},
        system="s",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 1  # exactly one revision attempted, matching the scenario
    reason = out.get("_regrade_unconfirmed_hold_reason", "")
    assert reason, (
        "a regrade degraded by a crashed rubric call after a revision attempt "
        "must not silently look like a confirmed-clean pass"
    )
    assert "narrative_synthesis" in reason
    fields = _parse_article_fields(out)
    assert fields.regrade_unconfirmed_hold_reason == reason


def test_degraded_factcheck_regrade_after_revision_holds_instead_of_silent_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same shape as the style-rubric degraded-regrade test above, for factcheck (root-caused via design review before shipping, 2026-09-09): a revision attempted specifically because factcheck flagged a WRONG claim, followed by a regrade whose factcheck call itself fails, must not be silently treated as a clean confirm-and-publish -- _grade_is_degraded must read a factcheck error the same way it already reads a quality-rubric error."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 7.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {
            "narrative_synthesis": 5,
            "technical_depth": 5,
            "critical_distance": 5,
            "repetition": 5,
            "issues": [],
        },
    )
    factcheck_calls = {"n": 0}

    def _factcheck(**_kw: object) -> dict:
        factcheck_calls["n"] += 1
        if factcheck_calls["n"] == 1:
            return {"claims": [{"claim": "a wrong claim", "verdict": "wrong", "why": "false"}]}
        raise TimeoutError("SoftTimeLimitExceeded")  # the regrade's factcheck call crashes

    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.check_factual_claims", _factcheck
    )
    trace: list[dict] = []
    fake = _FakeMistral({"title": "T2", "summary": "S", "body": "revised body attempting the fix"})

    out = _review_and_revise(
        fake,
        {"title": "T", "summary": "S", "body": "original body with a wrong claim"},
        system="s",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 1  # exactly one revision attempted, matching the scenario
    reason = out.get("_regrade_unconfirmed_hold_reason", "")
    assert reason, (
        "a regrade degraded by a crashed factcheck call after a revision attempt "
        "must not silently look like a confirmed-clean pass"
    )


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
    """Spends all available revision passes and returns the last draft when quality-LLM score never clears the bar."""
    # Quality mock never improves — with WRITER_REVISION_MAX_PASSES=3 (default)
    # this should genuinely attempt a SECOND and THIRD revision instead of
    # giving up after one, then stop once the revision budget is spent.
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

    assert fake.calls == 3  # spent all revision passes since quality never clears
    assert out["body"] == "deeper revised body with more detail"
    reviews = [e for e in trace if e["tool"] == "review_draft"]
    assert len(reviews) == 4  # initial grade + 3 rechecks


def test_factual_wrong_claim_forces_narrow_revision_when_style_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A WRONG factcheck verdict on an otherwise-clean draft (style rubric passes, nothing else flagged) must still force exactly one revision pass, and that pass's prompt must carry the narrow surgical-correction instruction, not the broad needs_depth rewrite."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 10.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {
            "narrative_synthesis": 5,
            "technical_depth": 5,
            "critical_distance": 5,
            "repetition": 5,
            "issues": [],
        },
    )
    factcheck_results = iter(
        [
            {
                "claims": [
                    {
                        "claim": "Only the creator can update or delete an application.",
                        "verdict": "wrong",
                        "why": "any account can submit that call",
                    }
                ]
            },
            {"claims": [{"claim": "Only the creator can update...", "verdict": "correct"}]},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.check_factual_claims",
        lambda **_kw: next(factcheck_results),
    )
    seq = _SequenceMistral(
        [{"title": "T2", "body": "the corrected claim, hedged to what is actually true"}]
    )

    out = _review_and_revise(
        seq,
        {"title": "T", "body": "a piece with one wrong protocol claim"},
        system="sys",
        gen_user="u",
        trace=[],
    )

    assert seq.calls == 1
    assert "Correct or hedge EXACTLY" in seq.sent_users[0]
    assert "factual concern (wrong)" in seq.sent_users[0]
    assert "Improve narrative synthesis" not in seq.sent_users[0]
    assert out["body"] == "the corrected claim, hedged to what is actually true"


def test_factual_overstated_alone_does_not_trigger_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An "overstated" (not "wrong") verdict, with a clean style rubric and nothing else flagged, must not by itself force a revision pass -- it's still persisted on the review record for a human reviewer, but a hedge-worthy claim is not automatically rewritten."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 10.0, "issues": []},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {
            "narrative_synthesis": 5,
            "technical_depth": 5,
            "critical_distance": 5,
            "repetition": 5,
            "issues": [],
        },
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.check_factual_claims",
        lambda **_kw: {
            "claims": [{"claim": "Algorand is very fast.", "verdict": "overstated", "why": "vague"}]
        },
    )
    fake = _FakeMistral({"title": "T2", "body": "should never be called"})
    trace: list[dict] = []

    out = _review_and_revise(
        fake,
        {"title": "T", "body": "a piece with one overstated but not wrong claim"},
        system="sys",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 0  # no revision fired
    assert out["body"] == "a piece with one overstated but not wrong claim"
    reviews = [e for e in trace if e["tool"] == "review_draft"]
    assert len(reviews) == 1
    assert any(
        "factual concern (overstated)" in c
        for c in reviews[0]["result"].get("factual_concerns", [])
    )


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


def test_best_of_n_penalizes_a_wrong_factcheck_verdict_not_just_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-cause regression (2026-09-09, design review before shipping): _draft_score used to only penalize the style-rubric's needs_revision, never factcheck's. A pass with a WRONG factcheck verdict but a marginally HIGHER heuristic grade than the very next (corrected) pass would win best-of-N and get returned -- silently discarding the forced revision that fixed it. Reproduced exactly: pass 1 grades 8.0 with a wrong claim; pass 2 (corrected) grades only 7.9. Without the fix, pass 1 wins on raw grade alone."""
    grades = iter(
        [
            {"grade": 8.0, "issues": []},
            {"grade": 7.9, "issues": []},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: next(grades),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {
            "narrative_synthesis": 5,
            "technical_depth": 5,
            "critical_distance": 5,
            "repetition": 5,
            "issues": [],
        },
    )
    factcheck_results = iter(
        [
            {"claims": [{"claim": "wrong protocol claim", "verdict": "wrong", "why": "false"}]},
            {"claims": [{"claim": "wrong protocol claim", "verdict": "correct"}]},
        ]
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.check_factual_claims",
        lambda **_kw: next(factcheck_results),
    )
    trace: list[dict] = []
    fake = _SequenceMistral(
        [{"title": "T2", "body": "the corrected, hedged claim — grades slightly lower"}]
    )

    out = _review_and_revise(
        fake,
        {"title": "T1", "body": "the wrong claim, stated confidently — grades slightly higher"},
        system="sys",
        gen_user="u",
        trace=trace,
    )

    assert fake.calls == 1
    assert out["body"] == "the corrected, hedged claim — grades slightly lower"


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


class _ToolFakeMistral:
    """Fake writer client for the tool-enabled revision path (chat_with_tools), returning each entry of `raw_replies` in order -- a plain string simulates a completion that does/doesn't parse as JSON, matching what `_attempt_revision`'s tool-enabled branch feeds to `_parse_json_object`."""

    def __init__(self, raw_replies: list[str]) -> None:
        self._raw_replies = list(raw_replies)
        self.calls = 0

    def chat_with_tools(self, _messages: list[dict], **_kwargs: object) -> str:
        raw = self._raw_replies[self.calls]
        self.calls += 1
        return raw


class _ToolFakeMistralWithRounds:
    """Like _ToolFakeMistral, but actually invokes the caller's `on_round` once before returning -- a real chat_with_tools call fires it once per round via run_tool_loop's _fire_on_round, so this is the minimal stand-in needed to prove a caller's on_round is genuinely threaded through to the revision's tool-enabled call, not just accepted as a kwarg and dropped."""

    def __init__(self, raw_replies: list[str]) -> None:
        self._raw_replies = list(raw_replies)
        self.calls = 0

    def chat_with_tools(
        self, _messages: list[dict], *, on_round: object | None = None, **_kwargs: object
    ) -> str:
        if on_round is not None:
            on_round()  # type: ignore[operator]
        raw = self._raw_replies[self.calls]
        self.calls += 1
        return raw


class _ToolFakeMistralRaising:
    """Fake writer client whose chat_with_tools always raises -- a real API/network failure, as opposed to a JSON-parse failure."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    def chat_with_tools(self, *_args: object, **_kwargs: object) -> Never:
        self.calls += 1
        raise self._exc


_NOOP_TOOL_SCHEMAS = [{"type": "function", "function": {"name": "noop", "parameters": {}}}]
_NOOP_TOOL_HANDLERS = {"noop": lambda **_kw: {}}


def test_json_parse_failure_retries_once_then_uses_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-08-28 audit regression: a tool-enabled revision whose final output doesn't parse as JSON gets ONE immediate retry of the identical call. When the retry succeeds, its draft is used (not the original) and the retry does not consume a WRITER_REVISION_MAX_PASSES pass -- a later genuinely NEW revision pass (triggered by a still-low grade) still gets its own full budget, proven here by a real second pass actually firing."""
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — issue A"]},
            {"grade": 6.0, "issues": ["structure — issue B"]},
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
    fake = _ToolFakeMistral(
        [
            "I tried a few lookups but ran out of budget with no final answer.",
            '{"title": "T2", "body": "recovered after one retry", "summary": "s"}',
            '{"title": "T3", "body": "a genuine second revision pass", "summary": "s"}',
        ]
    )

    out = _review_and_revise(
        fake,
        {"title": "T1", "body": "original body"},
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    # pass-1 attempt (fails) + pass-1 retry (succeeds) + pass-2 (a real,
    # separately-budgeted revision, since the recovered draft still had an
    # issue) -- WRITER_REVISION_MAX_PASSES defaults to 2, so this proves the
    # retry did not eat into that budget.
    assert fake.calls == 3
    assert out["body"] == "a genuine second revision pass"
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 1  # only the recovered pass-1 attempt failed
    assert "did not return a valid JSON object" in failures[0]["result"]["error"]


def test_json_parse_failure_retry_also_fails_falls_through_to_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH the original attempt and its one retry fail to parse as JSON, the loop falls through to the existing behavior exactly as before this retry was added -- returns best_current (the original draft here), with both failures visible in the trace."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["structure — issue A"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T1", "body": "original body"}
    fake = _ToolFakeMistral(["still no JSON", "still no JSON on the retry either"])

    out = _review_and_revise(
        fake,
        payload,
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    assert fake.calls == 2  # one attempt + one retry, no more
    assert out is payload  # original draft kept, same as pre-retry behavior
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 2  # both the original attempt AND the retry are visible
    assert all("did not return a valid JSON object" in f["result"]["error"] for f in failures)


def test_revision_call_exception_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real API/network error ('revision call failed: <exc>') is NOT retried -- unlike the JSON-parse/empty-body cases, an exception (e.g. a sustained outage or a rate limit already exhausted upstream) is not obviously a one-off glitch, so this stays exactly as it behaved before the retry was added (see also test_failed_revision_is_surfaced_not_silent for the tool-less path's identical behavior)."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["too long (3000 words) — cut padding"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T1", "body": "original body"}
    fake = _ToolFakeMistralRaising(RuntimeError("DeepSeek API 503"))

    out = _review_and_revise(
        fake,
        payload,
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    assert fake.calls == 1  # no retry on a real call failure
    assert out is payload
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 1
    assert "503" in failures[0]["result"]["error"]


def test_revision_empty_body_retries_once_then_uses_successful_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty-body reply ('revision returned an empty body') is also a TECHNICAL failure that gets one retry (2026-08-28 audit scope expansion) -- when the retry comes back with real content, that draft is used instead of falling back to the original."""
    grades = iter(
        [
            {"grade": 4.0, "issues": ["structure — issue A"]},  # triggers the revision
            {"grade": 8.0, "issues": []},  # recovered draft clears the bar
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
    fake = _ToolFakeMistral(
        [
            '{"title": "T2", "body": "", "summary": "s"}',
            '{"title": "T3", "body": "real content after the retry", "summary": "s"}',
        ]
    )

    out = _review_and_revise(
        fake,
        {"title": "T1", "body": "original body"},
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    assert fake.calls == 2  # one attempt + one retry
    assert out["body"] == "real content after the retry"
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 1  # only the empty-body attempt failed
    assert "empty body" in failures[0]["result"]["error"]


def test_revision_empty_body_retry_also_fails_falls_through_to_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When BOTH the original attempt and its retry come back with an empty body, the loop falls through to best_current (the original draft here), with both failures visible in the trace -- not an unbounded loop."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["too long (3000 words) — cut padding"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T1", "body": "original body"}
    fake = _ToolFakeMistral(
        [
            '{"title": "T2", "body": "", "summary": "s"}',
            '{"title": "T3", "body": "  ", "summary": "s"}',
        ]
    )

    out = _review_and_revise(
        fake,
        payload,
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    assert fake.calls == 2  # one attempt + one retry, no more
    assert out is payload
    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 2
    assert all("empty body" in f["result"]["error"] for f in failures)


def test_json_parse_failure_preserves_raw_broken_output_in_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-08-28 audit finding: a JSON-parse failure used to discard the model's actual (broken) output, recording only the fixed reason string -- "we do not save the content even if it is broken?". The raw completion must now be visible in the trace entry so a real failure is diagnosable after the fact."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["too long (3000 words) — cut padding"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T1", "body": "original body"}
    fake = _ToolFakeMistral(
        [
            "I checked three sources but never wrote a final JSON answer, oops.",
            "still not JSON on the retry either",
        ]
    )

    _review_and_revise(
        fake,
        payload,
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 2
    assert failures[0]["result"]["raw_output"] == (
        "I checked three sources but never wrote a final JSON answer, oops."
    )
    assert failures[1]["result"]["raw_output"] == "still not JSON on the retry either"


def test_revision_call_exception_never_carries_a_raw_output_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real exception has no model output to preserve -- the trace entry must not carry a stray/empty raw_output key for that failure mode."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["too long (3000 words) — cut padding"]},
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    trace: list[dict] = []
    payload = {"title": "T1", "body": "original body"}
    fake = _ToolFakeMistralRaising(RuntimeError("DeepSeek API 503"))

    _review_and_revise(
        fake,
        payload,
        system="sys",
        gen_user="u",
        trace=trace,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    failures = [e for e in trace if e.get("arguments", {}).get("revision") == "failed"]
    assert len(failures) == 1
    assert "raw_output" not in failures[0]["result"]


def test_revision_outgoing_request_is_bounded_not_the_full_prior_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-09-06 cost fix (operator-approved, measured tonight at ~65% of a real compose's uncached token spend): the tool-enabled revision call's OUTGOING request must no longer carry the full accumulated Stage-1/Stage-2 transcript. `chat_with_tools` now seeds it from `_compact_revision_prior` (the current draft) instead of `debug["messages"]`, on top of `revise_user`'s own digest+flagged-issues text (`_build_revision_prompt`/`_build_stage2_user`). This supersedes the pre-fix `test_revision_carries_forward_prior_stage_context_via_debug`, which asserted exactly the behavior this change removes -- see `test_revision_stored_transcript_keeps_full_prior_plus_its_own_turns_in_order` below for the other half (the STORED transcript is unaffected). Uses the REAL MistralProvider, like the test it replaces, since this is `_merged_convo_with_prior_debug`'s own behavior, not something the simplified fakes elsewhere in this file implement."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 4.0, "issues": ["structure — issue A"]},  # forces one revision call
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_quality_llm.grade_article_quality_llm",
        lambda **_kw: {"narrative_synthesis": 4, "technical_depth": 4, "issues": []},
    )
    from app.modules.ai.llm_openai_compatible import MistralProvider

    client = MistralProvider(api_key="test-key")
    sent_payloads: list[dict] = []

    def fake_post(payload: dict) -> dict:
        sent_payloads.append(payload)
        return {"choices": [{"message": {"content": '{"title": "T2", "body": "revised"}'}}]}

    monkeypatch.setattr(client, "_post", fake_post)

    debug: dict = {
        "messages": [
            {"role": "system", "content": "STAGE1 SYSTEM"},
            {"role": "user", "content": "STAGE1 RESEARCH PROMPT"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "fetch_url", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "name": "fetch_url", "content": "RESEARCH FACT"},
            {"role": "user", "content": "[stage 2 handoff] Research Digest:\nDIGEST TEXT"},
            {"role": "assistant", "content": '{"title": "T1", "body": "STAGE2 DRAFT"}'},
        ]
    }

    _review_and_revise(
        client,
        {"title": "T1", "body": "original body"},
        system="sys",
        gen_user="u",
        trace=[],
        debug=debug,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    assert sent_payloads  # the revision call actually happened
    sent_texts = [str(m.get("content", "")) for m in sent_payloads[0]["messages"]]
    # The full raw prior trace must NOT reach the outgoing request anymore.
    assert not any("STAGE1 SYSTEM" in t for t in sent_texts)
    assert not any("STAGE1 RESEARCH PROMPT" in t for t in sent_texts)
    assert not any("RESEARCH FACT" in t for t in sent_texts)
    assert not any("[stage 2 handoff]" in t for t in sent_texts)
    # The current draft it's revising must still be there, standing in for
    # the raw trace (via _compact_revision_prior), so the reviser knows what
    # it's actually revising.
    assert any("original body" in t for t in sent_texts)


def test_revision_stored_transcript_keeps_full_prior_plus_its_own_turns_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bounded outgoing request (see test above) must not come at the cost of the admin Sessions view: `debug["messages"]` -- the REAL, persisted transcript, a totally different object from what the reviser's own request actually sent once `prior_override` is in play -- must still hold the full prior Stage-1/Stage-2 history untouched, with this revision pass's own real turns (its [system, user] start, plus any tool-call round it actually ran) appended after it, in order. This is the other half of CLAUDE.md's regression-test requirement for the 2026-09-06 cost fix."""
    grades = iter(
        [
            {"grade": 5.0, "issues": ["structure — issue A"]},
            {"grade": 8.0, "issues": []},  # clean on the recheck -- exactly one revision pass
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
    from app.modules.ai.llm_openai_compatible import MistralProvider

    client = MistralProvider(api_key="test-key")
    # Round 1: the reviser calls a tool before finishing (proving a real
    # tool-call round still lands in the stored transcript, not just the
    # pass's own [system, user] start). Round 2: the final revised article.
    replies = [
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "r1",
                                "type": "function",
                                "function": {"name": "noop", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
        {
            "choices": [
                {"message": {"content": '{"title": "T2", "body": "revised", "summary": "s"}'}}
            ]
        },
    ]
    calls = {"n": 0}

    def fake_post(_payload: dict) -> dict:
        i = calls["n"]
        calls["n"] += 1
        return replies[i]

    monkeypatch.setattr(client, "_post", fake_post)

    prior_messages = [
        {"role": "system", "content": "STAGE1 SYSTEM"},
        {"role": "user", "content": "STAGE1 RESEARCH PROMPT"},
        {"role": "assistant", "content": '{"title": "T1", "body": "STAGE2 DRAFT"}'},
    ]
    debug: dict = {"messages": list(prior_messages)}

    _review_and_revise(
        client,
        {"title": "T1", "body": "original body"},
        system="sys",
        gen_user="u",
        trace=[],
        debug=debug,
        revision_tool_schemas=_NOOP_TOOL_SCHEMAS,
        revision_tool_handlers=_NOOP_TOOL_HANDLERS,
    )

    stored = debug["messages"]
    # The original prior transcript survives, untouched, at the front.
    assert stored[: len(prior_messages)] == prior_messages
    tail = stored[len(prior_messages) :]
    # The pass-0 grade appends a synthetic review_draft turn (see
    # _record_grade/_debug_tool_turn) BEFORE the revision call -- locate the
    # revision pass's own start (its fresh "sys..." system message) after it.
    revision_start = next(
        i
        for i, m in enumerate(tail)
        if m.get("role") == "system" and str(m.get("content", "")).startswith("sys")
    )
    revision_tail = tail[revision_start:]
    assert revision_tail[0]["role"] == "system"
    assert revision_tail[1]["role"] == "user"
    assert any(m.get("role") == "assistant" and m.get("tool_calls") for m in revision_tail)
    assert any(m.get("role") == "tool" and m.get("name") == "noop" for m in revision_tail)
