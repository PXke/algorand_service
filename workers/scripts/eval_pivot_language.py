"""One-off: does pivoting through Chinese beat translating directly from English?

The owner's question: "We always did en -> X but maybe zh -> ps or zh -> fa
was better." Not wired into eval_translate_candidates.py's CANDIDATES
registry (that harness compares MODELS for a fixed en->X direction) -- this
is a different axis, comparing SOURCE LANGUAGE for a fixed model, so it's
a separate small script, run by hand, same "signals not a gate" posture as
the rest of scripts/eval_*.py.

Engines: MiLMMT (en<->zh<->fa, all three in its 46-language set) and
SeamlessM4T-v2 for Pashto (production's dedicated Pashto engine -- MiLMMT
has NO Pashto in its language set at all). SeamlessM4T's production
wrapper (local_translate._SEAMLESS_LANG) only maps en/ps because production
never translates FROM anything but English; this script calls the model
directly with FLORES-200 codes ('cmn' Chinese, 'pbt' Pashto, 'eng' English)
instead of extending that production dict for a one-off test.

Fixture: agent_term_consistency (eval_translate_fixtures.py) -- the one
excerpt built around a REAL confirmed MiLMMT defect (agent -> agency drift
across blocks), so back-translation drift here is a meaningful signal, not
a synthetic one.

Usage: .venv/bin/python -m scripts.eval_pivot_language
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass

sys.path.insert(0, "scripts")

from eval_translate_fixtures import FIXTURES  # noqa: E402

from app.modules.ai import local_translate as lt  # noqa: E402
from app.modules.ai.translation_eval import (  # noqa: E402
    back_translation_consistency,
    digit_consistency,
    structural_alignment,
)

FIXTURE = next(f for f in FIXTURES if f.name == "agent_term_consistency")
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
    en = FIXTURE.excerpt
    print(f"fixture: {FIXTURE.name}\n")

    print("stage 1: en -> zh (MiLMMT, shared pivot intermediate)")
    zh = _timed("en->zh", _milmmt, en, "en", "zh")

    print("\nstage 2: fa legs (direct en->fa vs pivot zh->fa, both MiLMMT)")
    direct_fa = _timed("en->fa (direct)", _milmmt, en, "en", "fa")
    pivot_fa = _timed("zh->fa (pivot)", _milmmt, zh, "zh", "fa")
    direct_fa_back = _timed("fa->en (direct, back-translate)", _milmmt, direct_fa, "fa", "en")
    pivot_fa_back = _timed("fa->en (pivot, back-translate)", _milmmt, pivot_fa, "fa", "en")

    print("\nstage 3: ps legs (direct en->ps vs pivot zh->ps, both SeamlessM4T-v2)")
    direct_ps = _timed("en->ps (direct)", _seamless, en, "eng", "pbt")
    pivot_ps = _timed("zh->ps (pivot)", _seamless, zh, "cmn", "pbt")
    direct_ps_back = _timed("ps->en (direct, back-translate)", _seamless, direct_ps, "pbt", "eng")
    pivot_ps_back = _timed("ps->en (pivot, back-translate)", _seamless, pivot_ps, "pbt", "eng")

    legs = [
        Leg("fa direct (en->fa)", direct_fa, direct_fa_back),
        Leg("fa pivot  (en->zh->fa)", pivot_fa, pivot_fa_back),
        Leg("ps direct (en->ps)", direct_ps, direct_ps_back),
        Leg("ps pivot  (en->zh->ps)", pivot_ps, pivot_ps_back),
    ]

    print("\n" + "=" * 72)
    print(f"{'leg':<26} {'digits':>8} {'structure':>10} {'agent term':>12}")
    print("=" * 72)
    for leg in legs:
        digits = digit_consistency(en, leg.text)
        structure = structural_alignment(en, leg.text)
        backtrans = back_translation_consistency(
            [en], [leg.back_translated], FIXTURE.dominant_term, synonyms=_AGENT_SYNONYMS
        )
        digit_flag = "ok" if not digits.ungrounded else f"{len(digits.ungrounded)} ungrounded"
        struct_flag = "ok" if structure.block_count_matches and not structure.row_diffs else "drift"
        agent_flag = f"{backtrans.consistency:.0%}"
        print(f"{leg.label:<26} {digit_flag:>8} {struct_flag:>10} {agent_flag:>12}")

    print("\n--- full text, read this yourself (nobody here reads fa/ps fluently) ---")
    for leg in legs:
        print(f"\n### {leg.label}")
        print(leg.text)
        print(f"\n[back-translated to English]: {leg.back_translated}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
