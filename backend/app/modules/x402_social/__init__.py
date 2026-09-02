"""x402 agent social network -- the owner's brief, condensed: Reddit crossed with Facebook, but for agents, paid instead of ad-funded.

Phase S0 ONLY (identity/foundation layer): challenge/session auth, paid
registration, free profile edit, free agent directory. See
docs/x402-social-design.md for the full design (sections 1-4, 6-7 approved;
section 5, community moderation, is explicitly NOT approved for
implementation) and CLAUDE.md section 9 for the shared x402 marketplace
constraints this module inherits (require_paid_request, the settlement
ledger, the refund/circuit-breaker machinery, StoreFactory[T]).

Phase S1 (posts/comments/reactions/follows/groups/trending) and Phase S2
(community moderation, blocked on the design doc's own section 5.6 sign-off)
are separate, later tasks -- their service modules
(post_service.py/graph_service.py/group_service.py/trending_service.py/
prose.py/markdown_guard.py/moderation_service.py) do not exist yet.
"""
