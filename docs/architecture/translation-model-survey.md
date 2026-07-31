# Translation model survey: MiLMMT vs. smaller seq2seq candidates

Run 2026-07-31 (`20260731T085110Z_deterministic`, 120 cases). First real run
of the "promising-ranking" harness (`workers/app/modules/ai/translation_eval.py`
+ `workers/scripts/eval_translate_*`) built to answer "how much fixing
engineering does each translation language need," per language and per
candidate model.

## Why

Production translation (`local_translate.py`) had a confirmed defect: MiLMMT
rendered "agent(s)" as عاملان (correct) in a Farsi article's title but
آژانس‌های ("agencies", wrong) in its body — a term drifting within one
document. Trying to answer "is there a better model for this language" from
published benchmarks went nowhere: a COMET table initially cited for MiLMMT
turned out to be a Qwen baseline shown for comparison in the same paper, not
MiLMMT's own numbers; WMT's constrained track covers only 3 of our 8
languages in a given year; no aggregator publishes comparable per-language
scores across the license-eligible, right-sized candidates we could actually
run. Conclusion: stop reading papers, build a small in-house survey instead.

## Method

Two automatable, language-agnostic layers (deliberately nothing that judges
fluency — nobody on the team reads Pashto, Farsi, Arabic, Russian, or
Chinese fluently enough to score that, and the harness doesn't pretend
otherwise):

- **Structural**: block-count and list/table row-count preservation, plus
  number-value grounding between source and translation.
- **Back-translation**: translate the output back to English and check
  whether the source's dominant repeated term survives, block by block —
  how the original agent/agency defect gets caught without reading Farsi.

5 frozen excerpts (2-3 paragraphs each, not full articles — MiLMMT alone
measured up to 51 minutes on one long article earlier this session, so a
survey has to use excerpts or it never finishes): a term-consistency
regression case, a markdown list, a markdown table, a numbers-heavy
paragraph, and a plain-prose control. 3 candidates per language wherever
possible: the two production baselines (MiLMMT for 7 languages,
SeamlessM4T for Pashto) plus M2M-100 (MIT) and, per-language, either a
dedicated OPUS-MT pair or (for Farsi/Pashto, where the dedicated pair
turned out not to exist any more — see below) the grouped
`opus-mt-en-ine`/`opus-mt-ine-en` models. A 4th Arabic candidate, Jais, was
tried and dropped (see below).

## Finding 1: markdown structure breaks every candidate except MiLMMT

This is the headline result. Across every table_block and list_block case,
row-count preservation split cleanly by model family, not by language:

- **table_block**: every one of the 16 non-MiLMMT cases (across M2M-100,
  every OPUS-MT variant, and SeamlessM4T) lost the table's row structure.
  This wasn't reformatting — the actual data (in the fixture: 4 rows of
  category/proposal-count/ALGO-amount) was **destroyed outright** and
  replaced with degenerate repetition or nonsense:
  - M2M-100, French: `Bande annonce Bande annonce Bande annonce ...`
    ("Trailer" repeated ~19 times) in place of the table.
  - SeamlessM4T, Pashto (the actual **production** Pashto model): `د دې
    لپاره چې` repeated ~40 times, back-translating as "what's in it"
    repeated ~24 times.
  - OPUS-MT (grouped `en-ine`), Pashto: not even a repetition loop — outright
    semantic garbage: back-translated as "General Translators and
    Translators... The video group is 31% of the error, 38% of the
    computer."
  - MiLMMT never did this, in any of its 7 languages — worst case was a
    known digit-checker false positive (see Finding 3), never row loss.
- **list_block**: same split, mechanism, and 16/16-vs-0/7 pattern, but
  milder — content stayed accurate, only the markdown structure broke.
  OPUS-MT-es collapsed 4 bullet items into one run-on line with inline
  dashes (`- A TypeScript client generator... - Box storage helpers... - A
  local sandbox...`) while translating every item correctly.

**Why**: OPUS-MT (Marian), M2M-100, and SeamlessM4T are dedicated seq2seq
MT models trained on sentence/short-phrase parallel corpora (Tatoeba, OPUS
bitext) — none of that training data looks like a markdown table. Feeding
one in is far out of distribution, and repetition-loop degeneration is a
classic failure mode for that. MiLMMT is built on a Gemma3 base pretrained
on general web text, which includes countless real markdown tables (READMEs,
wikis, docs) — it has genuine structural understanding the dedicated MT
models never learned.

**Implication**: ~59% of the article corpus contains a table. This
disqualifies M2M-100, OPUS-MT, and SeamlessM4T from any table-bearing
content as currently wired (whole-block translation) — not a minor quality
gap, an outright data-loss bug. The likely fix is **cell-by-cell
translation**: extract each table cell's text, translate it in isolation
(exactly the short-phrase input these models were trained on), and
reassemble the table deterministically ourselves — which also makes
`structural_mismatch`/`row_diff` impossible by construction, since we
control the reassembly, not the model. Not yet built; see Next steps.

## Finding 2: MiLMMT's own quality, once formatting is set aside, is solid

Excluding the table/list formatting issue above, MiLMMT was close to clean
across its 7 languages:

- **Arabic and Hindi**: fully clean on every fixture, including numbers.
- **French**: the only plausible real semantic issue found all day —
  "Proposals" (a table column header) translated as "Projets" ("Projects"),
  a defensible register choice in French governance language, not an
  obvious error.
- **Farsi**: one `backtrans_drift` flag on the term-consistency regression
  fixture itself, but this run's MiLMMT output actually held the term
  consistent in the forward translation (نماینده throughout) — the flag
  came from the back-translation choosing "representative" over "agent",
  see Finding 3.
- No language reproduced the original agent/agency defect on this fixture
  set. That's a real result, if a slightly anticlimactic one: today's
  fixtures didn't catch a repeat of the bug that motivated this whole
  survey, though the underlying risk (a single stable target-language term
  covering multiple English synonyms) hasn't gone away.

## Finding 3: two harness bugs/limitations found and handled, mid-run

- **Fixed**: `digit_consistency` false-positived constantly on correct
  European number formatting — MiLMMT's French output used `1,4 M` (comma
  decimal) and `900 000` / `900.000` (space/period thousands grouping),
  both correct in their own language, neither parseable by the underlying
  US/UK-formatted regex. Fixed by normalizing both texts through a
  locale-agnostic heuristic (a separator is a thousands grouping only when
  followed by an exact 3-digit group, never a decimal) before comparison.
  Verified against the real French/Spanish output: the `dense_numbers` case
  went from 2/10 grounded (8 false positives) to 5/8. Committed
  (`eda263f`).
- **Documented, not fixed**: the remaining 3 ungrounded values in that same
  case are a different, deeper gap — Spanish/Arabic spelled out "310
  millones"/"310 ملايين" instead of "310M", and the unit-class regex only
  recognizes the English singular "million," not its translations. Would
  need a per-language magnitude-word table; not attempted.
- **Method limitation, not a bug**: back-translation is two hops, and
  either can introduce noise independent of the forward translation being
  correct. Two cases this run: MiLMMT's Chinese back-translation rendered
  一致的 代理 (consistently "agent" in the actual Chinese text) as "agent"
  in one back-translated block and "proxy"/"proxies" in another — the
  *Chinese* text never varied, only the back-translation did. Same pattern
  with OPUS-MT-es's back-translation garbling "AlgoKit" into "SomethingKit"
  even though the real Spanish translation kept "AlgoKit" correctly. Any
  `backtrans_drift` flag needs a glance at the actual target-language text
  before concluding the *forward* translation is at fault.

## Candidate notes

- **Jais** (Arabic 4th/bonus candidate): tried, dropped. Every checkpoint in
  the family shares custom `modeling_jais.py` code that imports
  `find_pruneable_heads_and_indices` from `transformers.pytorch_utils`,
  removed in the installed transformers 5.14.1 — fails to load at any size.
  Also hit, in order, on the way to that conclusion: checkpoints ≥1.3B are
  Hub-gated (need an accepted license + token, not set up here), and the
  6.7B checkpoint alone is 28.6GB (more than was free on the dev box).
  Arabic still has 3 working candidates without it.
- **OPUS-MT en-fa / en-ps**: don't exist as dedicated pairs. The initial
  401 looked like an access restriction — the plain webpage even served an
  AWS WAF bot-challenge page — but that was a red herring: the real
  `huggingface_hub` client gives a clean `RepositoryNotFoundError`, and
  neither repo appears in a full listing of Helsinki-NLP's 1563 repos.
  Helsinki-NLP folded languages like these into grouped multi-target models
  instead (`opus-mt-en-ine` / `opus-mt-ine-en`, "ine" = Indo-European),
  selected via a `>>id<<` prefix token rather than a separate repo — both
  `pes` (Farsi) and `pus` (Pashto) are covered, both Apache-2.0.
- **M2M-100**: also caught a real, unrelated translation error during its
  initial smoke test — mistranslated "wallet" as "computer" (الكمبيوتر
  instead of المحفظة) in Arabic, independent of the table/list issue.

## Numbers

Cases per candidate, `[clean]` vs. anything flagged (structural mismatch,
row diff, ungrounded digits, or back-translation drift — see Finding 3 for
why "flagged" isn't the same as "wrong"):

| Candidate | Clean | Total |
|---|---|---|
| milmmt-46-4b (prod baseline) | 17 | 35 |
| seamless-m4t-v2 (prod baseline, Pashto) | 2 | 5 |
| m2m100-1.2b | 9 | 40 |
| opus-mt-en-fr | 1 | 5 |
| opus-mt-en-es | 0 | 5 |
| opus-mt-en-ar | 1 | 5 |
| opus-mt-en-ru | 2 | 5 |
| opus-mt-en-zh | 2 | 5 |
| opus-mt-en-hi | 1 | 5 |
| opus-mt-ine-fa | 0 | 5 |
| opus-mt-ine-ps | 0 | 5 |

Raw "clean" rate understates MiLMMT's real advantage relative to the
others once digit-checker noise and back-translation-hop artifacts are
read out (see Findings 2-3), and overstates how "bad" the smaller
candidates' actual translation *content* is — most of what drags their
numbers down is the list/table formatting collapse (Finding 1), not wrong
meaning.

## Next steps

1. **Prototype cell-by-cell table translation** for the smaller candidates
   — the highest-leverage fix, since it's the one thing standing between
   M2M-100/OPUS-MT and being viable at all for ~59% of the corpus.
2. Re-run this survey (or at least `table_block`/`list_block`) after that
   fix lands, to see whether it actually rescues the smaller candidates or
   just shifts the failure mode.
3. `docs/modules/article-translations.md` still describes the old
   Mistral-based translation pipeline (`translate_article`,
   `MISTRAL_MODEL_TRANSLATE`) — stale since `local_translate.py` replaced
   it earlier this session (`6303958`). Needs an update, separate from this
   doc.

## Raw data

Full per-(candidate, language) Markdown reports (translated + back-translated
text for every case) are in
`workers/scripts/eval_translate_output/20260731T085110Z_deterministic/`.
That directory is gitignored (matches the existing convention for
`eval_compose_output/`) — it's local-only and not guaranteed to survive;
re-run `python -m scripts.eval_translate_candidates` to regenerate it.
