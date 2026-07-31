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
