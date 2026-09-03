# Execution-trust cluster — evaluation (2026-09-03)

Evaluation of a cluster of feature propositions relayed by an external
demand-signal report ("Relay"). The specific attribution (which agents asked,
how many) is an unverified lead; the themes are evaluated on their own merits.

The cluster's shared question: *how does a paying agent know a paid
interaction actually delivered what it promised, beyond "reachable and
returned 200"?*

## Ground rules this evaluation was run under

- The escrow-based "guaranteed execution" proposal (pay into contract, release
  on verified success) was **already rejected by the owner** this cycle:
  custody-in-substance, breaks x402 client compatibility, gameable by whoever
  controls the success check, and no real correctness guarantee anyway. The
  owner chose **auto-refund on product-write failure** instead, and it is
  live (`backend/app/modules/x402/paid_request.py`, `run_with_refund`,
  migration 102). Each item below is explicitly checked for being that
  rejected idea in different clothes.
- Constraints from CLAUDE.md §9 apply: nothing custodial, no wash volume,
  Cassandra+Redis only, financially sensitive items get flagged for an owner
  decision, never built on an agent's own call.

## What already exists (the baseline any new feature must beat)

1. **Auto-refund** (`x402/paid_request.py`): a paid product write that raises
   after settlement refunds the payer in full, on-chain, automatically, and
   trips a per-resource circuit breaker. This is the *delivery-failure*
   answer.
2. **Settlement ledger with `fulfilled` flag** (`x402/settlement.py`,
   migrations 095/102): every payment that bought nothing is a durable
   `fulfilled=False` row with refund status — reconcilable per-txid. This is
   the *bookkeeping* answer.
3. **Free unpaid probe + history** (`workers/app/modules/x402_probe/probe.py`,
   surfaced on every directory listing detail and `probe-status` read):
   liveness, latency, 402-offer validity, and payTo-vs-lister match (the
   verified badge). Deliberately never pays and never judges content. This is
   the *liveness/identity* answer.
4. **Paid grading with on-chain usage proof**
   (`x402_grading/services/grading_service.py`, `usage_proof.py`,
   `credibility.py`): one grade per (wallet, url); a cited payment txid is
   verified on-chain against the graded endpoint's own payTo (receiver side)
   and against the grader's settled wallet (sender side); grader influence is
   weighted by that wallet's all-time settled spend with the marketplace;
   listing owners' self-grades and our probe wallets are excluded from
   rankings. This is the *content-quality* answer — post-hoc paid judgment by
   agents who provably paid the endpoint.

Together these already cover: paid-but-undelivered (refund + ledger), dead or
misconfigured endpoints (probe), impostor payTo (probe badge), and "was the
content actually good" (grading, sybil-weighted). Every item below is judged
against that baseline.

---

## Item 1 — Verifiable execution / attestation schema

*"Settlement memo carries a hash of (input + output + session nonce),
checkable after the fact."*

**As literally specified: technically impossible in x402.** The payer's
client signs the payment transaction *before* sending the paid request — the
signed txn travels in the payment header and the facilitator settles it
as-is (see `docs/x402-facilitator.md`, client-signer section). The note/memo
is therefore fixed at signing time, before the response exists. An output
hash cannot appear in the settlement memo, ever, under this protocol. The
payer *could* voluntarily put `H(input‖nonce)` in their own txn note today —
that needs nothing from us and proves only what the payer already knows.

**The feasible variant is a server-signed fulfillment receipt**, off-chain: a
response header on our own paid routes carrying
`sig(H(request body) ‖ H(response body) ‖ settlement txid ‖ timestamp)` under
a published key, verifiable by anyone against the on-chain settlement (the
facilitator already serves `GET /api/receipt/{txId}` for the payment leg;
this would bind that settlement to specific request/response content). Hash
commitments only — raw input/output never published (which also answers
item 5 for free).

**Is it escrow in a different hat?** The receipt itself is not: no funds
held, nothing gated on verification, x402 client flow unchanged. But the
*implied next step* — "checkable after the fact, and then a dispute gets
adjudicated and money moves" — is exactly the rejected escrow re-derived.
Any design here must stop at *evidence*: a receipt is non-repudiation (the
endpoint cannot later deny having served that output for that payment), not
a correctness guarantee and not a remedy. The remedy already exists and is
auto-refund; the correctness judgment already exists and is grading.

**Honest limits:** for third-party endpoints a receipt convention is only as
honest as the endpoint — a lying endpoint signs a lying receipt. Its value
for third parties is a *self-declared capability* ("this endpoint issues
verifiable receipts") that grading and complaints can then reference, not a
verification we perform.

**Feasibility:** small. One signing key (management/rotation is the real
cost), one header emitted next to the existing `mark_fulfilled` call in the
`paid_request.py` contract, a published spec, optionally a
`supports_receipts` listing field. No new datastore, no custody, no protocol
break.

**Recommendation: needs an owner decision, then small build if approved.**
The one genuinely new, non-custodial, non-escrow idea in the cluster.
Modest direct value; its best case is Innovation-score differentiation
(PXke's own endpoints as the reference implementation of a receipt
convention the directory can then let third parties self-declare). The
owner decision needed: whether receipt-signing key management is worth
owning, and confirmation that the design stops at evidence — no dispute or
adjudication arm, ever.

## Item 2 — Deterministic endpoints

*"Same input → same output, agents on constrained hardware want
reproducibility."*

**A different problem from the rest of the cluster** — reproducibility, not
trust enforcement — and mostly not ours to solve. Two halves:

- **Our own products:** determinism is not even coherent for most of them.
  Directory search, grading aggregates (credibility weights move with every
  settlement, by design), probe history, and news feeds are all inherently
  time-varying. Forcing determinism would break their correctness.
- **Third-party listings:** this is *metadata*, and the directory's existing
  free-form tag system already expresses it — an endpoint can tag itself
  `deterministic` today, searchably, at zero build cost. A dedicated
  schema-blessed boolean would be a one-column nicety over that.
  *Verifying* the claim is where it turns expensive: the probe would have to
  pay the same endpoint twice and compare paid outputs, which collides with
  the probe's designed never-pays contract and inherits all of item 3's
  economics below.

**Recommendation: don't build.** Point demand at the tag system. If several
real listings adopt a `deterministic` tag organically, promoting it to a
schema field is a trivial later change; verification remains item 3's
problem and shares its verdict.

## Item 3 — Fidelity scoring on the probe

*"Does a paid response's content match what the listing claims, not just
'did it respond'."*

**This is the expensive one, and the existing grading product already covers
its useful core from a better angle.**

Three structural problems with a fidelity probe:

1. **It must pay, at prices set by the probed endpoint.** The existing probe
   never pays — its UA advertises that, its design (`probe.py` docstring)
   depends on it, and probe payers are excluded from every ranking. A paying
   fidelity probe means real recurring spend of *unbounded, adversary-set*
   size: an endpoint can list at any price and farm our probe wallet every
   sweep. Price caps and cadence caps mitigate but don't remove the
   economics; this is precisely the "real financial exposure → owner
   decision first" category the roadmap's sequencing note names. (It is not
   wash volume — we'd be paying third parties, not ourselves — but the
   probe-pays-us case for our own listings *would* need the same probe-payer
   exclusion the grading module already applies.)
2. **"Matches what the listing claims" is a judgment call, not a
   measurement.** Listings today carry free-text descriptions and tags, not
   machine-checkable response schemas. Judging content against prose means
   an LLM verdict — fuzzy, gameable, and it puts the marketplace's name
   behind correctness verdicts it cannot stand behind (the same category
   error roadmap item 25 already flags: there is no formal ground truth for
   "did what it claimed"). Worse, it would poison the probe's one asset —
   §9 item 20's decision note is explicit that the measured layer stays a
   pure, free, mechanical trust signal.
3. **Grading already delivers the honest version.** Agents who *provably
   paid the endpoint* (on-chain receiver+sender-verified txid) score what
   they actually received, sybil-weighted by their own settled spend, with
   self-grades excluded. That is fidelity signal produced at zero marginal
   cost to us, by the parties actually positioned to judge it.

The one narrow, deterministic, defensible subset: an *optional*
machine-readable response schema on a listing, plus a paid probe that checks
schema conformance only (parses, validates, no content judgment). Honest,
but still inherits problem 1's spend economics and requires listers to
declare schemas nobody has asked to declare.

**Recommendation: don't build the content-fidelity version — grading is the
existing answer; steer demand there.** The schema-conformance subset is
"needs a real owner design decision first" (recurring probe spend, price-cap
policy, listing-schema extension) and should not start on an agent's call.

## Item 4 — Two-sided records / two-sided reputation

*"Worker reputation from provenance, requester reputation from quality of
use — a pilot concept."*

**Both sides substantially exist already; the proposal is redundant.**

- **Worker (endpoint) side — three live layers:** the probe's verified badge
  (listed payTo matches the live 402 offer's payTo — provenance, exactly),
  free uptime/latency/402-validity history on every listing read, and the
  paid grading aggregate. A fourth parallel reputation surface would dilute
  the existing ones, and §9 item 20's owner decision already killed the
  "separate paid reputation ledger" shape for this exact reason.
- **Requester (payer) side — exists where it has any use:**
  `x402_grading/services/credibility.py` literally *is* requester
  reputation — a wallet's all-time settled spend with the marketplace,
  ledger-backed and sybil-resistant by construction, weighting that wallet's
  influence where influence matters (grades). Beyond that, x402 is
  settle-before-serve: no credit is ever extended to a requester, so there
  is almost nothing for a requester-reputation score to gate. The fuller
  "who is this agent, how does it behave" product is **KYA** (roadmap
  item 8, `modules/kyc/`) — code-complete, registered, deliberately gated
  off in prod by owner decision.

**Recommendation: don't build.** If external demand for requester identity
firms up into something concrete, the answer is the owner's existing
decision point — whether to un-gate KYA — not a new pilot.

## Item 5 — Session-nonce privacy variant

Only meaningful if item 1's attestation exists — and then it comes free:
item 1's feasible form is already a *hash commitment* (nothing about the raw
input/output pair is derivable from `H(input‖output‖nonce)`; revealing the
preimage to a chosen auditor is inherent to hash commitments, no extra
machinery). Anything stronger — proving *properties* of a hidden response —
is zero-knowledge territory and belongs to roadmap item 25's explicitly
design-gated "wondering if possible" bucket.

**Recommendation: no separate work.** Fold the commitment-not-plaintext
requirement into item 1's design *if* item 1 is approved; otherwise moot.

---

## Overall verdict

The cluster is one theme wearing four hats, and the marketplace has already
answered the enforceable parts of it: delivery failure → auto-refund +
`fulfilled` ledger; liveness/identity → free probe + verified badge; content
quality → paid, usage-proven, spend-weighted grading; requester standing →
credibility weighting now, KYA when the owner un-gates it. None of items
1–5 is literally the rejected escrow, but the cluster's implied endgame
("attest → check → dispute → remedy") re-derives it the moment anyone adds
an enforcement arm — so every design here must terminate in *evidence and
reputation*, which is what the existing stack already produces.

**The one thing worth pursuing** (behind an explicit owner decision, per the
roadmap's flag-and-stop pattern): the **server-signed fulfillment receipt**
from item 1 — a response header on our own paid routes binding settlement
txid ↔ request hash ↔ response hash under a published key, hash-commitment
only, spec published, optionally self-declarable by third-party listings.
Non-custodial, no protocol break, small surface, composes with the
facilitator's existing settlement receipt, and it is the only idea in the
cluster that adds something the baseline doesn't already do. Everything else:
already covered, already rejected in substance, or gated on spend/design
decisions that are the owner's to make.

## Not covered / assumptions

- Demand attribution (which agents, how many) was treated as unverified per
  the task; nothing above depends on it.
- "Settlement memo is payer-signed before the response exists" is derived
  from the verified client-signer flow in `docs/x402-facilitator.md`; if the
  facilitator ever adds a post-settlement annotation mechanism, item 1's
  "impossible as specified" narrows to "impossible today".
