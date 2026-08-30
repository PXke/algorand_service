"""x402 paid visibility board: a paid placement endpoint and a free public feed.

The Algorand Global x402 Challenge entry's third paid product (after
modules/kyc and modules/x402_directory), roadmap item 2 in CLAUDE.md section
9.1. An agent pays a flat x402 fee through the shared require_paid_request
gate to place one link plus a short pitch for a fixed term; the board is then
readable for free by anyone.

Deliberately NOT the directory. The directory (modules/x402_directory)
catalogues paid endpoints an agent can call, so it carries the callee's price,
accepted assets and request schema. The board is pure presence -- "we exist,
here is our link" -- so it carries none of that: a name, a link, a pitch, and
the term the placement was paid for. Resist growing it back into the
directory; if a field only makes sense for something callable, it belongs
there, not here.
"""
