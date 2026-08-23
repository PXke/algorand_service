"""translate_article_batch: engine-batched local translation.

Only one language's worth of a model may be resident in production memory at
a time (owner requirement, 2026-07-30) — these tests verify the load-once-
per-engine, unload-before-the-next-group, never-both-loaded, non-reentrant-
lock properties the batch orchestrator exists to guarantee, all without
touching real model weights (the actual translate/load primitives are
monkeypatched throughout).
"""

from __future__ import annotations

import pytest

from app.modules.ai import local_translate as lt


def _stub_lock(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake acquire/release with real single-holder semantics, and a log of every acquire call (for the reentrancy regression test)."""
    calls: list[str] = []
    held = {"token": None}

    def _acquire(_key: str, _ttl: int) -> str | None:
        calls.append("acquire")
        if held["token"] is not None:
            return None
        held["token"] = "tok"
        return "tok"

    def _release(_key: str, token: str) -> None:
        if held["token"] == token:
            held["token"] = None

    monkeypatch.setattr("app.modules.ai.local_translate_lock.acquire", _acquire)
    monkeypatch.setattr("app.modules.ai.local_translate_lock.release", _release)
    return calls


def _stub_loaders(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Fake _load_*/unload_engine tracking call order and a "currently resident" set, so a test can assert its size never exceeds 1.

    Mirrors the real _load_seamless/_load_milmmt's own idempotent caching
    (``if not _seamless: ...``) -- a second call for an already-resident
    engine is a no-op, same as production. That caching is what actually
    guarantees "load once per group": translate_article_batch never calls
    _load_* directly, it only calls it indirectly once per language via
    _translate_article_no_lock, so the real one-load-per-group property
    lives in the loaders themselves, not in the orchestrator.
    """
    state = {"resident": set(), "order": []}

    def _load_seamless() -> None:
        if "seq2seq" not in state["resident"]:
            state["order"].append("load:seq2seq")
            state["resident"].add("seq2seq")

    def _load_milmmt() -> None:
        if "milmmt" not in state["resident"]:
            state["order"].append("load:milmmt")
            state["resident"].add("milmmt")

    def _unload(engine: str) -> None:
        state["order"].append(f"unload:{engine}")
        state["resident"].discard(engine)

    monkeypatch.setattr(lt, "_load_seamless", _load_seamless)
    monkeypatch.setattr(lt, "_load_milmmt", _load_milmmt)
    monkeypatch.setattr(lt, "unload_engine", _unload)
    return state


def _stub_translate(
    monkeypatch: pytest.MonkeyPatch, *, fail_langs: set[str] | None = None
) -> list[str]:
    """Fake _translate_article_no_lock: records which language it was called for (in call order), triggers the matching engine's fake loader first (mirroring the real _load_* call inside _translate_text_*), and raises for any lang in fail_langs."""
    fail_langs = fail_langs or set()
    seen: list[str] = []

    def _fake(*, target_language: str, **_kw: object) -> dict[str, str]:
        engine = lt.engine_for(target_language)
        (lt._load_seamless if engine == "seq2seq" else lt._load_milmmt)()
        seen.append(target_language)
        if target_language in fail_langs:
            raise RuntimeError("boom")
        return {"title": f"T-{target_language}", "summary": "S", "body": "B"}

    monkeypatch.setattr(lt, "_translate_article_no_lock", _fake)
    return seen


def _batch(langs: list[str], **kw: object) -> dict:
    return lt.translate_article_batch(
        english_title="T", english_summary="S", english_body="B", target_languages=langs, **kw
    )


def test_batch_loads_each_engine_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """fa, ps, ru: two milmmt languages either side of one seq2seq language in ARTICLE_TRANSLATION_LANGS order -- must not reload milmmt for the second one."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    _batch(["fa", "ps", "ru"])

    loads = [e for e in state["order"] if e.startswith("load:")]
    assert loads == ["load:seq2seq", "load:milmmt"]


def test_batch_never_holds_both_engines_resident(monkeypatch: pytest.MonkeyPatch) -> None:
    """seq2seq is fully unloaded before milmmt loads -- the actual production memory guarantee."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)

    max_resident = {"n": 0}

    def _fake(*, target_language: str, **_kw: object) -> dict[str, str]:
        engine = lt.engine_for(target_language)
        (lt._load_seamless if engine == "seq2seq" else lt._load_milmmt)()
        max_resident["n"] = max(max_resident["n"], len(state["resident"]))
        return {"title": target_language, "summary": "", "body": ""}

    monkeypatch.setattr(lt, "_translate_article_no_lock", _fake)

    _batch(["ps", "fa", "ru"])

    assert max_resident["n"] == 1
    assert state["order"] == ["load:seq2seq", "unload:seq2seq", "load:milmmt", "unload:milmmt"]


def test_batch_unloads_between_groups_in_fixed_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """seq2seq always runs (and is torn down) before milmmt, regardless of input language order -- _ENGINE_ORDER is fixed, not derived from the caller's list."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    _batch(["ru", "fa", "ps"])  # ps (seq2seq) deliberately last in the input list

    assert state["order"] == ["load:seq2seq", "unload:seq2seq", "load:milmmt", "unload:milmmt"]


def test_batch_persists_incrementally_in_engine_grouped_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_language_done fires once per successful language, engine-grouped (seq2seq first), not in the caller's original list order."""
    _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    done: list[str] = []
    outcome = _batch(["fa", "ps", "ru"], on_language_done=lambda lang, _result: done.append(lang))

    assert done == ["ps", "fa", "ru"]
    assert outcome["ok"] == ["ps", "fa", "ru"]
    assert outcome["failed"] == {}


def test_batch_continues_past_one_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One language raising doesn't abort the batch -- caught, recorded, the rest still run, and the group still unloads."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch, fail_langs={"fa"})

    done: list[str] = []
    outcome = _batch(["fa", "ru", "ps"], on_language_done=lambda lang, _r: done.append(lang))

    assert outcome["ok"] == ["ps", "ru"]  # seq2seq (ps) group first, then milmmt (fa fails, ru ok)
    assert outcome["failed"] == {"fa": "translation_error"}
    assert "fa" not in done
    assert "unload:milmmt" in state["order"]
    assert "unload:seq2seq" in state["order"]


def test_on_language_done_failure_does_not_abort_batch_or_skip_unload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A persistence callback raising for one language must not lose the rest of the batch or leak a loaded model."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    def _flaky(lang: str, _result: dict) -> None:
        if lang == "fa":
            raise RuntimeError("cassandra write failed")

    outcome = _batch(["fa", "ru"], on_language_done=_flaky)

    assert outcome["ok"] == ["fa", "ru"]  # translation itself still succeeded for fa
    assert "unload:milmmt" in state["order"]


def test_on_language_start_fires_before_each_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_language_start fires once per language, in engine-grouped order, before that language's translate call -- lets a caller record a 'running' row before the work begins, not just after it ends."""
    _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    seen = _stub_translate(monkeypatch)

    started: list[str] = []
    outcome = _batch(["fa", "ps", "ru"], on_language_start=lambda lang: started.append(lang))

    assert started == ["ps", "fa", "ru"]
    # started must precede translate for every language, not just match its set
    assert started == seen
    assert outcome["ok"] == ["ps", "fa", "ru"]


def test_on_language_error_fires_only_for_the_failing_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_language_error fires with (lang, reason) for a failed language and is never called for a successful one."""
    _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch, fail_langs={"fa"})

    errors: list[tuple[str, str]] = []
    outcome = _batch(["fa", "ru", "ps"], on_language_error=lambda lang, reason: errors.append((lang, reason)))

    assert errors == [("fa", "translation_error")]
    assert outcome["failed"] == {"fa": "translation_error"}


def test_on_language_error_failure_does_not_abort_batch_or_skip_unload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error callback raising for one language must not lose the rest of the batch or leak a loaded model."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch, fail_langs={"fa"})

    def _flaky(_lang: str, _reason: str) -> None:
        raise RuntimeError("session store write failed")

    outcome = _batch(["fa", "ru"], on_language_error=_flaky)

    assert outcome["failed"] == {"fa": "translation_error"}
    assert outcome["ok"] == ["ru"]
    assert "unload:milmmt" in state["order"]


def test_lock_acquired_exactly_once_for_a_multi_language_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the reentrancy bug: the batch must hold ONE lock for its whole duration, never re-acquiring per language (a second acquire on an already-held SETNX-style lock returns None and would incorrectly raise LocalTranslateBusyError against itself)."""
    acquire_calls = _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    _batch(["fa", "ps", "ru", "zh"])

    assert acquire_calls == ["acquire"]


def test_translate_article_local_unchanged_single_call_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """translate_article_local (the pre-existing single-language entry point used by the manual backfill script) still works standalone -- still acquires its own lock, still returns the same shape."""
    _stub_lock(monkeypatch)

    monkeypatch.setattr(
        lt,
        "_translate_article_no_lock",
        lambda **_kw: {"title": "t", "summary": "s", "body": "b"},
    )

    out = lt.translate_article_local(
        english_title="T", english_summary="S", english_body="B", target_language="fr"
    )
    assert out == {"title": "t", "summary": "s", "body": "b"}


# --- list/table cell-splitting: seq2seq (SeamlessM4T) only ------------------


def _stub_seamless_text(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake _translate_text_seamless: uppercases and records every call's input text, so a test can assert exactly what got sent to the model."""
    calls: list[str] = []

    def _fake(text: str, _target_language: str) -> str:
        calls.append(text)
        return text.upper()

    monkeypatch.setattr(lt, "_translate_text_seamless", _fake)
    return calls


def test_translate_block_splits_list_items_for_seq2seq(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pure list block routed to seq2seq is translated item-by-item and reassembled with its original bullet prefix -- the actual production fix for the survey's list-collapse finding."""
    calls = _stub_seamless_text(monkeypatch)
    block = "- one\n- two\n- three"
    result = lt._translate_block(block, "ps", "seq2seq")
    assert result == "- ONE\n- TWO\n- THREE"
    assert calls == ["one", "two", "three"]


def test_translate_block_splits_table_cells_for_seq2seq(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pure table block routed to seq2seq is translated cell-by-cell and reassembled -- the separator row (all-dashes) is never sent to the model at all."""
    calls = _stub_seamless_text(monkeypatch)
    block = "| Category | Count |\n| --- | --- |\n| tools | 18 |"
    result = lt._translate_block(block, "ps", "seq2seq")
    assert result == "| CATEGORY | COUNT |\n| --- | --- |\n| TOOLS | 18 |"
    assert calls == ["Category", "Count", "tools", "18"]


def test_translate_block_does_not_split_for_milmmt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same list/table content routed to milmmt goes through as one whole-block call -- MiLMMT is the proven candidate for this, deliberately untouched."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lt, "_translate_text_milmmt", lambda text, lang: calls.append((text, lang)) or text.upper()
    )
    block = "- one\n- two"
    result = lt._translate_block(block, "fr", "milmmt")
    assert result == block.upper()
    assert calls == [(block, "fr")]


def test_translate_block_plain_paragraph_untouched_by_splitting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A regular paragraph (not a pure list or table) still goes through seq2seq as one whole-block call, even with a dash in it."""
    calls = _stub_seamless_text(monkeypatch)
    text = "Just a sentence with a dash - not a list."
    lt._translate_block(text, "ps", "seq2seq")
    assert calls == [text]


def test_translate_block_falls_back_to_source_on_empty_milmmt_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whole-block MiLMMT call that returns nothing (found 2026-08-04: a real 3-row table vanished from a French article this way) keeps the English source instead of silently dropping the block."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "")
    table = "| Concept | Meaning |\n| --- | --- |\n| Vaults | Hold assets |"
    result = lt._translate_block(table, "fr", "milmmt")
    assert result == table


def test_translate_block_falls_back_to_source_on_whitespace_only_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only model output is treated the same as truly empty output."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "   \n  ")
    text = "A paragraph that deserves a real translation."
    result = lt._translate_block(text, "fr", "milmmt")
    assert result == text


def test_translate_block_falls_back_to_source_on_empty_seq2seq_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback applies to the heading path too, not just the whole-block path."""
    monkeypatch.setattr(lt, "_translate_text_seamless", lambda _text, _lang: "")
    heading = "## A Real Heading"
    result = lt._translate_block(heading, "ps", "seq2seq")
    assert result == heading


def test_translate_block_keeps_real_translation_when_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback must never trigger on a normal, successful translation."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "Bonjour le monde")
    result = lt._translate_block("Hello world", "fr", "milmmt")
    assert result == "Bonjour le monde"


# --- alignment-findings observability ----------------------------------------


def test_log_alignment_findings_warns_on_block_count_mismatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A translation missing a whole block (the real NFDomains defect this exists to catch) is logged, not silent."""
    with caplog.at_level("WARNING", logger=lt.logger.name):
        lt._log_alignment_findings(
            "## Heading\n\nFirst paragraph.\n\n## Second heading\n\nSecond paragraph.",
            "## Heading\n\nFirst paragraph.",
            "fr",
        )
    assert any("block count mismatch" in r.message for r in caplog.records)


def test_log_alignment_findings_silent_on_clean_translation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A translation with matching structure and grounded digits logs nothing -- this is a signal, not noise on every call."""
    with caplog.at_level("WARNING", logger=lt.logger.name):
        lt._log_alignment_findings("Just a plain paragraph with no numbers.", "Juste un paragraphe.", "fr")
    assert not caplog.records


def test_log_alignment_findings_never_raises_when_the_check_itself_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug in the eval-harness functions must never break the translation write path that calls this."""

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("eval harness bug")

    monkeypatch.setattr("app.modules.ai.translation_eval.structural_alignment", _boom)
    lt._log_alignment_findings("English body.", "Corps français.", "fr")  # must not raise


def test_translate_article_no_lock_calls_alignment_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The observability check runs as part of the normal per-language translation flow, not just when called directly."""
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(lt, "_log_alignment_findings", lambda en, tr, lang: calls.append((en, tr, lang)))
    monkeypatch.setattr(lt, "_translate_block", lambda text, _lang, _engine: text.upper())

    lt._translate_article_no_lock(
        english_title="T", english_summary="S", english_body="Body.", target_language="fr"
    )

    assert len(calls) == 1
    assert calls[0][2] == "fr"
