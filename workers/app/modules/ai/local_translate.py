"""Local (on-box) translation, replacing the Mistral translation lane.

Single engine: xiaomi-research/MiLMMT-46-4B-v0.1, a Gemma3-4B continual
pretrain fine-tuned specifically for translation across 46 languages. Gemma
Terms of Use, not Apache -- read the prohibited-use policy before this
reaches a commercial product. Covers ar/fa/ru/zh/hi/es/fr; Pashto (``ps``)
does NOT go through this module -- see ``DEEPSEEK_TRANSLATE_LANGS`` below.

REMOVED 2026-08-25: this module used to also run facebook/seamless-m4t-v2-large
(engine "seq2seq") for Pashto, the one language MiLMMT doesn't cover. A
2026-08-23 side-by-side comparison confirmed SeamlessM4T broken on
markdown-heavy content: it repeatedly collapsed list/table-heavy blocks into
repetition-loop degeneration, in one case destroying every citation in a
source list outright. Production had already worked around this by routing
Pashto to DeepSeek instead (``DEEPSEEK_TRANSLATE_LANGS`` in
``app/core/config.py``, checked by ``translate_article_batch_task`` in
``publish_tasks.py`` BEFORE a language ever reaches this module) -- so
SeamlessM4T's engine path here was already dead code in production, and is
now deleted outright rather than kept as an unreachable fallback. The
two-engine "route per language" architecture (``ENGINE_FOR_LANG``,
``engine_for``, per-engine load/unload dicts, and the SeamlessM4T-only
list/table cell-splitting workaround) went with it, since none of it has a
second engine left to route between. See
docs/architecture/translation-model-survey.md for the full historical
survey record.

MiLMMT takes no system prompt or free-form instructions -- exactly ONE fixed
template, demonstrated only on single segments. All the Mistral-era prompt
engineering -- anti-calque rules, per-language glossary, digit-system
pinning, colon-label title ban, named-entity preservation -- has no home
here; it only worked because Mistral is a chat model that follows
instructions. That machinery is gone, not ported.

Markdown structure inside a block, resolved 2026-08-01 by the
promising-ranking survey (see docs/architecture/translation-model-survey.md):
headings get their ``#`` prefix stripped and reapplied outside the model call
(cheap, clearly correct), and code fences / bare URLs pass through untouched.
Lists and tables need no special handling -- MiLMMT was the ONE candidate
across the entire survey that handled both correctly fed as one whole block,
so they go through _translate_block like any other text. 59% of the corpus
has a table.

The model runs CPU-only (no GPU on dev or prod). Loaded lazily, cached
in-process, and reused across calls within a process -- see
local_translate_lock.py for why only one inference batch runs at a time
across the whole worker fleet.
"""

from __future__ import annotations

import gc
import logging
import os
import re
import threading
from collections.abc import Callable
from typing import Any

from app.modules.ai.llm_compose import split_markdown_blocks
from app.modules.ai.local_translate_lock import local_translate_lock

logger = logging.getLogger(__name__)

# transformers' default weight-loading path spins up a ThreadPoolExecutor
# (core_model_loading.py, GLOBAL_WORKERS) to load safetensors shards
# concurrently. Under free-threaded CPython (python3.15t -- see
# build_tokenizers_ft.sh), that reliably segfaults inside the interpreter
# itself partway through from_pretrained (found 2026-08-07: reproduced with
# a bare `from_pretrained` call, fully cached weights, dmesg showed the
# crash in python3.15t's own binary on a ThreadPoolExecutor thread -- a
# free-threading/thread-pool interaction bug in transformers' generic
# weight-loading path itself, not anything specific to the model being
# loaded or our code). First reproduced against SeamlessM4Tv2ForTextToText,
# but the crash site (GLOBAL_WORKERS in core_model_loading.py) is shared by
# every from_pretrained call including MiLMMT's AutoModelForCausalLM, so
# this stays in place after SeamlessM4T's removal. Forcing the documented
# synchronous fallback avoids the crash entirely; must be set before
# transformers is imported anywhere in this process.
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

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
# Caps CPU threads to leave headroom for everything else on the same box
# (Cassandra/Celery/Typesense on prod) -- hard requirement, owner 2026-07-30:
# "we do not use more than half the CPU in production."
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
# Deliberately simple: only matches a plain `[text](url)` link with no
# `"title"` portion, so it never touches the more elaborate
# `[text](url "title")` glossary-link syntax (title text included) that
# already survives whole-block translation correctly -- see
# _repair_inline_links for why that's the right scope, not an oversight.
_INLINE_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")

_load_lock = threading.Lock()
_milmmt: dict[str, Any] = {}


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


def unload_milmmt() -> None:
    """Evict the cached MiLMMT model/tokenizer so it stops counting toward resident memory.

    CPU-only, no CUDA cache to clear. ``gc.collect()`` frees the Python
    objects, but glibc malloc does not always hand pages back to the OS
    without an explicit ``malloc_trim`` -- RSS may not visibly drop even
    though the tensors are gone and their memory is available for reuse by
    the next allocation. Revisit with
    ``ctypes.CDLL("libc.so.6").malloc_trim(0)`` only if prod RSS monitoring
    shows this matters in practice; not worth the extra ctypes surface
    up front on a guess.
    """
    with _load_lock:
        if _milmmt:
            logger.info("unloading %s", _MILMMT_MODEL_ID)
            _milmmt.clear()
    gc.collect()


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


def _looks_translatable_anchor(anchor: str) -> bool:
    """True for a link anchor worth checking for the leave-it-in-English defect (see _repair_inline_links).

    Multi-word only: a single token is almost always a brand name or a bare
    domain used as its own anchor text (``Pera's``, ``docs.perawallet.app``)
    that correctly stays unchanged across languages -- retranslating those
    is how a fix like this would start breaking things that already work.
    Real prose anchors (``GitHub repository``, ``Pera's technical
    documentation hub``) are always 2+ words.
    """
    return len(anchor.split()) >= 2


def _repair_inline_links(source_text: str, translated_text: str, target_language: str) -> str:
    """Deterministic post-translation repair for two MiLMMT inline-link defects -- see module docstring: `[text](url)` gets no special handling before the model call, unlike headings/code-fences/bare URLs.

    Found 2026-08-25 diffing a live French article against its English
    source (docs.perawallet.app "Pera Connect" piece):

    1. **Broken link syntax.** MiLMMT sometimes regenerates a space between
       `]` and `(` while translating the surrounding sentence, e.g.
       ``[répositoire GitHub] (https://...)`` -- reproduced live off real
       model inference on the exact source paragraph, byte-for-byte
       matching the production defect. A markdown link is never valid with
       that space, so collapsing it can only repair syntax, never damage a
       correct link.
    2. **Untranslated anchor.** MiLMMT occasionally leaves a multi-word
       anchor's text completely untouched inside an otherwise-translated
       sentence, e.g. ``[Pera's technical documentation hub](url)``
       surviving verbatim into the French body. Detected by the source
       anchor's exact English text still being present in the model's own
       output; when found, that anchor alone is re-translated (its own
       small MiLMMT call) and spliced back in. Single-word/bare-domain
       anchors are skipped (``_looks_translatable_anchor``) since those are
       usually brand names that are correctly left alone, not a defect.

    Both signatures are read off the SOURCE block's own links (regex scoped
    to plain `[text](url)`, deliberately not matching the more elaborate
    `[text](url "title")` glossary-link syntax, which already survives
    whole-block translation correctly and isn't touched here). Best-effort
    safety net, not a guarantee -- only catches defects matching these two
    specific signatures.
    """
    translated_text = re.sub(r"\]\s+\(", "](", translated_text)
    for anchor, _url in _INLINE_LINK.findall(source_text):
        if not _looks_translatable_anchor(anchor):
            continue
        marker = f"[{anchor}]("
        if marker not in translated_text:
            continue
        fixed_anchor = _translate_text_milmmt(anchor, target_language).strip()
        if fixed_anchor and fixed_anchor != anchor:
            translated_text = translated_text.replace(marker, f"[{fixed_anchor}](", 1)
    return translated_text


def _translate_block(text: str, target_language: str) -> str:
    """Translate one block, handling the markdown-structure exceptions cheap enough to be safe (see module docstring for what is NOT yet handled).

    No list/table cell-level splitting needed -- MiLMMT handles both
    correctly fed as one whole block (proven across the full
    promising-ranking survey); see module docstring.
    """
    stripped = text.strip()
    if not stripped:
        return text
    if _CODE_FENCE.match(stripped) or _BARE_URL.match(stripped):
        return text  # never translate code or a bare link
    heading = _HEADING.match(text)
    if heading:
        prefix, content = heading.group(1), heading.group(2)
        translated_content = _translate_text_milmmt(content, target_language)
        translated = prefix + translated_content if translated_content.strip() else ""
        link_source = content
    else:
        translated = _translate_text_milmmt(text, target_language)
        link_source = text
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
            "local translation of a block returned empty (lang=%s); falling back to source text",
            target_language,
        )
        return text
    return _repair_inline_links(link_source, translated, target_language)


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
    blocks = split_markdown_blocks(english_body)

    title = _translate_block(english_title, target_language)
    summary = (
        _translate_block(english_summary, target_language) if english_summary.strip() else ""
    )
    translated_blocks = [_translate_block(b, target_language) for b in blocks]
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
    """Translate one article via MiLMMT, the local engine.

    Same block-per-call shape as the retired Mistral path, but alignment is
    now structural rather than a contract the model can violate: this calls
    the model once per source block and reassembles in order, so the output
    block count can never drift -- no retry logic needed, unlike the old
    JSON-list approach where the model chose how many blocks to return.

    Serialized via local_translate_lock -- see that module for why. This is
    the single-language entry point (used by the manual backfill script and
    any other one-off caller); for translating one article into SEVERAL
    languages, use translate_article_batch instead, which loads MiLMMT only
    once for the whole batch rather than once per language.
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
    on_language_start: Callable[[str], None] | None = None,
    on_language_done: Callable[[str, dict[str, str]], None] | None = None,
    on_language_error: Callable[[str, str], None] | None = None,
) -> dict[str, list[str] | dict[str, str]]:
    """Translate one article into every language in ``target_languages``, loading MiLMMT at most ONCE for the whole batch.

    Holds local_translate_lock() for the WHOLE batch, one acquisition, not
    once per language -- see _translate_article_no_lock for why the
    per-language calls inside must not re-acquire it. MiLMMT is unloaded
    once at the end (``finally``), not per language -- ``_load_milmmt``'s own
    idempotent caching is what keeps it loaded exactly once across every
    language in the batch.

    ``on_language_start(lang)``, ``on_language_done(lang, result)`` and
    ``on_language_error(lang, reason)`` -- if given -- fire synchronously
    right before/after each language, while the lock is still held. This is
    how a caller tracks progress incrementally (e.g. one Cassandra row per
    language, marked running -> ok/error) instead of only learning about a
    hung or crashed language after the whole batch's own multi-hour timeout
    fires. Any exception from any of them is logged and swallowed: a
    transient callback failure for one language must not skip the unload
    step or abort translation of the remaining languages.

    A single language's translation raising does NOT abort the batch --
    caught, recorded in the returned "failed" dict, and the loop continues.
    This matches the failure isolation the old one-task-per-language design
    already had: one language failing was always independent of the others.

    Returns {"ok": [...langs...], "failed": {lang: reason, ...}}.
    """
    ok: list[str] = []
    failed: dict[str, str] = {}
    with local_translate_lock():
        try:
            for lang in target_languages:
                _translate_one_language(
                    lang=lang,
                    english_title=english_title,
                    english_summary=english_summary,
                    english_body=english_body,
                    on_language_start=on_language_start,
                    on_language_done=on_language_done,
                    on_language_error=on_language_error,
                    ok=ok,
                    failed=failed,
                )
        finally:
            unload_milmmt()

    return {"ok": ok, "failed": failed}


def _translate_one_language(
    *,
    lang: str,
    english_title: str,
    english_summary: str,
    english_body: str,
    on_language_start: Callable[[str], None] | None,
    on_language_done: Callable[[str, dict[str, str]], None] | None,
    on_language_error: Callable[[str, str], None] | None,
    ok: list[str],
    failed: dict[str, str],
) -> None:
    """One language of translate_article_batch's inner loop: start callback, translate, then the done/error callback -- split out purely to keep the batch orchestrator's own cyclomatic complexity down; behavior is identical to having this inlined."""
    if on_language_start is not None:
        try:
            on_language_start(lang)
        except Exception:
            logger.error("on_language_start callback failed for lang=%s", lang, exc_info=True)
    try:
        result = _translate_article_no_lock(
            english_title=english_title,
            english_summary=english_summary,
            english_body=english_body,
            target_language=lang,
        )
    except Exception:
        logger.error("local translation failed: lang=%s", lang, exc_info=True)
        failed[lang] = "translation_error"
        if on_language_error is not None:
            try:
                on_language_error(lang, "translation_error")
            except Exception:
                logger.error(
                    "on_language_error callback failed for lang=%s", lang, exc_info=True
                )
        return
    ok.append(lang)
    if on_language_done is not None:
        try:
            on_language_done(lang, result)
        except Exception:
            logger.error("on_language_done callback failed for lang=%s", lang, exc_info=True)
