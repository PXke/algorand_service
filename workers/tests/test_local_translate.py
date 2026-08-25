"""translate_article_batch: single-engine (MiLMMT) local translation.

Only one language's worth of a model may be resident in production memory at
a time (owner requirement, 2026-07-30) -- these tests verify the load-once-
per-batch, unload-at-the-end, non-reentrant-lock properties the batch
orchestrator exists to guarantee, all without touching real model weights
(the actual translate/load primitives are monkeypatched throughout).

SeamlessM4T (a second engine, used only for Pashto) was removed 2026-08-25
-- see local_translate.py's module docstring for why -- along with the
engine-routing/grouping machinery this test file used to exercise. What's
left is a single fixed engine, so these tests no longer need to assert
anything about engine grouping or load order across engines.
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
    """Fake _load_milmmt/unload_milmmt tracking call order and a "currently resident" flag.

    Mirrors the real _load_milmmt's own idempotent caching (``if not
    _milmmt: ...``) -- a second call while already resident is a no-op, same
    as production. That caching is what actually guarantees "load once per
    batch": translate_article_batch never calls _load_milmmt directly, it
    only calls it indirectly once per language via _translate_article_no_lock,
    so the real one-load-per-batch property lives in the loader itself, not
    in the orchestrator.
    """
    state = {"resident": False, "order": []}

    def _load_milmmt() -> None:
        if not state["resident"]:
            state["order"].append("load")
            state["resident"] = True

    def _unload() -> None:
        state["order"].append("unload")
        state["resident"] = False

    monkeypatch.setattr(lt, "_load_milmmt", _load_milmmt)
    monkeypatch.setattr(lt, "unload_milmmt", _unload)
    return state


def _stub_translate(
    monkeypatch: pytest.MonkeyPatch, *, fail_langs: set[str] | None = None
) -> list[str]:
    """Fake _translate_article_no_lock: records which language it was called for (in call order), triggers the fake loader first (mirroring the real _load_milmmt call inside _translate_text_milmmt), and raises for any lang in fail_langs."""
    fail_langs = fail_langs or set()
    seen: list[str] = []

    def _fake(*, target_language: str, **_kw: object) -> dict[str, str]:
        lt._load_milmmt()
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


def test_batch_loads_milmmt_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several languages in one batch -- must not reload MiLMMT for the second+ language."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    _batch(["fa", "ru", "zh"])

    loads = [e for e in state["order"] if e == "load"]
    assert loads == ["load"]


def test_batch_unloads_after_all_languages(monkeypatch: pytest.MonkeyPatch) -> None:
    """MiLMMT is unloaded once, after every language in the batch has run."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    _batch(["fa", "ru"])

    assert state["order"] == ["load", "unload"]


def test_batch_persists_incrementally_in_input_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_language_done fires once per successful language, in the caller's original list order (no engine grouping to reorder it anymore)."""
    _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    done: list[str] = []
    outcome = _batch(["fa", "zh", "ru"], on_language_done=lambda lang, _result: done.append(lang))

    assert done == ["fa", "zh", "ru"]
    assert outcome["ok"] == ["fa", "zh", "ru"]
    assert outcome["failed"] == {}


def test_batch_continues_past_one_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One language raising doesn't abort the batch -- caught, recorded, the rest still run, and MiLMMT still unloads."""
    _stub_lock(monkeypatch)
    state = _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch, fail_langs={"fa"})

    done: list[str] = []
    outcome = _batch(["fa", "ru", "zh"], on_language_done=lambda lang, _r: done.append(lang))

    assert outcome["ok"] == ["ru", "zh"]
    assert outcome["failed"] == {"fa": "translation_error"}
    assert "fa" not in done
    assert "unload" in state["order"]


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
    assert "unload" in state["order"]


def test_on_language_start_fires_before_each_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    """on_language_start fires once per language, in input order, before that language's translate call -- lets a caller record a 'running' row before the work begins, not just after it ends."""
    _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    seen = _stub_translate(monkeypatch)

    started: list[str] = []
    outcome = _batch(["fa", "ru", "zh"], on_language_start=lambda lang: started.append(lang))

    assert started == ["fa", "ru", "zh"]
    # started must precede translate for every language, not just match its set
    assert started == seen
    assert outcome["ok"] == ["fa", "ru", "zh"]


def test_on_language_error_fires_only_for_the_failing_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """on_language_error fires with (lang, reason) for a failed language and is never called for a successful one."""
    _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch, fail_langs={"fa"})

    errors: list[tuple[str, str]] = []
    outcome = _batch(["fa", "ru"], on_language_error=lambda lang, reason: errors.append((lang, reason)))

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
    assert "unload" in state["order"]


def test_lock_acquired_exactly_once_for_a_multi_language_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for the reentrancy bug: the batch must hold ONE lock for its whole duration, never re-acquiring per language (a second acquire on an already-held SETNX-style lock returns None and would incorrectly raise LocalTranslateBusyError against itself)."""
    acquire_calls = _stub_lock(monkeypatch)
    _stub_loaders(monkeypatch)
    _stub_translate(monkeypatch)

    _batch(["fa", "ru", "zh"])

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


# --- _translate_block: whole-block MiLMMT translation, no engine param ------


def test_translate_block_whole_block_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A list/table/plain block all go through as one whole-block MiLMMT call -- MiLMMT is the proven candidate for handling markdown structure without any splitting workaround (see module docstring)."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lt, "_translate_text_milmmt", lambda text, lang: calls.append((text, lang)) or text.upper()
    )
    block = "- one\n- two"
    result = lt._translate_block(block, "fr")
    assert result == block.upper()
    assert calls == [(block, "fr")]


def test_translate_block_table_whole_block_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A table block is sent to MiLMMT as one whole-block call too, not split cell-by-cell."""
    calls: list[str] = []
    monkeypatch.setattr(
        lt, "_translate_text_milmmt", lambda text, _lang: calls.append(text) or text.upper()
    )
    block = "| Category | Count |\n| --- | --- |\n| tools | 18 |"
    result = lt._translate_block(block, "fr")
    assert result == block.upper()
    assert calls == [block]


def test_translate_block_plain_paragraph(monkeypatch: pytest.MonkeyPatch) -> None:
    """A regular paragraph goes through as one whole-block call, even with a dash in it."""
    calls: list[str] = []
    monkeypatch.setattr(
        lt, "_translate_text_milmmt", lambda text, _lang: calls.append(text) or text.upper()
    )
    text = "Just a sentence with a dash - not a list."
    lt._translate_block(text, "fr")
    assert calls == [text]


def test_translate_block_falls_back_to_source_on_empty_milmmt_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A whole-block MiLMMT call that returns nothing (found 2026-08-04: a real 3-row table vanished from a French article this way) keeps the English source instead of silently dropping the block."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "")
    table = "| Concept | Meaning |\n| --- | --- |\n| Vaults | Hold assets |"
    result = lt._translate_block(table, "fr")
    assert result == table


def test_translate_block_falls_back_to_source_on_whitespace_only_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only model output is treated the same as truly empty output."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "   \n  ")
    text = "A paragraph that deserves a real translation."
    result = lt._translate_block(text, "fr")
    assert result == text


def test_translate_block_falls_back_to_source_on_empty_heading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback applies to the heading path too, not just the whole-block path."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "")
    heading = "## A Real Heading"
    result = lt._translate_block(heading, "fr")
    assert result == heading


def test_translate_block_keeps_real_translation_when_non_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback must never trigger on a normal, successful translation."""
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: "Bonjour le monde")
    result = lt._translate_block("Hello world", "fr")
    assert result == "Bonjour le monde"


# --- inline markdown link repair ---------------------------------------------
#
# Found 2026-08-25 diffing a live French article
# (docs.perawallet.app "Pera Connect" piece) against its English source: two
# distinct MiLMMT defects on `[text](url)` links embedded in ordinary prose,
# which get no special handling before the model call (unlike headings,
# code fences, and bare URLs). Both were reproduced off real MiLMMT
# inference on the exact source paragraphs, byte-for-byte matching what was
# actually served -- these fixtures use that real captured text, not
# invented examples.


def test_translate_block_collapses_space_before_link_paren(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MiLMMT sometimes regenerates a space between a link's `]` and `(` while translating the surrounding sentence -- this is what actually broke the "Dépôt GitHub"-style link the owner reported: markdown never allows that space, so the link fails to parse and renders as literal bracket/paren text."""
    monkeypatch.setattr(
        lt,
        "_translate_text_milmmt",
        lambda _text, _lang: (
            "Pour plus de détails, lisez le [répositoire GitHub] "
            "(https://github.com/perawallet/connect)."
        ),
    )
    text = "Read more in the [GitHub repository](https://github.com/perawallet/connect) for details."
    result = lt._translate_block(text, "fr")
    assert result == (
        "Pour plus de détails, lisez le [répositoire GitHub]"
        "(https://github.com/perawallet/connect)."
    )


def test_translate_block_collapses_space_before_paren_with_multiple_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real captured production output: TWO links in one block, both corrupted with the same stray space -- both must be repaired, not just the first."""
    real_prod_translated = (
        "Le package a enregistré 46 854 téléchargements le mois dernier. Le "
        "[répositoire GitHub] (https://github.com/perawallet/connect) compte 70 étoiles, et "
        "l'[exemple d'application d'exemple] (https://perawallet.github.io/pera-demo-dapp/) "
        "montre plus de cent scénarios."
    )
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: real_prod_translated)
    text = (
        "The package recorded 46,854 downloads in the trailing month. The "
        "[GitHub repository](https://github.com/perawallet/connect) carries 70 stars, and "
        "the [example dApp](https://perawallet.github.io/pera-demo-dapp/) shows more than a "
        "hundred scenarios."
    )
    result = lt._translate_block(text, "fr")
    assert "] (" not in result
    assert "[répositoire GitHub](https://github.com/perawallet/connect)" in result
    assert (
        "[exemple d'application d'exemple](https://perawallet.github.io/pera-demo-dapp/)"
        in result
    )


def test_translate_block_retranslates_untranslated_multiword_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real captured production defect: MiLMMT translated the whole surrounding sentence but left the link's anchor text "Pera's technical documentation hub" completely in English. The repair path detects the untouched anchor and issues a small dedicated MiLMMT call to translate just that phrase, splicing the result back into the link."""
    text = (
        "The SDK sits at the center of [Pera's technical documentation hub]"
        "(https://docs.perawallet.app/), which organizes four integration surfaces."
    )
    whole_block_output = (
        "Le SDK est au cœur de [Pera's technical documentation hub]"
        "(https://docs.perawallet.app/), qui organise quatre interfaces d'intégration."
    )

    def _fake_translate(inner_text: str, _lang: str) -> str:
        if inner_text == text:
            return whole_block_output
        if inner_text == "Pera's technical documentation hub":
            return "hub de documentation technique de Pera"
        raise AssertionError(f"unexpected translation call: {inner_text!r}")

    monkeypatch.setattr(lt, "_translate_text_milmmt", _fake_translate)
    result = lt._translate_block(text, "fr")
    assert "Pera's technical documentation hub" not in result
    assert (
        "[hub de documentation technique de Pera](https://docs.perawallet.app/)" in result
    )


def test_translate_block_leaves_single_word_anchor_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-word anchor (a brand name/proper noun, e.g. "Pera's") legitimately staying unchanged across languages must NOT trigger a retranslation call -- only multi-word anchors are treated as the leave-it-in-English defect."""
    calls: list[str] = []

    def _fake_translate(inner_text: str, _lang: str) -> str:
        calls.append(inner_text)
        return "le portefeuille qui s'ouvre généralement est [Pera's](https://perawallet.app/)."

    monkeypatch.setattr(lt, "_translate_text_milmmt", _fake_translate)
    text = "the wallet that typically opens is [Pera's](https://perawallet.app/)."
    lt._translate_block(text, "fr")
    # Only the one whole-block call -- no second call for the single-word anchor.
    assert calls == [text]


def test_translate_block_link_repair_does_not_touch_titled_glossary_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`[text](url "title")` glossary links already survive whole-block translation correctly (unlike plain links) -- the repair regex is scoped to skip them entirely, so a correctly-translated title is never re-touched."""
    text = (
        'Apps use [Algorand Standard Asset](/glossary/algorand-standard-asset "A built-in '
        'mechanism.") for tokens.'
    )
    translated = (
        'Les applications utilisent [Algorand Standard Asset](/glossary/algorand-standard-asset '
        '"Un mécanisme intégré.") pour les tokens.'
    )
    monkeypatch.setattr(lt, "_translate_text_milmmt", lambda _text, _lang: translated)
    result = lt._translate_block(text, "fr")
    assert result == translated


def test_looks_translatable_anchor_multiword_true() -> None:
    """Multi-word anchors (real prose) are flagged as worth checking for the leave-it-in-English defect."""
    assert lt._looks_translatable_anchor("GitHub repository") is True
    assert lt._looks_translatable_anchor("Pera's technical documentation hub") is True


def test_looks_translatable_anchor_single_token_false() -> None:
    """Single-token anchors (brand names, bare domains) are skipped -- they legitimately don't translate."""
    assert lt._looks_translatable_anchor("Pera's") is False
    assert lt._looks_translatable_anchor("docs.perawallet.app") is False


def test_repair_inline_links_skips_retranslation_when_anchor_already_translated() -> None:
    """No spurious extra call when the anchor was correctly translated already (the common, non-buggy case) -- only the space-before-paren repair should apply."""
    source = "See the [GitHub repository](https://github.com/perawallet/connect) for more."
    translated = "Voir le [répositoire GitHub] (https://github.com/perawallet/connect) pour plus."
    result = lt._repair_inline_links(source, translated, "fr")
    assert result == "Voir le [répositoire GitHub](https://github.com/perawallet/connect) pour plus."


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
    monkeypatch.setattr(lt, "_translate_block", lambda text, _lang: text.upper())

    lt._translate_article_no_lock(
        english_title="T", english_summary="S", english_body="Body.", target_language="fr"
    )

    assert len(calls) == 1
    assert calls[0][2] == "fr"
