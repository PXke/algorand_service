# Translation model survey: MiLMMT vs. smaller seq2seq candidates

**Addendum, 2026-08-25:** SeamlessM4T (this survey's Pashto production
baseline) has since been removed from production code. A 2026-08-23
side-by-side comparison confirmed it broken for markdown-heavy content --
repeated repetition-loop degeneration on list/table blocks, in one case
destroying every citation in a source list outright. Pashto now translates
via DeepSeek instead of a local engine (see `DEEPSEEK_TRANSLATE_LANGS` in
`workers/app/core/config.py`). This document's findings below are left as
the historical record of what was actually run and found; they are not
rewritten to match the current architecture.

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

## Finding 4: cell-level splitting fixes the collapse, but unevenly

Following Finding 1's implication, `translate_block_with` was extended
(2026-07-31, commit `f01b81a`) to detect a pure list or table block and
translate each item/cell's text in isolation, reassembling the markdown
structure deterministically ourselves — the same principle already used for
headings, just applied to two more structural units. Re-running
`table_block`/`list_block` across every candidate and language
(`20260731T134459Z_deterministic`, 48 cases) confirmed it works, but not
uniformly:

- **The catastrophe is gone everywhere.** No more repetition-loop
  degeneration, no more outright data loss, across every candidate tested.
  Non-MiLMMT clean rate on these two fixtures went from 0/34 to 16/34.
- **But cell isolation trades one failure mode for another, and which one
  shows up is per-model, per-language, not predictable in advance.** M2M-100
  improved substantially (9/16 clean) with some remaining vocabulary gaps —
  "DeFi" translated to French as "Défaut" ("Lack/Default", not even a
  plausible near-miss, evidence the term simply isn't in its vocabulary) and
  "ALGO" as "Quelque chose" ("Something"). OPUS-MT-zh kept producing
  structurally-clean tables with wrong units on isolated numbers ("900K" →
  "900克朗", 900 *kroner*; "780K" → "780公里", 780 *kilometres*) — a
  systematic pattern, the model guessing a unit with no sentence context to
  anchor "K". OPUS-MT-hi was worse in a different way: not systematic at
  all, just occasionally and wildly wrong — one isolated cell ("650K")
  back-translated as "Watch Tower Bible and Tract Society of Pennsylvania",
  a completely unrelated hallucination. SeamlessM4T's `list_block` went
  fully clean, but `table_block` still garbled isolated numbers in its own
  way ("9" → "Nine nights", "650K" → "650 kilos of wheat", "900K" produced a
  raw, unfilled `XNUMX` template placeholder).
- **Conclusion**: cell isolation is a real, necessary fix for the
  structural catastrophe, but it is not sufficient on its own to make any
  of the smaller candidates production-ready for numeric/tabular content —
  each remaining defect is a distinct, model-specific problem that would
  need its own investigation, not one shared fix.

Eval-harness only; not ported into `local_translate.py`'s production
`_translate_block` (see the comment in `translation_eval.py` for why).

## Conclusions: which model for which language

No language tested here has a case for switching away from what's already
in production. MiLMMT and SeamlessM4T remain the pick for every language;
this survey's practical value was confirming that, not finding a
replacement — every alternative candidate, even after the Finding 4 fix,
still has real, unresolved problems no language recovers from cleanly.

- **Arabic, Hindi, Spanish, Russian**: MiLMMT clean or near-clean
  throughout, no real defect found. No open concern from this survey.
- **French**: MiLMMT's only debatable case all day (Proposals → Projets)
  resolved clean on the cell-split retest. No open concern.
- **Chinese**: MiLMMT clean (the one flag was a back-translation-hop
  artifact, see Finding 3). Worth remembering if a cheaper candidate is
  ever reconsidered: OPUS-MT-zh's wrong-unit hallucination on numbers is a
  real, still-open defect class for that candidate specifically.
- **Farsi**: this is the language the original production incident
  happened on. Today's fixture test didn't reproduce it, but deterministic
  decoding means a clean result on ONE fixture proves the harness didn't
  catch it THIS time, not that the underlying risk is gone — a real
  article's specific phrasing could still trigger it. Treat as "watch this
  going forward," not "resolved."
- **Pashto**: the one language with no good answer yet. SeamlessM4T has a
  genuine, currently-unfixed production defect (whole-table destruction —
  Finding 4's fix exists only in the eval harness). Even with that fix
  applied, it still garbles isolated numbers in table cells ("9" → "Nine
  nights", "650K" → "650 kilos of wheat"). No alternative tested (M2M-100,
  the grouped OPUS-MT model) is any better — both stayed 0/2 clean on
  `table_block` post-fix too. Combined with its non-commercial license
  (an already-accepted stopgap, not a resolved question) and the fact that
  nobody on the team reads Pashto well enough to sanity-check any of this
  by eye, this is the language most worth real, continued investment —
  not the one to leave alone because "it's just Pashto."

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

## 2026-08-26 update: upgraded to MiLMMT v1.0

xiaomi-research released `MiLMMT-46-4B-v1.0` on 2026-08-21 — same
architecture/license/language coverage as the `v0.1` this survey picked,
with published WMT24++ gains (most notably Persian: COMETKiwi 80.03→83.31,
XCOMET 80.84→86.74), still no Pashto coverage (the DeepSeek routing this
survey's Findings already established stays correct, unchanged).

Ran a side-by-side eval on the production box across all 7 covered
languages (ar/fa/ru/zh/hi/es/fr), 4 fixtures each (plain prose, markdown
headers+list, digit-heavy, inline markdown link), both versions, 56 cases
total:

- **Structural/digit-grounding: 0 failures on either version**, all 56
  cases — no regression, no improvement either (both were already clean
  here, consistent with Finding 2).
- **One link-drop quirk found, shared by both versions equally**: a
  paragraph with two markdown links loses the second one's `[text](url)`
  wrapper (translates the anchor text as plain prose, URL and brackets
  gone) on es/fa/fr, not on ar/ru/zh/hi. Same behavior on v0.1 and v1.0 —
  not a v1.0 regression, a pre-existing model-family weakness on
  multi-link paragraphs worth a dedicated follow-up eval of its own
  (`link_repair` in `local_translate.py` already handles two OTHER known
  MiLMMT link defects — broken syntax and untranslated anchors — but not
  this third one, a full link disappearance).
- **Throughput: inconclusive, not trustworthy from this run.** The box was
  under heavy, fluctuating unrelated load (a large crawl backlog) for the
  entire eval window; per-language deltas ranged from v1.0 being 18%
  *faster* to 62% *slower* than v0.1 depending on which language happened
  to run during a load spike. Needs a clean re-benchmark on an idle box if
  a real throughput number is ever needed.
- **Fluency**: both versions produce well-formed, complete output with no
  garbling on manual inspection (not independently verified by a native
  speaker) — deferred to the published external benchmark numbers above
  for the actual quality claim.

Net: no regression on anything measured, and a real published quality gain
for Persian specifically. Upgraded `_MILMMT_MODEL_ID` in
`local_translate.py` to `xiaomi-research/MiLMMT-46-4B-v1.0`.

## Next steps

1. ~~Prototype cell-by-cell table translation~~ — done (Finding 4,
   `f01b81a`), and re-run against every candidate/language. It fixed the
   structural catastrophe but not the deeper per-model quality gaps.
2. Investigate whether any *other* candidate models were missed — the field
   moves fast and the ones tried so far were found ad hoc (MiLMMT itself
   was found by searching HuggingFace for recent translation models, not
   from a systematic sweep). Now that a cheap benchmark exists, a broader
   HF search targeted at "general LLM continual-pretrained for
   translation" (MiLMMT's own recipe, and the likely reason it's the only
   candidate that handles markdown structurally) is worth doing before
   calling the candidate list closed.
3. Farsi and Pashto both need more than one clean deterministic fixture
   pass before either is trusted: sampling-variance mode (temperature
   decoding, repeated runs) to check whether a clean result is stable, and
   ideally testing against more/different real Farsi content, not just the
   one synthetic excerpt used here.
4. `docs/modules/article-translations.md` still describes the old
   Mistral-based translation pipeline (`translate_article`,
   `MISTRAL_MODEL_TRANSLATE`) — stale since `local_translate.py` replaced
   it earlier this session (`6303958`). Needs an update, separate from this
   doc.

## Raw data

Full per-(candidate, language) Markdown reports (translated + back-translated
text for every case) are in
`workers/scripts/eval_translate_output/20260731T085110Z_deterministic/`
(the original 120-case run) and
`workers/scripts/eval_translate_output/20260731T134459Z_deterministic/`
(the Finding 4 list/table retest, 48 cases). That directory is gitignored
(matches the existing convention for `eval_compose_output/`) — local-only,
not guaranteed to survive; re-run `python -m scripts.eval_translate_candidates`
to regenerate it.
