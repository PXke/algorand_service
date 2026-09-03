USE algorand_platform;

-- Signed fulfillment receipts (owner conversation 2026-09-03, see
-- docs/x402-execution-trust-evaluation.md item 1, "Verifiable execution /
-- attestation schema"). Right after a paid route's product write succeeds
-- (modules/x402/paid_request.run_with_refund), a server-signed receipt binds
-- exactly what was delivered to exactly what was paid for:
-- sig(H(request body) || H(response body) || settlement_tx_id || timestamp).
-- Evidence, not a guarantee -- see the doc, do not re-derive here.
--
-- A DEDICATED table -- NEVER the settlement ledger (x402_settlements /
-- x402_settlements_by_tx, migrations 090/095/102). That ledger holds
-- transaction metadata ONLY, permanently, by design (CLAUDE.md section 9).
-- Receipt content is stored content that may need to go away independently
-- of the financial record (same principle the social network's moderation
-- hard-delete work established this same session: financial history stays
-- immutable forever, content sometimes must not) -- hence its own table with
-- its own TTL, joined back to the ledger via settlement_tx_id (a plain
-- column here, no new column added to the ledger side).
--
-- Keyed by receipt_id (a fresh uuid per receipt), not settlement_tx_id
-- directly: today's mark_fulfilled/run_with_refund flow is 1:1 per
-- settlement, but a receipt_id lets a future design attach more than one
-- receipt to a single settlement without a schema change, and never
-- collides with the ledger's own key space.
--
-- 90-day retention (x402_receipt_ttl_days), enforced here via
-- default_time_to_live -- deliberately temporary evidence, not a permanent
-- record. output is capped at write time to x402_receipt_output_max_chars
-- (app/core/config.py) -- output_truncated says whether the retained copy is
-- the full response or a truncated prefix -- the signature itself always
-- covers the FULL response hash regardless of this cap, so a truncated
-- stored copy is a display limitation, never something the signature lies
-- about.
--
-- See app/modules/x402/receipt_store.py (storage) and
-- app/modules/x402/receipts.py (signing/generation).
CREATE TABLE IF NOT EXISTS x402_fulfillment_receipts (
  receipt_id uuid PRIMARY KEY,
  settlement_tx_id text,
  resource text,
  signing_address text,
  signature text,
  request_hash text,
  response_hash text,
  output text,
  output_truncated boolean,
  issued_at timestamp,
  issued_at_epoch bigint
) WITH default_time_to_live = 7776000;
