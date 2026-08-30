"""x402 endpoint grading: paid grade submission, paid weighted score, free index.

The Algorand Global x402 Challenge entry's fifth paid product (after
modules/kyc, modules/x402_directory, modules/x402_board and
modules/x402_features), roadmap item 6 in CLAUDE.md section 9.1. An agent pays
a flat x402 fee through the shared require_paid_request gate to grade one
endpoint 1-5 stars with an optional one-line opinion; anyone can pay a higher
fee to read that endpoint's aggregate score.

**Any URL can be graded.** The grader names the endpoint, and that is the whole
input. It does not have to be listed in x402_directory, or known to this
backend at all -- this module has NO dependency on x402_directory, does not
import it, and does not look anything up in it.

**Credibility comes from the grader's track record with US.** There is no
proof-of-payment-to-the-graded-endpoint gate, because there cannot be one: a
payment to a third party's x402 endpoint goes to their payTo through their
facilitator and never traverses our gate, so it is not in our settlement ledger
by construction, and no amount of querying that ledger will produce it. What
the ledger CAN answer exactly is how much a wallet has paid this marketplace in
total, across every product. That total is the weight each grade carries in the
published average -- a costly, ledger-backed, sybil-resistant signal that a
wallet cannot fake and cannot multiply by splitting itself. See
services/credibility.py.

**The "stake" is the payment, and nothing is ever held.** The roadmap phrase
"agents pay a small stake to grade endpoints" is a costly signal, not an
escrow: the one-time x402 payment IS the stake, it is settled straight to the
receive-only payTo address like every other payment in this marketplace, and
there is no refund, no forfeiture, no slashing and no held balance anywhere in
this module. The credibility weight is likewise a read-time sum over settlement
rows, never a balance this module controls. CLAUDE.md section 9 bars this
project from holding user funds at all, and roadmap item 5 (the bounty board)
is where a real escrow primitive would live -- behind a smart contract, not
started. If a future change to this module starts tracking a payment pending
some later outcome, that change is out of scope by construction.

The only cross-module read is modules/x402/settlement.py's ledger, through
services/credibility.py. That dependency is READ-ONLY and one-directional:
nothing here writes to, imports routes from, or otherwise mutates another
module.
"""
