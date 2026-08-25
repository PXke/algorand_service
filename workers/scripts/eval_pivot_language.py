"""One-off: does pivoting through another language beat translating directly from English?

NOTE (2026-08-25): this script is no longer runnable as-is -- it calls
local_translate's SeamlessM4T loader/language map directly (``lt._load_seamless``,
FLORES codes passed straight to the model), and both were removed from
local_translate.py along with SeamlessM4T support (see that module's
docstring: broken on markdown-heavy content, confirmed 2026-08-23; Pashto
now translates via DeepSeek). The investigation itself already concluded
(RESOLVED 2026-08-03: neither pivot route beat direct en->X; production
stays on direct translation) -- this file is kept as a record of the method
and findings below, not as a script anyone should try to run again.

The owner's question: "We always did en -> X but maybe zh -> ps or zh -> fa
was better." Not wired into eval_translate_candidates.py's CANDIDATES
registry (that harness compares MODELS for a fixed en->X direction) -- this
is a different axis, comparing SOURCE LANGUAGE for a fixed model, so it's
a separate small script, run by hand, same "signals not a gate" posture as
the rest of scripts/eval_*.py.

Two pivot routes for Pashto, both compared against direct en->ps:
- en->zh->ps: the original test (Chinese, linguistically unrelated to
  Pashto -- a "does ANY pivot help" baseline).
- en->fa->ps: Farsi as the pivot instead. Linguistically motivated, not
  arbitrary -- Persian has heavily influenced Pashto vocabulary and the two
  are geographically adjacent (both spoken in Afghanistan), unlike Chinese.
  Reuses the SAME en->fa direct-leg translation as the fa leg below, since
  en->fa is identical work either way -- one fewer model call.

Engines: MiLMMT (en<->zh<->fa, all three in its 46-language set) and
SeamlessM4T-v2 for Pashto (production's dedicated Pashto engine -- MiLMMT
has NO Pashto in its language set at all). SeamlessM4T's production
wrapper (local_translate._SEAMLESS_LANG) only maps en/ps because production
never translates FROM anything but English; this script calls the model
directly with FLORES-200-style codes ('cmn' Chinese, 'pes' Persian, 'pbt'
Pashto, 'eng' English) instead of extending that production dict for a
one-off test. 'pes' matches translation_eval.py's own
_OPUS_MT_INE_LANG = {"fa": "pes", "ps": "pus"} mapping for Farsi.

Fixture: pass any name from eval_translate_fixtures.FIXTURES (default
agent_term_consistency, the one excerpt built around a REAL confirmed
MiLMMT defect -- agent -> agency drift across blocks -- so back-translation
drift there is a meaningful signal, not a synthetic one). dense_numbers is
the sharpest fixture for THIS script specifically: the first run (zh pivot,
agent_term_consistency) found the zh->ps leg corrupting "40,000" down to
"4", a digit-accuracy failure a numbers-dense fixture will stress far
harder than a fixture with one number in it.

Usage: .venv/bin/python -m scripts.eval_pivot_language [fixture_name]
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

sys.path.insert(0, "scripts")

from eval_translate_fixtures import FIXTURES, get as get_fixture  # noqa: E402

from app.modules.ai import local_translate as lt  # noqa: E402
from app.modules.ai.translation_eval import (  # noqa: E402
    back_translation_consistency,
    digit_consistency,
    structural_alignment,
)

_AGENT_SYNONYMS = ("agents", "agency", "agencies")


@dataclass
class Leg:
    label: str
    text: str
    back_translated: str


def _milmmt(text: str, src: str, tgt: str) -> str:
    import torch

    tokenizer, model = lt._load_milmmt()
    src_name = lt._MILMMT_LANG_NAME[src]
    tgt_name = lt._MILMMT_LANG_NAME[tgt]
    prompt = f"Translate this from {src_name} to {tgt_name}:\n{src_name}: {text}\n{tgt_name}:"
    inputs = tokenizer(prompt, return_tensors="pt")
    torch.set_num_threads(lt._MAX_THREADS)
    with torch.inference_mode():
        out = model.generate(
            **inputs, max_new_tokens=max(256, int(len(text) * 1.6)), do_sample=False
        )
    generated = out[0][inputs["input_ids"].shape[1] :]
    return tokenizer.decode(generated, skip_special_tokens=True).strip()


def _seamless(text: str, src_flores: str, tgt_flores: str) -> str:
    import torch

    processor, model = lt._load_seamless()
    inputs = processor(text=text, src_lang=src_flores, return_tensors="pt")
    torch.set_num_threads(lt._MAX_THREADS)
    with torch.inference_mode():
        tokens = model.generate(**inputs, tgt_lang=tgt_flores, num_beams=5, max_new_tokens=512)[0]
    return processor.decode(tokens, skip_special_tokens=True).strip()


def _timed(label: str, fn, *args):
    t0 = time.monotonic()
    out = fn(*args)
    print(f"  [{time.monotonic() - t0:5.1f}s] {label}")
    return out


def main() -> int:
    fixture_name = sys.argv[1] if len(sys.argv) > 1 else "agent_term_consistency"
    fixture = get_fixture(fixture_name)
    en = fixture.excerpt
    synonyms = _AGENT_SYNONYMS if fixture.dominant_term == "agent" else (fixture.dominant_term,)
    print(f"fixture: {fixture.name}")
    print(f"watch_for: {fixture.watch_for}\n")

    print("stage 1: en -> zh (MiLMMT, pivot intermediate for the zh route)")
    zh = _timed("en->zh", _milmmt, en, "en", "zh")

    print("\nstage 2: fa legs (direct en->fa; this doubles as the pivot intermediate for the fa route)")
    direct_fa = _timed("en->fa (direct)", _milmmt, en, "en", "fa")
    pivot_fa_via_zh = _timed("zh->fa (pivot via zh)", _milmmt, zh, "zh", "fa")
    direct_fa_back = _timed("fa->en (direct, back-translate)", _milmmt, direct_fa, "fa", "en")
    pivot_fa_via_zh_back = _timed(
        "fa->en (pivot via zh, back-translate)", _milmmt, pivot_fa_via_zh, "fa", "en"
    )

    print("\nstage 3: ps legs -- direct, pivot via zh, and pivot via fa (all SeamlessM4T-v2)")
    direct_ps = _timed("en->ps (direct)", _seamless, en, "eng", "pbt")
    pivot_ps_via_zh = _timed("zh->ps (pivot via zh)", _seamless, zh, "cmn", "pbt")
    # Reuses direct_fa as input -- en->fa is identical work whether it feeds
    # the fa-direct leg above or this fa-pivot leg, no need to translate twice.
    pivot_ps_via_fa = _timed("fa->ps (pivot via fa)", _seamless, direct_fa, "pes", "pbt")
    direct_ps_back = _timed("ps->en (direct, back-translate)", _seamless, direct_ps, "pbt", "eng")
    pivot_ps_via_zh_back = _timed(
        "ps->en (pivot via zh, back-translate)", _seamless, pivot_ps_via_zh, "pbt", "eng"
    )
    pivot_ps_via_fa_back = _timed(
        "ps->en (pivot via fa, back-translate)", _seamless, pivot_ps_via_fa, "pbt", "eng"
    )

    legs = [
        Leg("fa direct (en->fa)", direct_fa, direct_fa_back),
        Leg("fa pivot via zh (en->zh->fa)", pivot_fa_via_zh, pivot_fa_via_zh_back),
        Leg("ps direct (en->ps)", direct_ps, direct_ps_back),
        Leg("ps pivot via zh (en->zh->ps)", pivot_ps_via_zh, pivot_ps_via_zh_back),
        Leg("ps pivot via fa (en->fa->ps)", pivot_ps_via_fa, pivot_ps_via_fa_back),
    ]

    print("\n" + "=" * 76)
    print(f"{'leg':<32} {'digits':>8} {'structure':>10} {'term match':>12}")
    print("=" * 76)
    for leg in legs:
        digits = digit_consistency(en, leg.text)
        structure = structural_alignment(en, leg.text)
        backtrans = back_translation_consistency(
            [en], [leg.back_translated], fixture.dominant_term, synonyms=synonyms
        )
        digit_flag = "ok" if not digits.ungrounded else f"{len(digits.ungrounded)} ungrounded"
        struct_flag = "ok" if structure.block_count_matches and not structure.row_diffs else "drift"
        term_flag = f"{backtrans.consistency:.0%}"
        print(f"{leg.label:<32} {digit_flag:>8} {struct_flag:>10} {term_flag:>12}")

    print("\n--- full text, read this yourself (nobody here reads fa/ps fluently) ---")
    for leg in legs:
        print(f"\n### {leg.label}")
        print(leg.text)
        print(f"\n[back-translated to English]: {leg.back_translated}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
