"""x402 feature-request board: paid requests, paid votes, free browse, paid demand.

The Algorand Global x402 Challenge entry's fourth paid product (after
modules/kyc, modules/x402_directory and modules/x402_board), roadmap item 4 in
CLAUDE.md section 9.1: "agents pay to request an endpoint and vote; builders
pay to read demand."

Four surfaces, not the two the directory and the board each have:

  POST /api/v1/x402/features            paid  -- submit a request
  POST /api/v1/x402/features/:id/vote   paid  -- add one unit of demand to it
  GET  /api/v1/x402/features            free  -- browse what is being asked for
  GET  /api/v1/x402/features/demand     paid  -- the ranked demand signal

The free/paid split is the product. The free browse answers "what has anyone
asked for" -- existence only, no vote counts. The paid demand read answers
"what do builders actually want most" -- the ranking, with the numbers. That
asymmetry is deliberate: the demand signal is the aggregate of every vote
anyone has paid for, and it is the thing a builder deciding what to build next
would actually pay for. Giving the counts away on the free surface would leave
the paid one selling nothing.

A vote is a payment, and paying again votes again. See vote_total in
services/feature_service.py for why this is not one-vote-per-wallet.
"""
