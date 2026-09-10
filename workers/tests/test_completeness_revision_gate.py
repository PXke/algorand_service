"""Completeness-gate self-correction (2026-09-09).

A missing mandatory due-diligence tool call (screen_sanctions_and_pep,
query_corporate_registry) gets ONE in-loop revision chance -- via
_completeness_gate_issues feeding _collect_fixable_issues, the same pattern
unsourced_specifics/broken_link_claim already use -- before the post-hoc
gatekeeper hold. Opt-in (check_completeness_gate), fresh content only.
"""

from __future__ import annotations

import pytest

from app.modules.ai import llm_compose as mc


def test_completeness_gate_issues_flags_missing_sanctions_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source naming a founder/CEO with no screen_sanctions_and_pep call in the trace produces one issue naming the tool.

    Doesn't assert on the exact extracted name -- named_persons_unscreened's
    own capitalized-word-run heuristic has known false-positive noise on
    lowercase words under its re.IGNORECASE flag (a pre-existing
    completeness.py quirk, not this function's concern; see that module's
    own tests for its extraction behavior).
    """
    monkeypatch.setattr("app.core.config.COMPLETENESS_REVISION_ENABLED", True, raising=False)
    title = "Acme Names Jane Doe CEO"
    body = "Founder Jane Doe takes over as chief executive of Acme."
    trace = [{"tool": "search_web", "arguments": {}, "result": {}}]

    issues = mc._completeness_gate_issues(title, body, trace, enabled=True)

    assert len(issues) == 1
    assert "screen_sanctions_and_pep" in issues[0]


def test_completeness_gate_issues_flags_missing_corporate_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source naming an incorporated company with no query_corporate_registry call produces one issue naming the tool."""
    monkeypatch.setattr("app.core.config.COMPLETENESS_REVISION_ENABLED", True, raising=False)
    title = "Acme Foundation US, Inc. Expands"
    body = "Acme Foundation US, Inc. is a Delaware corporation that funds the protocol."
    trace: list[dict] = []

    issues = mc._completeness_gate_issues(title, body, trace, enabled=True)

    assert len(issues) == 1
    assert "query_corporate_registry" in issues[0]


def test_completeness_gate_issues_empty_once_tool_was_called() -> None:
    """Once the trace shows the tool ran (even if the call itself errored -- e.g. no API key configured), the rule is satisfied -- this is a 'was it attempted' check, matching every other completeness rule."""
    title = "Acme Names Jane Doe CEO"
    body = "Founder Jane Doe takes over as chief executive of Acme."
    trace = [
        {
            "tool": "screen_sanctions_and_pep",
            "arguments": {"person_name": "Jane Doe"},
            "result": {"error": "No API key provided."},
        }
    ]

    assert mc._completeness_gate_issues(title, body, trace, enabled=True) == []


def test_completeness_gate_issues_empty_when_not_enabled() -> None:
    """``enabled=False`` (the default -- every recompose call site) short-circuits before the completeness check even runs, regardless of a real completeness failure being present. This is the opt-in the whole fix hinges on."""
    title = "Acme Names Jane Doe CEO"
    body = "Founder Jane Doe takes over as chief executive of Acme."

    assert mc._completeness_gate_issues(title, body, []) == []
    assert mc._completeness_gate_issues(title, body, [], enabled=False) == []


def test_completeness_gate_issues_empty_when_config_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMPLETENESS_REVISION_ENABLED=False is a full kill switch even when the caller passes enabled=True, matching UNSOURCED_SPECIFICS_GATE_ENABLED/BROKEN_LINK_CLAIM_GATE_ENABLED's own precedent."""
    monkeypatch.setattr("app.core.config.COMPLETENESS_REVISION_ENABLED", False, raising=False)
    title = "Acme Names Jane Doe CEO"
    body = "Founder Jane Doe takes over as chief executive of Acme."

    assert mc._completeness_gate_issues(title, body, [], enabled=True) == []


def test_completeness_gate_issues_empty_with_no_trigger() -> None:
    """No founder/CEO/company mention in the source -- no rule fires, nothing to flag, even when enabled."""
    assert (
        mc._completeness_gate_issues(
            "Routine Update", "A routine mainnet parameter update.", [], enabled=True
        )
        == []
    )


def _fixable_kwargs(**overrides: object) -> dict:
    base: dict[str, object] = {
        "review": {},
        "quality": {"issues": []},
        "needs_revision": False,
        "title": "Acme Names Jane Doe CEO",
        "body": "Founder Jane Doe takes over as chief executive of Acme.",
        "trace": [],
        "gen_user": "",
        "user": "",
        "research_user": None,
        "link_check_cache": {},
        "chain_check_cache": {},
        "factcheck": None,
        "check_completeness_gate": False,
    }
    base.update(overrides)
    return base


def test_collect_fixable_issues_includes_completeness_gap_when_enabled() -> None:
    """check_completeness_gate=True on a completeness-failing draft surfaces the gap in the returned fixable list (and localized_fixable, since it names a specific due-diligence gap) and stashes it onto review['completeness_gap']."""
    review: dict = {}
    fixable, localized_fixable = mc._collect_fixable_issues(
        **_fixable_kwargs(review=review, check_completeness_gate=True)
    )

    assert any("screen_sanctions_and_pep" in i for i in fixable)
    assert any("screen_sanctions_and_pep" in i for i in localized_fixable)
    assert "completeness_gap" in review


def test_collect_fixable_issues_ignores_completeness_by_default() -> None:
    """check_completeness_gate defaults False -- a recompose call site (which never passes it) must never see a completeness-driven revision, matching the deliberate exclusion _grade_and_gate already documents."""
    review: dict = {}
    fixable, localized_fixable = mc._collect_fixable_issues(**_fixable_kwargs(review=review))

    assert not any("screen_sanctions_and_pep" in i for i in fixable)
    assert not any("screen_sanctions_and_pep" in i for i in localized_fixable)
    assert "completeness_gap" not in review


def test_completeness_fixable_survives_top_ten_truncation_ordering() -> None:
    """Placed right after factual_fixable (same reasoning as that list's own ordering comment) -- a due-diligence gap must not get crowded out of _build_revision_prompt's fixable[:10] by lower-stakes issues."""
    review = {"issues": [f"schema issue {i}" for i in range(15)]}
    fixable, _localized_fixable = mc._collect_fixable_issues(
        **_fixable_kwargs(review=review, check_completeness_gate=True)
    )

    completeness_idx = next(i for i, v in enumerate(fixable) if "screen_sanctions_and_pep" in v)
    assert completeness_idx == 0  # no factual_fixable in this draft, so it leads
    assert completeness_idx < 10


def test_check_completeness_gate_threads_through_run_grade_revise_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_grade_revise_loop forwards check_completeness_gate into _collect_fixable_issues on every pass -- a thin integration check on top of the direct unit tests above, using the same monkeypatch-the-loop-internals style test_llm_compose_usage_accounting.py already uses for this function."""
    seen_flags: list[bool] = []

    def _fake_grade(*_a: object, **_kw: object) -> dict:
        return {"quality": {"issues": []}, "factcheck": {}}

    def _fake_collect(
        *_a: object, check_completeness_gate: bool, **_kw: object
    ) -> tuple[list[str], list[str]]:
        seen_flags.append(check_completeness_gate)
        return [], []

    monkeypatch.setattr(mc, "_grade_current_draft", _fake_grade)
    monkeypatch.setattr(mc, "_record_grade", lambda *_a, **_kw: None)
    monkeypatch.setattr(mc, "_collect_fixable_issues", _fake_collect)
    monkeypatch.setattr(mc, "_draft_score", lambda *_a, **_kw: 1.0)
    monkeypatch.setattr(mc, "_regrade_is_unconfirmed", lambda *_a, **_kw: False)
    monkeypatch.setattr(mc, "_maybe_stamp_regrade_unconfirmed", lambda *_a, **_kw: None)

    payload = {"title": "T", "body": "B", "summary": "S"}
    mc._run_grade_revise_loop(
        llm=object(),
        payload=payload,
        quality_llm=object(),
        system="sys",
        gen_user="gen",
        trace=[],
        debug=None,
        user="u",
        research_user=None,
        is_special_edition=False,
        max_revisions=1,
        revision_tool_schemas=None,
        revision_tool_handlers=None,
        check_completeness_gate=True,
    )

    assert seen_flags == [True]
