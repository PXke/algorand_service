"""x402 agent backup storage -- roadmap item 12 ("pay-per-MB storage") in CLAUDE.md section 9.1.

An agent pays to upload an opaque blob (its own "backup" -- state, config,
whatever it wants) and later retrieves or deletes it, authenticated purely by
proving control of its Algorand wallet at request time -- NOT a stored
session, because a disaster-recovery restore must not depend on session state
that might be exactly what was lost. See services/auth_service.py.

Starts local-disk-only (backends/local.py). A second cloud connector (Wasabi)
is an explicit future addition behind the same StorageBackend Protocol
(backends/base.py), not built now -- backends/factory.py only ever resolves
"local" today.

Nothing custodial: this module stores arbitrary bytes, never funds. Stored
content is treated as opaque everywhere in this module -- never inspected,
parsed, or used for anything beyond storage and integrity verification
(content_hash). Every response and discovery description says plainly that
uploaded content has no confidentiality guarantee beyond owner-only access
control -- a caller storing sensitive data must encrypt it themselves first.
"""
