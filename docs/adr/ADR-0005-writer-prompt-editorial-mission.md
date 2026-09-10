# ADR-0005: Writer prompt gets an editorial mission; incident notes leave the prompt

Date: 2026-09-09. Status: accepted (owner decision, same day).

## Context

A live AlgoChess piece was rigorous and unreadable: ~2,000 words on a
two-player site, opening on tokenomics and contract state, describing what
the game is like to play in one late section, every section closing on an
epigram, no outside context. The owner's read: "we do not know anything
about the site itself, what you can do, what it is... it is directly in
very technical details," and the mission is "to make the algorand ecosystem
more alive so even if there was one sad player it would be good to have a
long article that kind of promote the usage of the service."

Reading the prompt chain showed why. The writer read ~7,000 words of
instructions accreted one production incident at a time, with 18 dated
post-mortems inline, and no statement of what the newspaper is for or who
reads it. The identity line was "senior investigative journalist"; the
research prompt was a forensic toolkit walk-through with a 48-round "depth
beats speed" budget; the lede rule asked for a hook but never a nut graf;
length was tied to trace volume; three separate anti-fabrication rules
together deleted every comparable; and the rubric's four dimensions were all
rigor dimensions, so a fully-verified audit scored 10/10 and every revision
pass pushed toward more scrutiny.

## Decision

1. A mission paragraph (`_EDITORIAL_MISSION`) sits at the top of the system
   prompt, before any rule: what PXke is for, the five reader questions in
   order (what is it / what can I do / why would I / what's the catch /
   what's next), who the reader is, and a one-third ceiling on verification
   detail.
2. Identity changes to a features writer who fact-checks like an
   investigator.
3. The lede rule is a positive spec: first paragraph says what it is and
   what a person does on it; first section is the product as met; on-chain
   verification comes after, in its own section. THE SCENE INCLUDES THE
   PRODUCT ITSELF becomes an (a)-(e) shape.
4. A PLAIN REGISTER rule bans aphoristic section closers and teaser headers;
   the close ends on a fact or next step.
5. Length scales to the reader's questions, not the trace; the digest is a
   ceiling, not a quota.
6. Context & Comparables is asked for in research, has a section in the
   digest, and is explicitly allowed (as OTHER products) in stage 2.
7. The rubric gains a `reader_value` dimension that gates revision.
8. Dated incident narratives are removed from the prompt text. Each rule
   keeps its illustrative example; the who-got-it-wrong-when story lives in
   the pinning test's docstring, the commit log, and the list below.

## Incident notes removed from the prompt (for the record)

- 2026-07-15 — 'fees are undisclosed' restated in prose, table, bullets and
  guidance (NO REPETITION); '0.001 ALGO per transfer' invented as a
  marketplace-specific fee (stage-2 licence carve-out).
- 2026-07-16 — invented phrase attributed to a named council in quotes;
  every article opening with the same PPoS/finality paragraph.
- 2026-08-04 — PyTeal explainer omitted Algorand Python/Puya; special
  edition came out shorter than ordinary-tier versions.
- 2026-08-05 — 'Ship of Theseus' / 'Memento Mori' frame unexplained, then
  over-explained; digest dropped one of three guest-access mechanics;
  'Memento Mori' asset digested as 'Mori coin' with its url dropped.
- 2026-08-06 — 'a project running on pocket money' read as rude.
- 2026-08-10/12 — guessed /about and /terms URLs 404ed while the real
  buttons worked (lumirogue.com).
- 2026-08-11 — unrelated 'LUMI' ASA cited as Lumi Rogue's token on name
  match, twice.
- 2026-08-13 — play_interactive skipped half the time on LumiRogue; a
  footer read 'Algorand Testnet' while wallet code was mainnet.
- 2026-08-17 — Downbad.farm first-coverage piece written about one
  previewed feature.
- 2026-08-28 — LumiRogue compose spent its interactive budget on
  Rankings/Battles, never opened the tutorial, never took a screenshot.
- 2026-09-02 — crowdfunding refund fired as designed but a draft opened on
  an unrelated finding; self-funded test vs. displayed backer momentum.
- 2026-09-05 — owner directive: neutral tone must not become a red flag
  for routine early-stage facts.
- 2026-09-08 (AlgoChess) — a 10.04 ALGO payout framed as 'the winner gets
  paid' on amount alone; no price call on a real-money story; Glicko-2
  described as operator-proof while computed server-side; invented
  before/after ratings in a settlement note; garbled maxPayoutBps
  sentence; wallet-custody question left open while the resolving fact
  sat in the how-to section.
- 2026-09-09 (AlgoChess) — settlement note written up with ratings no
  lookup found; the piece that prompted this ADR.

## Consequences

A same-day second pass (owner ask: "sweep useless numerical limits ...
compact where it can be compacted") removed decorative numbers — 'about 80
words', 'about a third of the body', '1-2 sentences', 'three consecutive
paragraphs', 'at least THREE sub-narratives / at least TWO of these', 'five
times in 800 words' — keeping only limits a gate or tool actually enforces
(90-char headline, 280-char summary, tag count, configured round budget,
gap-list cap, search limit parameter), and compacted every rule block.
Sizes before the refactor → after the mission pass → after compaction:

| block | before | mission pass | compacted |
|---|---|---|---|
| system prompt | ~4,150 | ~4,550 | ~3,400 |
| stage-2 narrative guidance | ~2,340 | ~2,500 | ~2,000 |
| stage-2 generation guidance | ~580 | ~530 | ~350 |
| research-phase guidance | ~3,010 | ~3,200 | ~2,290 |
| digest synthesis | ~970 | ~970 | ~650 |

Two tests keep it that way: `test_prompts_carry_no_dated_incident_notes`
and `test_prompts_carry_no_decorative_numeric_limits`. `PROMPT_VERSION`
bumped to `2026-09-09.1` so recomposes pick this up.
