"""Local (on-box) translation, replacing the Mistral translation lane.

Two pluggable engines, chosen per target language (see ``ENGINE_FOR_LANG``):

  - "seq2seq": facebook/seamless-m4t-v2-large (``SeamlessM4Tv2ForTextToText``,
    the text-only variant -- skips the speech/vocoder weights this pipeline
    never uses). CC-BY-NC 4.0, same non-commercial restriction as NLLB.
    Pashto only, for now -- the one language with no fluent alternative at
    any size (see the FLORES eng->pbt spBLEU figures discussed earlier this
    thread).
  - "milmmt": xiaomi-research/MiLMMT-46-4B-v0.1, a Gemma3-4B continual
    pretrain fine-tuned specifically for translation across 46 languages.
    Gemma Terms of Use, not Apache -- read the prohibited-use policy before
    this reaches a commercial product. Everything except Pashto.

Neither engine takes a system prompt or free-form instructions. SeamlessM4T is
pure seq2seq (source text + language codes in, translated text out); MiLMMT
has exactly ONE fixed template, demonstrated only on single segments. All the
Mistral-era prompt engineering -- anti-calque rules, per-language glossary,
digit-system pinning, colon-label title ban, named-entity preservation -- has
no home here; it only worked because Mistral is a chat model that follows
instructions. That machinery is gone, not ported.

Markdown structure inside a block, resolved 2026-08-01 by the
promising-ranking survey (see docs/architecture/translation-model-survey.md):
headings get their ``#`` prefix stripped and reapplied outside the model call
(cheap, clearly correct) for both engines, and code fences / bare URLs pass
through untouched. For lists and tables the two engines differ for real,
not just "unproven": MiLMMT handles both correctly fed as one whole block
(the only candidate across the entire survey that did) and is left on that
path. SeamlessM4T does not -- confirmed to destroy a table outright (real
data replaced with repetition-loop degeneration, not just reformatting) and
collapse a list into one run-on line when fed either as a whole block --
so its path splits list items / table cells into isolated per-item/per-cell
model calls and reassembles the markdown structure deterministically
instead (see _translate_block, _is_list_block/_is_table_block). 59% of the
corpus has a table.

Both models run CPU-only (no GPU on dev or prod). Loaded lazily, cached
in-process, and reused within a call -- but NEVER both resident at once in
production: translate_article_batch (the multi-language entry point, used by
the Celery task) loads one engine, translates everything routed to it,
explicitly unloads, then moves to the next. Only one inference batch runs at
a time across the whole worker fleet -- see local_translate_lock.py for why.
"""

from __future__ import annotations

import gc
import logging
import os
import re
import threading
from collections.abc import Callable
from typing import Any

from app.modules.ai.local_translate_lock import local_translate_lock
from app.modules.ai.mistral_compose import split_markdown_blocks

logger = logging.getLogger(__name__)

# transformers' default weight-loading path spins up a ThreadPoolExecutor
# (core_model_loading.py, GLOBAL_WORKERS) to load safetensors shards
# concurrently. Under free-threaded CPython (python3.15t -- see
# build_tokenizers_ft.sh), that reliably segfaults inside the interpreter
# itself partway through SeamlessM4Tv2ForTextToText.from_pretrained (found
# 2026-08-07: reproduced with a bare `from_pretrained` call, fully cached
# weights, dmesg showed the crash in python3.15t's own binary on a
# ThreadPoolExecutor thread -- a free-threading/thread-pool interaction
# bug, not anything specific to this model or our code). Forcing the
# documented synchronous fallback avoids the crash entirely; must be set
# before transformers is imported anywhere in this process.
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

ENGINE_FOR_LANG: dict[str, str] = {
    "ps": "seq2seq",
}
_DEFAULT_ENGINE = "milmmt"
# Fixed load order for a multi-engine batch (see translate_article_batch).
# NOT dict-iteration order over a language->engine grouping, which would
# follow ARTICLE_TRANSLATION_LANGS' actual order (fa, ps, ar, ...) and load
# milmmt, unload it for ps's seq2seq, then reload milmmt again for the rest
# -- exactly the double-load this whole batching design exists to prevent.
_ENGINE_ORDER = ("seq2seq", "milmmt")

_SEAMLESS_MODEL_ID = "facebook/seamless-m4t-v2-large"
_SEAMLESS_LANG = {"en": "eng", "ps": "pbt"}

_MILMMT_MODEL_ID = "xiaomi-research/MiLMMT-46-4B-v0.1"
# Exact strings MiLMMT's own model card lists among its 46 supported
# languages -- not our internal display names (which carry parentheticals
# like "Spanish (Castilian)" the model was never trained to parse).
_MILMMT_LANG_NAME = {
    "en": "English",
    "ar": "Arabic",
    "fa": "Persian",
    "ru": "Russian",
    "zh": "Chinese (Simplified)",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
}
# Caps CPU threads for BOTH engines to leave headroom for everything else on
# the same box (Cassandra/Celery/Typesense on prod) -- hard requirement, owner
# 2026-07-30: "we do not use more than half the CPU in production." No
# per-engine exception -- an earlier version of this comment carved one out
# for SeamlessM4T (Pashto-only, less frequent) on the author's own reasoning,
# not something asked for; restated here unqualified for both.
#
# At 3 articles/day (budget-capped -- see workers/app/core/config.py's
# publish cadence), a translation pass across every language for one article
# comfortably fits inside the gap before the next one even at this half-core
# pace; hardware gets revisited if that cadence ever rises toward the ~24/day
# a full-throughput day would need, not before.
_MAX_THREADS = max(1, (os.cpu_count() or 2) // 2)

_HEADING = re.compile(r"^(#{1,6}\s+)(.*)$", re.DOTALL)
_CODE_FENCE = re.compile(r"^```")
_BARE_URL = re.compile(r"^https?://\S+$")

# List/table cell-level splitting -- SeamlessM4T (seq2seq) ONLY, see
# _translate_block. Ported 2026-08-01 from the eval harness
# (translation_eval.py) after the promising-ranking survey found SeamlessM4T
# destroys markdown tables outright when fed one as a whole block (real data
# replaced with repetition-loop degeneration, not just reformatting) and
# collapses lists into one run-on line -- resolving the "UNPROVEN" risk this
# module's docstring used to flag, now proven and fixed for this engine.
# MiLMMT was the ONE candidate across the entire survey that handled both
# correctly as whole blocks; deliberately NOT touched here to avoid risking
# an already-proven path for a problem it doesn't have. See
# docs/architecture/translation-model-survey.md for the full evidence.
_LIST_ITEM_SPLIT = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")
_TABLE_ROW_SPLIT = re.compile(r"^\s*\|(.*)\|\s*$")
_SEPARATOR_CELL = re.compile(r"^\s*:?-+:?\s*$")

_load_lock = threading.Lock()
_seamless: dict[str, Any] = {}
_milmmt: dict[str, Any] = {}


def engine_for(target_language: str) -> str:
    """Which engine a target language routes to. Defaults to "milmmt" for anything not explicitly mapped, so adding a language without deciding its engine still translates (just via the general-purpose model) rather than KeyError-ing at runtime."""
    return ENGINE_FOR_LANG.get(target_language, _DEFAULT_ENGINE)


def _load_seamless() -> tuple[Any, Any]:
    with _load_lock:
        if not _seamless:
            from transformers import AutoProcessor, SeamlessM4Tv2ForTextToText

            logger.info("loading %s (first use this process)", _SEAMLESS_MODEL_ID)
            _seamless["processor"] = AutoProcessor.from_pretrained(_SEAMLESS_MODEL_ID)
            _seamless["model"] = SeamlessM4Tv2ForTextToText.from_pretrained(_SEAMLESS_MODEL_ID)
        return _seamless["processor"], _seamless["model"]


def _load_milmmt() -> tuple[Any, Any]:
    with _load_lock:
        if not _milmmt:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer

            logger.info("loading %s (first use this process)", _MILMMT_MODEL_ID)
            _milmmt["tokenizer"] = AutoTokenizer.from_pretrained(_MILMMT_MODEL_ID)
            _milmmt["model"] = AutoModelForCausalLM.from_pretrained(
                _MILMMT_MODEL_ID, dtype=torch.bfloat16
            )
        return _milmmt["tokenizer"], _milmmt["model"]


def _unload_seamless() -> None:
    with _load_lock:
        if _seamless:
            logger.info("unloading %s", _SEAMLESS_MODEL_ID)
            _seamless.clear()
    gc.collect()


def _unload_milmmt() -> None:
    with _load_lock:
        if _milmmt:
            logger.info("unloading %s", _MILMMT_MODEL_ID)
            _milmmt.clear()
    gc.collect()


_UNLOAD_FOR_ENGINE: dict[str, Callable[[], None]] = {
    "seq2seq": _unload_seamless,
    "milmmt": _unload_milmmt,
}


def unload_engine(engine: str) -> None:
    """Evict the cached model/processor for ``engine`` so it stops counting toward resident memory -- this plus the fixed load order in translate_article_batch is what guarantees the two models are never both loaded at once.

    CPU-only, no CUDA cache to clear. ``gc.collect()`` frees the Python
    objects, but glibc malloc does not always hand pages back to the OS
    without an explicit ``malloc_trim`` -- RSS may not visibly drop even
    though the tensors are gone and their memory is available for reuse by
    the next allocation. Revisit with
    ``ctypes.CDLL("libc.so.6").malloc_trim(0)`` only if prod RSS monitoring
    shows this matters in practice; not worth the extra ctypes surface
    up front on a guess.
    """
    fn = _UNLOAD_FOR_ENGINE.get(engine)
    if fn is not None:
        fn()


def _translate_text_seamless(text: str, target_language: str) -> str:
    import torch

    processor, model = _load_seamless()
    inputs = processor(text=text, src_lang=_SEAMLESS_LANG["en"], return_tensors="pt")
    torch.set_num_threads(_MAX_THREADS)
    with torch.inference_mode():
        tokens = model.generate(
            **inputs, tgt_lang=_SEAMLESS_LANG[target_language], num_beams=5, max_new_tokens=512
        )[0]
    return processor.decode(tokens, skip_special_tokens=True).strip()


def _translate_text_milmmt(text: str, target_language: str) -> str:
    """Translate via MiLMMT's single fixed template -- see module docstring: this model takes no other instruction."""
    import torch

    tokenizer, model = _load_milmmt()
    src_name = _MILMMT_LANG_NAME["en"]
    tgt_name = _MILMMT_LANG_NAME[target_language]
    prompt = f"Translate this from {src_name} to {tgt_name}:\n{src_name}: {text}\n{tgt_name}:"
    inputs = tokenizer(prompt, return_tensors="pt")

    torch.set_num_threads(_MAX_THREADS)
    with torch.inference_mode():
        out = model.generate(
            **inputs,
            # Heuristic multiplier on input char length -- unvalidated; watch
            # real output for truncation on long blocks and raise this if so.
            max_new_tokens=max(256, int(len(text) * 1.6)),
            do_sample=False,
        )
    generated = out[0][inputs["input_ids"].shape[1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def _is_list_block(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    return bool(lines) and all(_LIST_ITEM_SPLIT.match(line) for line in lines)


def _is_table_block(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    return bool(lines) and all(_TABLE_ROW_SPLIT.match(line) for line in lines)


def _translate_list_block_seamless(text: str, target_language: str) -> str:
    """Translate each list item's text in isolation, reassembling with its original bullet/number prefix -- same principle as heading handling, applied per item."""
    out_lines = []
    for line in text.split("\n"):
        match = _LIST_ITEM_SPLIT.match(line)
        if not match or not match.group(2).strip():
            out_lines.append(line)
            continue
        prefix, content = match.group(1), match.group(2)
        out_lines.append(prefix + _translate_text_seamless(content, target_language))
    return "\n".join(out_lines)


def _translate_table_block_seamless(text: str, target_language: str) -> str:
    """Translate each table cell's text in isolation, reassembling the row -- the separator row (all-dashes cells) passes through unchanged, never sent to the model."""
    out_lines = []
    for line in text.split("\n"):
        match = _TABLE_ROW_SPLIT.match(line)
        if not match:
            out_lines.append(line)
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if all(_SEPARATOR_CELL.match(c) for c in cells):
            out_lines.append(line)
            continue
        translated_cells = [_translate_text_seamless(c, target_language) if c else c for c in cells]
        out_lines.append("| " + " | ".join(translated_cells) + " |")
    return "\n".join(out_lines)


def _translate_block(text: str, target_language: str, engine: str) -> str:
    """Translate one block, handling the markdown-structure exceptions cheap enough to be safe (see module docstring for what is NOT yet handled).

    List/table cell-level splitting is seq2seq (SeamlessM4T) ONLY -- MiLMMT
    handles both correctly as a whole block (proven across the full
    promising-ranking survey) and is deliberately left on its original path.
    """
    stripped = text.strip()
    if not stripped:
        return text
    if _CODE_FENCE.match(stripped) or _BARE_URL.match(stripped):
        return text  # never translate code or a bare link
    heading = _HEADING.match(text)
    translate_fn = _translate_text_seamless if engine == "seq2seq" else _translate_text_milmmt
    if heading:
        prefix, content = heading.group(1), heading.group(2)
        translated_content = translate_fn(content, target_language)
        translated = prefix + translated_content if translated_content.strip() else ""
    elif engine == "seq2seq" and _is_table_block(text):
        translated = _translate_table_block_seamless(text, target_language)
    elif engine == "seq2seq" and _is_list_block(text):
        translated = _translate_list_block_seamless(text, target_language)
    else:
        translated = translate_fn(text, target_language)
    if not translated.strip():
        # The engine returned nothing for a non-empty source block. Silently
        # keeping that empty string here would drop the block wholesale --
        # translated_blocks are just joined by position in
        # _translate_article_no_lock, so an empty entry vanishes from the
        # published body with nothing to show it was ever there. Found
        # 2026-08-04: a whole 3-row table (explaining forward/reverse
        # resolution and vaults) disappeared from a French NFDomains
        # translation this way, published with no error and no trace in any
        # log. Falling back to the source text keeps the content intact --
        # a stray English block inside an otherwise-translated article is a
        # visible, known degradation; a silently missing table is not.
        logger.warning(
            "local translation of a block returned empty (lang=%s engine=%s); falling back to source text",
            target_language,
            engine,
        )
        return text
    return translated


def _log_alignment_findings(english_body: str, translated_body: str, target_language: str) -> None:
    """Best-effort observability: log (never raise, never block) any structural/digit drift found in a completed translation.

    translation_eval.py's structural_alignment/digit_consistency checks
    previously existed only in the offline eval harness -- real production
    defects (a dropped citation link, a scrambled list item) were only ever
    found by an ad-hoc manual audit, invisible otherwise. This does not fix
    or reject anything; it exists so a real defect shows up in logs instead
    of requiring another manual audit to notice. Lazy import: translation_eval
    imports from this module at its own top level, so a top-level import here
    would be circular.
    """
    try:
        from app.modules.ai.translation_eval import digit_consistency, structural_alignment

        structure = structural_alignment(english_body, translated_body)
        digits = digit_consistency(english_body, translated_body)
        problems = []
        if not structure.block_count_matches:
            problems.append("block count mismatch")
        if structure.row_diffs:
            problems.append(f"{len(structure.row_diffs)} row-count diff(s)")
        if digits.ungrounded:
            problems.append(f"{len(digits.ungrounded)} ungrounded digit(s)")
        if problems:
            logger.warning(
                "translation alignment findings lang=%s: %s (read-only signal, not enforced -- "
                "some findings are known false positives, e.g. reformatted currency figures)",
                target_language,
                "; ".join(problems),
            )
    except Exception:
        logger.warning("alignment-findings check itself failed (fail-open)", exc_info=True)


def _translate_article_no_lock(
    *,
    english_title: str,
    english_summary: str,
    english_body: str,
    target_language: str,
) -> dict[str, str]:
    """Same as translate_article_local, minus the lock acquisition.

    Callers that already hold local_translate_lock() for a wider scope (the
    batch orchestrator below) call this directly instead of
    translate_article_local -- the lock is a plain Redis SETNX mutex, not
    reentrant, so a second acquire() from inside an already-held batch would
    incorrectly raise LocalTranslateBusyError against itself.
    """
    engine = engine_for(target_language)
    blocks = split_markdown_blocks(english_body)

    title = _translate_block(english_title, target_language, engine)
    summary = (
        _translate_block(english_summary, target_language, engine)
        if english_summary.strip()
        else ""
    )
    translated_blocks = [_translate_block(b, target_language, engine) for b in blocks]
    translated_body = "\n\n".join(translated_blocks).strip() or english_body

    _log_alignment_findings(english_body, translated_body, target_language)

    return {
        "title": title.strip() or english_title,
        "summary": summary.strip() or english_summary,
        "body": translated_body,
    }


def translate_article_local(
    *,
    english_title: str,
    english_summary: str,
    english_body: str,
    target_language: str,
) -> dict[str, str]:
    """Translate one article via the local engine mapped to ``target_language``.

    Same block-per-call shape as the retired Mistral path, but alignment is
    now structural rather than a contract the model can violate: this calls
    the model once per source block and reassembles in order, so the output
    block count can never drift -- no retry logic needed, unlike the old
    JSON-list approach where the model chose how many blocks to return.

    Serialized via local_translate_lock -- see that module for why. This is
    the single-language entry point (used by the manual backfill script and
    any other one-off caller); for translating one article into SEVERAL
    languages, use translate_article_batch instead, which loads each engine
    only once for the whole batch rather than once per language.
    """
    with local_translate_lock():
        return _translate_article_no_lock(
            english_title=english_title,
            english_summary=english_summary,
            english_body=english_body,
            target_language=target_language,
        )


def translate_article_batch(
    *,
    english_title: str,
    english_summary: str,
    english_body: str,
    target_languages: list[str],
    on_language_done: Callable[[str, dict[str, str]], None] | None = None,
) -> dict[str, list[str] | dict[str, str]]:
    """Translate one article into every language in ``target_languages``, loading each engine's model at most ONCE for the whole batch.

    Groups languages by engine_for(lang) and processes each engine group in
    the fixed _ENGINE_ORDER: load, translate every language in that group
    reusing the one loaded instance, unload before moving to the next group.
    Never both engines resident at once -- this is the actual production
    memory guarantee, not just a CPU one.

    Holds local_translate_lock() for the WHOLE batch, one acquisition, not
    once per language -- see _translate_article_no_lock for why the
    per-language calls inside must not re-acquire it.

    ``on_language_done(lang, result)``, if given, is called synchronously
    right after each language finishes, while the lock is still held -- this
    is how a caller persists incrementally (e.g. one Cassandra write per
    language) instead of buffering every result to the end of a batch that
    can run for hours. Any exception from it is logged and swallowed: a
    transient write failure for one language must not skip that engine's
    unload step or abort translation of the remaining languages.

    A single language's translation raising does NOT abort the batch --
    caught, recorded in the returned "failed" dict, and the loop continues.
    This matches the failure isolation the old one-task-per-language design
    already had: one language failing was always independent of the others,
    and grouping by engine is a scheduling change, not a blast-radius one.

    Returns {"ok": [...langs...], "failed": {lang: reason, ...}}.
    """
    groups: dict[str, list[str]] = {}
    for lang in target_languages:
        groups.setdefault(engine_for(lang), []).append(lang)

    ok: list[str] = []
    failed: dict[str, str] = {}
    with local_translate_lock():
        for engine in _ENGINE_ORDER:
            langs = groups.get(engine)
            if not langs:
                continue
            try:
                for lang in langs:
                    try:
                        result = _translate_article_no_lock(
                            english_title=english_title,
                            english_summary=english_summary,
                            english_body=english_body,
                            target_language=lang,
                        )
                    except Exception:
                        logger.error(
                            "local translation failed: lang=%s engine=%s",
                            lang,
                            engine,
                            exc_info=True,
                        )
                        failed[lang] = "translation_error"
                        continue
                    ok.append(lang)
                    if on_language_done is not None:
                        try:
                            on_language_done(lang, result)
                        except Exception:
                            logger.error(
                                "on_language_done callback failed for lang=%s",
                                lang,
                                exc_info=True,
                            )
            finally:
                unload_engine(engine)

    return {"ok": ok, "failed": failed}
