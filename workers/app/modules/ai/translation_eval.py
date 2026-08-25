"""Reusable checks + candidate model registry for the translation "promising-ranking" survey.

Built after a real, confirmed defect: MiLMMT rendered "agent(s)" as عاملان
(correct) in a Farsi article's title but آژانس‌های ("agencies", wrong) in its
body -- a term-consistency drift invisible to anyone here who doesn't read
Farsi. External literature (WMT, FLORES leaderboards, model self-reports)
turned out to have no reliable, cross-comparable per-language answer for
"which model is best" -- see the plan this module implements. This is a
TRIAGE tool, not a certification: it answers "how much fixing engineering
does each language need" using only automatable, language-agnostic signals.
Fluency ("is this well-written") is explicitly out of scope for automation --
nobody in-house reads Pashto, Farsi, Arabic, Russian, or Chinese fluently
enough to judge it, and nothing here pretends otherwise. Every check below
produces a SIGNAL to read, not a pass/fail gate -- same posture as
scripts/eval_compose_prompts.py.

Two layers, both language-agnostic:

  - Layer 1 (structural): digit_consistency, structural_alignment. No model
    call, no language knowledge needed.
  - Layer 2 (semantic adequacy via back-translation): dominant_term +
    back_translation_consistency. Translate a candidate's output back to
    English and check the source's dominant repeated word survives the round
    trip -- this is how the agent/agency defect gets caught without reading
    Farsi.

Layer 3 (fluency) has no function here on purpose.

The Candidate registry below is a SEPARATE, offline evaluation-only surface
-- it never touches app.modules.ai.local_translate's production entry points
except to reuse its two loaded baseline models directly (same cache, same
_MAX_THREADS cap). local_translate.py stays exactly what it is: two fixed
engines, a hard never-both-resident guarantee, a production-sized Redis
lock. This module does NOT take local_translate_lock() -- that lock is
keyed/sized for production batch traffic. Run eval scripts that use this
registry by hand, off-peak, the same convention scripts/eval_compose_prompts.py
already uses for its own cost reasons -- never automatically, never in CI,
and never while a real translation batch might be in flight.
"""

from __future__ import annotations

import gc
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.modules.ai import local_translate as lt
from app.modules.ai.llm_compose import split_markdown_blocks
from app.modules.ai.local_translate import _MAX_THREADS
from app.modules.gatekeeper.fact_align import EntailmentResult, numeric_entailment_score

# ---------------------------------------------------------------------------
# Layer 1: structural checks
# ---------------------------------------------------------------------------


_THOUSANDS_GROUP_SEP = re.compile(r"(?<=\d)[.,\s](?=\d{3}(?:[.,\s]\d{3})*(?:\D|$))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def _normalize_numeral_punctuation(text: str) -> str:
    """Collapse European-style number punctuation (comma/period/space thousands grouping, comma decimals) to the English convention extract_numbers() already parses, without touching the digit VALUES.

    Found live 2026-07-31 running the eval harness on real MiLMMT French/
    Spanish output: "1.4M" came back as "1,4 M" (comma decimal) and
    "900,000" as "900 000" (French, space-grouped) or "900.000" (Spanish,
    period-grouped) -- all correct in their own language, all unparseable
    by fact_align's US/UK-formatted _NUM_RE, which shattered each into 2-3
    garbage "ungrounded" fragments. A thousands separator is only ever
    followed by an exact 3-digit group (then either another such group or
    the end of the number); a decimal separator never is -- that
    distinction, not knowing which language it is, is what lets this stay
    locale-agnostic across all 8 target languages rather than hardcoding a
    per-language rule.
    """
    text = _THOUSANDS_GROUP_SEP.sub("", text)
    return _DECIMAL_COMMA.sub(".", text)


def digit_consistency(
    source_text: str, translated_text: str, *, tol: float = 0.02
) -> EntailmentResult:
    r"""Do the numeric values in ``translated_text`` match ``source_text``'s?

    Thin wrapper over the gatekeeper's own numeric_entailment_score
    (app.modules.gatekeeper.fact_align) -- reused as-is, not reimplemented.
    No digit-glyph normalization step needed: Python's `\\d` and `float()`
    already parse Extended Arabic-Indic (۰-۹), Arabic-Indic (٠-٩), and
    Devanagari (०-९) digits natively (verified 2026-07-31 -- `float("۱۲۳")
    == 123.0`), so extract_numbers() reads a translated "۱۲۳" the same as a
    source "123". Both texts pass through ``_normalize_numeral_punctuation``
    first (see its docstring) so European thousands/decimal punctuation
    doesn't shatter into false "ungrounded" fragments.

    Known gap: unit-class markers ($, %, "million"/"billion",
    "bytes"/"bits") are English/Latin-script pattern matches only -- a
    translated percent SIGN (e.g. Arabic ٪) or magnitude WORD won't be
    recognized as a suffix and falls back to the "plain" class, which is
    compatible with "currency"/"bytes" but NOT "percent"/"bits"/"multiplier".
    This can produce a false-positive drift report for those three classes;
    read the ``ungrounded`` list before concluding a real defect.
    """
    return numeric_entailment_score(
        _normalize_numeral_punctuation(source_text),
        _normalize_numeral_punctuation(translated_text),
        tol=tol,
    )


_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def list_table_row_count(block: str) -> tuple[int, int]:
    """(list-item lines, table-row lines) in one markdown block, skipping fenced code.

    Targets the open, previously-flagged risk in local_translate.py's own
    docstring: list items and table rows are fed to a model as plain text
    inside one block, with no evidence either engine preserves them.
    """
    list_items = 0
    table_rows = 0
    in_fence = False
    for line in block.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if _TABLE_ROW.match(line):
            table_rows += 1
        elif _LIST_ITEM.match(line):
            list_items += 1
    return list_items, table_rows


@dataclass(frozen=True)
class RowCountDiff:
    """One block where list-item or table-row count didn't survive translation."""

    block_index: int
    kind: str  # "list" | "table"
    source_count: int
    translated_count: int


@dataclass(frozen=True)
class StructuralResult:
    """Block-count alignment + per-block list/table row-count drift."""

    source_blocks: int
    translated_blocks: int
    block_count_matches: bool
    row_diffs: tuple[RowCountDiff, ...]


def structural_alignment(source_body: str, translated_body: str) -> StructuralResult:
    """Block-count alignment (reuses llm_compose.split_markdown_blocks) plus per-block list/table row-count drift.

    Row-count comparison only runs for blocks that exist on both sides --
    a block-count mismatch is already surfaced via ``block_count_matches``
    and is a more serious signal than any individual row diff.
    """
    src_blocks = split_markdown_blocks(source_body)
    tgt_blocks = split_markdown_blocks(translated_body)
    diffs: list[RowCountDiff] = []
    for i, src in enumerate(src_blocks):
        if i >= len(tgt_blocks):
            break
        s_list, s_rows = list_table_row_count(src)
        t_list, t_rows = list_table_row_count(tgt_blocks[i])
        if s_list != t_list:
            diffs.append(RowCountDiff(i, "list", s_list, t_list))
        if s_rows != t_rows:
            diffs.append(RowCountDiff(i, "table", s_rows, t_rows))
    return StructuralResult(
        source_blocks=len(src_blocks),
        translated_blocks=len(tgt_blocks),
        block_count_matches=len(src_blocks) == len(tgt_blocks),
        row_diffs=tuple(diffs),
    )


# ---------------------------------------------------------------------------
# Layer 2: back-translation semantic adequacy
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "by",
        "at",
        "from",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "their",
        "they",
        "he",
        "she",
        "his",
        "her",
        "we",
        "our",
        "you",
        "your",
        "i",
        "not",
        "no",
        "do",
        "does",
        "did",
        "has",
        "have",
        "had",
        "will",
        "would",
        "can",
        "could",
        "should",
        "may",
        "might",
        "which",
        "who",
        "whom",
        "what",
        "when",
        "where",
        "why",
        "how",
        "there",
        "here",
        "than",
        "then",
        "so",
        "if",
        "into",
        "about",
        "over",
        "after",
        "before",
        "up",
        "down",
        "out",
        "off",
        "also",
        "more",
        "most",
        "some",
        "such",
        "only",
        "own",
        "same",
        "new",
        "one",
        "two",
    }
)
_WORD = re.compile(r"[A-Za-z]{3,}")


def dominant_term(text: str) -> str:
    """Best-guess dominant content word in ``text`` -- lowercase, stopword-stripped, crude trailing-s plural fold.

    Approximate on purpose: this is a triage signal, not an NLP pipeline.
    Returns "" for text with no qualifying word.
    """
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for m in _WORD.finditer(text):
        word = m.group(0).lower()
        if word in _STOPWORDS:
            continue
        stem = word[:-1] if word.endswith("s") and len(word) > 4 else word
        counts[stem] = counts.get(stem, 0) + 1
        display.setdefault(stem, word)
    if not counts:
        return ""
    top = max(counts, key=lambda k: counts[k])
    return display[top]


@dataclass(frozen=True)
class BackTransResult:
    """Whether a source term survived a forward+back translation round trip."""

    term: str
    blocks_checked: int
    blocks_consistent: int
    drifted_block_indices: tuple[int, ...]
    consistency: float  # in [0, 1]; 1.0 when blocks_checked == 0 (vacuous)


def back_translation_consistency(
    source_blocks: list[str],
    back_translated_blocks: list[str],
    term: str,
    *,
    synonyms: tuple[str, ...] = (),
) -> BackTransResult:
    """Does ``term`` (or a listed synonym) survive a forward+back translation round trip, block by block?

    Checks only blocks where the SOURCE contained ``term`` -- a
    back-translated block is free to phrase things differently elsewhere.
    This is how the agent/agency defect (local_translate.py, MiLMMT,
    2026-07) gets caught without reading the intermediate language: the term
    held correctly in one block but drifted to a near-synonym in another --
    exactly the class of drift this function flags.

    Approximate substring matching, case-insensitive. A real semantic
    paraphrase that keeps the MEANING but not this literal word/synonym list
    reads as a false drift -- read flagged blocks yourself before concluding
    a candidate is bad, per this module's "signals, not a gate" posture.
    """
    accepted = {term.lower(), *(s.lower() for s in synonyms)}
    checked = 0
    drifted: list[int] = []
    for i, src in enumerate(source_blocks):
        if term.lower() not in src.lower():
            continue
        checked += 1
        back = back_translated_blocks[i].lower() if i < len(back_translated_blocks) else ""
        if not any(word in back for word in accepted):
            drifted.append(i)
    consistency = 1.0 if checked == 0 else (checked - len(drifted)) / checked
    return BackTransResult(
        term=term,
        blocks_checked=checked,
        blocks_consistent=checked - len(drifted),
        drifted_block_indices=tuple(drifted),
        consistency=consistency,
    )


# ---------------------------------------------------------------------------
# Candidate registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One translation engine/model entry in an eval run.

    ``translate_fn(text, src_lang, tgt_lang, sample=False) -> str`` is a raw,
    block-level primitive -- no markdown-structure awareness of its own. Use
    ``translate_block_with`` to apply the same heading/code-fence/bare-URL
    pass-throughs local_translate._translate_block uses, so every candidate
    in a comparison gets identical non-model-specific treatment. The SAME
    function handles both directions (back-translation calls it with
    src/tgt swapped) -- every loader below is written to support that.

    ``sample=True`` requests the sampling-variance run mode (see the plan):
    both current production engines decode deterministically by default
    (MiLMMT do_sample=False, SeamlessM4T fixed beam search), so a naive
    "run N times" test would trivially report 100% consistency and prove
    nothing. Every candidate except SeamlessM4T honors it by switching to
    temperature sampling; SeamlessM4T's beam search
    has no simple sampling equivalent and ignores the flag -- see
    _seamless_translate.
    """

    name: str
    license: str
    translate_fn: Callable[..., str]
    unload_fn: Callable[[], None]


_HEADING = re.compile(r"^(#{1,6}\s+)(.*)$", re.DOTALL)
_CODE_FENCE = re.compile(r"^```")
_BARE_URL = re.compile(r"^https?://\S+$")

# Fixed, moderate temperature for sampling-variance runs -- not tuned, just
# high enough to actually vary output across runs without degenerating into
# nonsense. Revisit if early runs show it's too tame or too wild.
_SAMPLE_TEMPERATURE = 0.7

# --- list/table structural splitting -----------------------------------
# Motivated by the 2026-07-31 survey run: every dedicated seq2seq candidate
# (M2M-100, OPUS-MT, SeamlessM4T -- everything except MiLMMT) destroyed
# markdown tables outright (repetition-loop degeneration, actual data loss)
# and collapsed lists into one run-on line, because none of them were
# trained on anything that looks like markdown table/list syntax -- it's
# far out of distribution for a model trained on sentence-level parallel
# corpora. The fix mirrors how headings are already handled just above:
# strip the markdown syntax, translate ONLY the isolated text content (the
# short-phrase input these models were actually trained on), reassemble
# the structure ourselves outside the model call. This makes a
# structural_mismatch/row_diff impossible by construction for these two
# cases, since the harness controls the reassembly, not the model.
#
# Scoped to BLOCK-level structural units (list items, table cells) only --
# never inline spans (bold/italic/links inside a sentence). Splitting
# there would fragment a natural sentence mid-thought for no evidence-based
# benefit; nothing in the survey showed inline-span corruption.
#
# Eval-harness only, by design (2026-07-31) -- NOT ported into
# local_translate.py's production _translate_block. MiLMMT (production's
# other engine) never needed this fix; SeamlessM4T (production's live
# Pashto engine) showed the identical defect in this survey and is a real,
# separate follow-up, not something silently fixed here.

_LIST_ITEM_SPLIT = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(.*)$")
_TABLE_ROW_SPLIT = re.compile(r"^\s*\|(.*)\|\s*$")
_SEPARATOR_CELL = re.compile(r"^\s*:?-+:?\s*$")


def _is_list_block(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    return bool(lines) and all(_LIST_ITEM_SPLIT.match(line) for line in lines)


def _is_table_block(text: str) -> bool:
    lines = [line for line in text.split("\n") if line.strip()]
    return bool(lines) and all(_TABLE_ROW_SPLIT.match(line) for line in lines)


def _translate_list_block(
    candidate: Candidate, text: str, src_lang: str, tgt_lang: str, *, sample: bool
) -> str:
    """Translate each list item's text in isolation, reassembling with its original bullet/number prefix -- same principle as heading handling, applied per item instead of once per block."""
    out_lines = []
    for line in text.split("\n"):
        match = _LIST_ITEM_SPLIT.match(line)
        if not match or not match.group(2).strip():
            out_lines.append(line)
            continue
        prefix, content = match.group(1), match.group(2)
        out_lines.append(
            prefix + candidate.translate_fn(content, src_lang, tgt_lang, sample=sample)
        )
    return "\n".join(out_lines)


def _translate_table_block(
    candidate: Candidate, text: str, src_lang: str, tgt_lang: str, *, sample: bool
) -> str:
    """Translate each table cell's text in isolation, reassembling the row ourselves -- the separator row (all-dashes cells) passes through unchanged, never sent to the model."""
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
        translated_cells = [
            candidate.translate_fn(c, src_lang, tgt_lang, sample=sample) if c else c for c in cells
        ]
        out_lines.append("| " + " | ".join(translated_cells) + " |")
    return "\n".join(out_lines)


def translate_block_with(
    candidate: Candidate, text: str, src_lang: str, tgt_lang: str, *, sample: bool = False
) -> str:
    """Apply one candidate's translate_fn to one markdown block, mirroring local_translate._translate_block's structural pass-throughs (code fences, bare URLs, heading prefix stripped/reapplied outside the model call) plus this module's own list/table cell-level splitting (see the comment above)."""
    stripped = text.strip()
    if not stripped:
        return text
    if _CODE_FENCE.match(stripped) or _BARE_URL.match(stripped):
        return text
    heading = _HEADING.match(text)
    if heading:
        prefix, content = heading.group(1), heading.group(2)
        return prefix + candidate.translate_fn(content, src_lang, tgt_lang, sample=sample)
    if _is_table_block(text):
        return _translate_table_block(candidate, text, src_lang, tgt_lang, sample=sample)
    if _is_list_block(text):
        return _translate_list_block(candidate, text, src_lang, tgt_lang, sample=sample)
    return candidate.translate_fn(text, src_lang, tgt_lang, sample=sample)


# --- generic loader cache for every non-baseline (third-party) candidate ---
# A single slot map keyed by HF model id, separate from local_translate.py's
# own two-engine cache. Baseline candidates (MiLMMT, SeamlessM4T) reuse
# local_translate's cache/loaders directly instead -- see below.

_loaded: dict[str, dict[str, Any]] = {}
_load_lock = threading.Lock()


def _load(
    model_id: str,
    model_cls: Any,  # noqa: ANN401 -- one of several unrelated transformers model classes, no single static type
    tokenizer_cls: Any,  # noqa: ANN401 -- matching tokenizer class for model_cls
    *,
    trust_remote_code: bool = False,
) -> tuple[Any, Any]:
    """``trust_remote_code`` executes arbitrary Python shipped in the model repo (needed for architectures transformers doesn't have a built-in class for, e.g. Jais's custom attention/positional-encoding code) -- defaults False for every loader here, and is opted into per-candidate below with a comment explaining why that specific repo needs it, never as a blanket default."""
    with _load_lock:
        if model_id not in _loaded:
            import logging

            logging.getLogger(__name__).info("loading %s (eval harness, first use)", model_id)
            _loaded[model_id] = {
                "tokenizer": tokenizer_cls.from_pretrained(
                    model_id, trust_remote_code=trust_remote_code
                ),
                "model": model_cls.from_pretrained(model_id, trust_remote_code=trust_remote_code),
            }
        entry = _loaded[model_id]
        return entry["tokenizer"], entry["model"]


def unload_all() -> None:
    """Evict every third-party candidate model currently cached here. Called after each candidate finishes in the runner -- same never-both-resident discipline local_translate.py enforces for its own two engines, applied to however many model ids this candidate populated (one for M2M-100, up to two for an OPUS-MT pair used for both forward and back-translation)."""
    with _load_lock:
        _loaded.clear()
    gc.collect()


# --- M2M-100 (facebook/m2m100_1.2B, MIT license, confirmed via HF API 2026-07-31) ---
# Single multilingual model, covers all 8 of our languages including ps/fa.
# Assumes M2M-100's own language codes match ours 1:1 (both are plain ISO
# 639-1 like "fr"/"es"/"zh") -- not independently verified per-language;
# a KeyError from tokenizer.get_lang_id means that assumption broke for
# that language and needs checking against the tokenizer's own lang list.

_M2M100_MODEL_ID = "facebook/m2m100_1.2B"


def _m2m100_translate(text: str, src_lang: str, tgt_lang: str, *, sample: bool = False) -> str:
    import torch
    from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

    tokenizer, model = _load(_M2M100_MODEL_ID, M2M100ForConditionalGeneration, M2M100Tokenizer)
    tokenizer.src_lang = src_lang
    inputs = tokenizer(text, return_tensors="pt")
    torch.set_num_threads(_MAX_THREADS)
    gen_kwargs: dict[str, object] = (
        {"do_sample": True, "temperature": _SAMPLE_TEMPERATURE} if sample else {}
    )
    with torch.inference_mode():
        tokens = model.generate(
            **inputs,
            forced_bos_token_id=tokenizer.get_lang_id(tgt_lang),
            max_new_tokens=512,
            **gen_kwargs,
        )
    return tokenizer.batch_decode(tokens, skip_special_tokens=True)[0].strip()


# --- OPUS-MT (Helsinki-NLP/opus-mt-{src}-{tgt}), per-language-pair models ---
# License confirmed via HF API 2026-07-31, per pair: en-ar/ar-en apache-2.0,
# en-ru apache-2.0 / ru-en cc-by-4.0, en-zh apache-2.0 / zh-en cc-by-4.0,
# en-hi/hi-en apache-2.0, en-es/es-en apache-2.0, en-fr/fr-en apache-2.0.
#
# en-fa and en-ps do NOT exist as dedicated pairs -- resolved 2026-07-31.
# The 401 initially looked like an access restriction (even served an AWS
# WAF bot-challenge page on the plain webpage fetch), but that was a red
# herring: the real `huggingface_hub` client returns a clean
# RepositoryNotFoundError, and neither repo appears anywhere in a full
# listing of all 1563 Helsinki-NLP repos. Helsinki-NLP consolidated small
# pairs like these into grouped multi-target models instead -- see
# _opus_mt_ine_translate below, which covers both languages via a different
# repo pair entirely.


def _opus_mt_translate(text: str, src_lang: str, tgt_lang: str, *, sample: bool = False) -> str:
    from transformers import MarianMTModel, MarianTokenizer

    model_id = f"Helsinki-NLP/opus-mt-{src_lang}-{tgt_lang}"
    import torch

    tokenizer, model = _load(model_id, MarianMTModel, MarianTokenizer)
    inputs = tokenizer(text, return_tensors="pt", truncation=True)
    torch.set_num_threads(_MAX_THREADS)
    gen_kwargs: dict[str, object] = (
        {"do_sample": True, "temperature": _SAMPLE_TEMPERATURE} if sample else {}
    )
    with torch.inference_mode():
        tokens = model.generate(**inputs, max_new_tokens=512, **gen_kwargs)
    return tokenizer.batch_decode(tokens, skip_special_tokens=True)[0].strip()


def _opus_mt_candidate(lang: str) -> Candidate:
    return Candidate(
        name=f"opus-mt-en-{lang}",
        license="Apache-2.0 or CC-BY-4.0 (both commercial-safe; see per-pair comment above)",
        translate_fn=_opus_mt_translate,
        unload_fn=unload_all,
    )


# --- OPUS-MT grouped Indo-European models (Helsinki-NLP/opus-mt-en-ine /
# opus-mt-ine-en), covering fa and ps where no dedicated pair exists ---
# Both Apache-2.0, confirmed 2026-07-31. Target language on the en->X
# direction is selected with a `>>id<<` prefix token on the input text
# (OPUS-MT's standard convention for grouped multi-target models) rather
# than a separate repo per language; the X->en direction needs no prefix
# (many-to-one). Smoke-tested 2026-07-31 with real, non-empty, correct-
# script output for both `pes` (Farsi) and `pus` (Pashto) -- whether the
# TRANSLATION QUALITY is any good is exactly what this harness exists to
# find out, not something to assume from a two-sentence manual check.

_OPUS_MT_INE_LANG = {"fa": "pes", "ps": "pus"}


def _opus_mt_ine_translate(text: str, src_lang: str, tgt_lang: str, *, sample: bool = False) -> str:
    import torch
    from transformers import MarianMTModel, MarianTokenizer

    if tgt_lang == "en":
        assert src_lang in _OPUS_MT_INE_LANG, f"opus-mt-ine-en has no coverage for {src_lang!r}"
        model_id = "Helsinki-NLP/opus-mt-ine-en"
        prefixed = text  # many-to-one -- no source-language prefix needed
    else:
        assert src_lang == "en", f"opus-mt-en-ine only translates FROM English, got {src_lang!r}"
        model_id = "Helsinki-NLP/opus-mt-en-ine"
        prefixed = f">>{_OPUS_MT_INE_LANG[tgt_lang]}<< {text}"

    tokenizer, model = _load(model_id, MarianMTModel, MarianTokenizer)
    inputs = tokenizer(prefixed, return_tensors="pt", truncation=True)
    torch.set_num_threads(_MAX_THREADS)
    gen_kwargs: dict[str, object] = (
        {"do_sample": True, "temperature": _SAMPLE_TEMPERATURE} if sample else {}
    )
    with torch.inference_mode():
        tokens = model.generate(**inputs, max_new_tokens=512, **gen_kwargs)
    return tokenizer.batch_decode(tokens, skip_special_tokens=True)[0].strip()


def _opus_mt_ine_candidate(lang: str) -> Candidate:
    return Candidate(
        name=f"opus-mt-ine-{lang}",
        license="Apache-2.0",
        translate_fn=_opus_mt_ine_translate,
        unload_fn=unload_all,
    )


_M2M100 = Candidate(
    name="m2m100-1.2b",
    license="MIT",
    translate_fn=_m2m100_translate,
    unload_fn=unload_all,
)

# MADLAD-400-3B-MT (google/madlad400-3b-mt) was tried and dropped 2026-08-01:
# despite Apache-2.0 licensing and covering all 8 survey languages, the
# checkpoint itself produces degenerate garbage output -- confirmed with the
# EXACT documented example from Google/jbochi's own README ("<2pt> I love
# pizza!" -> should be "Eu adoro pizza!"), which instead returned the string
# "1000000000000000000". A load-time warning ("tied weights mapping...
# present in the checkpoints with different values, so we will NOT tie
# them") strongly suggests the output embedding layer is desynced from the
# trained weights in this specific converted checkpoint -- not a bug in our
# loader or prompt, the model card's own usage example fails identically.
# Not retried at a different revision/quantization; may be worth revisiting
# if the repo is ever re-converted.

# --- Tencent Hy-MT2-1.8B (apache-2.0, verified 2026-07-31 by reading the
# actual LICENSE.txt line by line, not just the metadata tag -- the
# PREVIOUS Hunyuan-MT generation was ruled out earlier this session for a
# real EU/UK/South-Korea territorial exclusion clause in its Community
# License, so this one was not taken on trust). A newer generation
# (released 2026-05-21, part of an active WMT26 partnership -- a real,
# maintained release, not a derivative) that appears to have genuinely
# moved to a clean Apache 2.0 license. Covers 7 of our 8 languages --
# confirmed via its own tag list, but Pashto is NOT among them, unlike
# MADLAD-400. Instruction-tuned chat model (not a raw seq2seq translator
# like the others here): uses tokenizer.apply_chat_template with an
# English-language-name prompt, per Tencent's own documented usage.

_HYMT2_MODEL_ID = "tencent/Hy-MT2-1.8B"
_HYMT2_LANG_NAME = {
    "en": "English",
    "fr": "French",
    "es": "Spanish",
    "ar": "Arabic",
    "ru": "Russian",
    "zh": "Chinese",
    "hi": "Hindi",
    "fa": "Persian",
}


def _hymt2_translate(
    text: str,
    src_lang: str,  # noqa: ARG001 -- Hy-MT2's documented prompt only names the target language
    tgt_lang: str,
    *,
    sample: bool = False,
) -> str:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # trust_remote_code=True: Hy-MT2 ships custom modeling code, same
    # rationale as Jais -- scoped to this one candidate, publisher (Tencent,
    # via an active WMT26 partnership) is the trust basis.
    tokenizer, model = _load(
        _HYMT2_MODEL_ID, AutoModelForCausalLM, AutoTokenizer, trust_remote_code=True
    )
    tgt_name = _HYMT2_LANG_NAME.get(tgt_lang, tgt_lang)
    prompt = (
        f"Translate the following text into {tgt_name}. Note that you should only output "
        f"the translated result without any additional explanation:\n\n{text}"
    )
    messages = [{"role": "user", "content": prompt}]
    # Tencent's own README example does `model.generate(**inputs, ...)`, which
    # only type-checks if apply_chat_template returns a dict/BatchEncoding --
    # confirmed empirically (transformers 5.14.1 here): with return_tensors="pt"
    # alone it returns exactly that, not a raw tensor, so this unpacks with
    # **inputs and reads inputs["input_ids"].shape for the output slice below,
    # matching their example rather than my own earlier (wrong) assumption.
    inputs = tokenizer.apply_chat_template(
        messages, add_generation_prompt=True, return_tensors="pt"
    )
    torch.set_num_threads(_MAX_THREADS)
    gen_kwargs: dict[str, object] = (
        {"do_sample": True, "temperature": _SAMPLE_TEMPERATURE} if sample else {"do_sample": False}
    )
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max(256, int(len(text) * 1.6)), **gen_kwargs)
    generated = out[0][inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


_HYMT2 = Candidate(
    name="hy-mt2-1.8b",
    license="Apache-2.0",
    translate_fn=_hymt2_translate,
    unload_fn=unload_all,
)

# Jais (inceptionai/jais-family-*-chat) was tried as a 4th Arabic bonus
# candidate and dropped 2026-07-31: every checkpoint shares the same custom
# `modeling_jais.py`, which imports `find_pruneable_heads_and_indices` from
# `transformers.pytorch_utils` -- removed in the installed transformers
# 5.14.1 (only `prune_linear_layer` remains). Fails to load at any size,
# not a disk-space or gating issue (590M is neither gated nor large, and
# still hits the same ImportError). Arabic still has 3 real candidates
# (MiLMMT, OPUS-MT, M2M-100) without it -- revisit only if this repo's
# custom code is updated for newer transformers, or if pinning an older
# transformers version is ever worth it for one bonus candidate.

# --- production baselines, reusing local_translate.py's own cache/loaders ---
# These wrap the SAME model singletons production uses (lt._load_milmmt() /
# lt._load_seamless() -- same cache dict, not a reload), so a concurrent
# production batch would corrupt eval results and vice versa. Do not run
# this harness while a real translation batch might be in flight (see
# module docstring).
#
# local_translate.py's own _translate_text_milmmt/_translate_text_seamless
# are English-source-only by design (production never back-translates), so
# these reimplement the same fixed template/call shape with swappable
# src/tgt -- needed here for Layer 2's back-translation direction.


def _milmmt_translate(text: str, src_lang: str, tgt_lang: str, *, sample: bool = False) -> str:
    import torch

    tokenizer, model = lt._load_milmmt()
    src_name = lt._MILMMT_LANG_NAME[src_lang]
    tgt_name = lt._MILMMT_LANG_NAME[tgt_lang]
    prompt = f"Translate this from {src_name} to {tgt_name}:\n{src_name}: {text}\n{tgt_name}:"
    inputs = tokenizer(prompt, return_tensors="pt")
    torch.set_num_threads(_MAX_THREADS)
    gen_kwargs: dict[str, object] = (
        {"do_sample": True, "temperature": _SAMPLE_TEMPERATURE} if sample else {"do_sample": False}
    )
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max(256, int(len(text) * 1.6)), **gen_kwargs)
    generated = out[0][inputs["input_ids"].shape[1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def _seamless_translate(
    text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    sample: bool = False,  # noqa: ARG001 -- accepted for interface uniformity, see docstring
) -> str:
    """``sample`` is accepted for interface uniformity but ignored -- SeamlessM4T here always uses fixed beam search (num_beams=5), same as production. Beam search and temperature sampling aren't simply combinable, and this is the one baseline candidate where matching production's exact call shape (for a meaningful comparison) matters more than sampling-mode coverage."""
    import torch

    processor, model = lt._load_seamless()
    inputs = processor(text=text, src_lang=lt._SEAMLESS_LANG[src_lang], return_tensors="pt")
    torch.set_num_threads(_MAX_THREADS)
    with torch.inference_mode():
        tokens = model.generate(
            **inputs, tgt_lang=lt._SEAMLESS_LANG[tgt_lang], num_beams=5, max_new_tokens=512
        )[0]
    return processor.decode(tokens, skip_special_tokens=True).strip()


_MILMMT_BASELINE = Candidate(
    name="milmmt-46-4b (prod baseline)",
    license="Gemma Terms of Use (not Apache -- see local_translate.py module docstring)",
    translate_fn=_milmmt_translate,
    unload_fn=lambda: lt.unload_engine("milmmt"),
)

_SEAMLESS_BASELINE = Candidate(
    name="seamless-m4t-v2 (prod baseline, Pashto)",
    license="CC-BY-NC-4.0 (non-commercial -- accepted exception, see local_translate.py module docstring)",
    translate_fn=_seamless_translate,
    unload_fn=lambda: lt.unload_engine("seq2seq"),
)


# Starter set: 3 candidates for every language (fa/ps via the grouped
# opus-mt-en-ine/ine-en models rather than a dedicated pair, see above).
# Adding a candidate later means adding one Candidate to one list here --
# nothing about the runner or the checks above needs to change.
CANDIDATES: dict[str, list[Candidate]] = {
    "fr": [_MILMMT_BASELINE, _opus_mt_candidate("fr"), _M2M100, _HYMT2],
    "es": [_MILMMT_BASELINE, _opus_mt_candidate("es"), _M2M100, _HYMT2],
    "ar": [_MILMMT_BASELINE, _opus_mt_candidate("ar"), _M2M100, _HYMT2],
    "ru": [_MILMMT_BASELINE, _opus_mt_candidate("ru"), _M2M100, _HYMT2],
    "zh": [_MILMMT_BASELINE, _opus_mt_candidate("zh"), _M2M100, _HYMT2],
    "hi": [_MILMMT_BASELINE, _opus_mt_candidate("hi"), _M2M100, _HYMT2],
    "fa": [_MILMMT_BASELINE, _M2M100, _opus_mt_ine_candidate("fa"), _HYMT2],
    "ps": [_SEAMLESS_BASELINE, _M2M100, _opus_mt_ine_candidate("ps")],
}
