USE algorand_platform;

-- Promo-code payment bypass, shared across every x402 module
-- (app/modules/x402/promo.py), CLAUDE.md section 9 -- a bounded number of
-- free redemptions of ONE resource (paid route), admin-issued, not a
-- discount or a balance: a successful redemption is never a payment, never
-- reaches the settlement ledger (090), and never gets a settlement_tx_id.
--
-- x402_promo_codes is the durable, admin-managed record. `code` is the sole
-- key: an admin issues one code per campaign/resource, POST
-- /api/v1/admin/x402/promo creates it (a lightweight INSERT ... IF NOT
-- EXISTS transaction, so two concurrent admin creates of the same code
-- cannot silently overwrite each other's resource/count/expiry) and DELETE
-- /api/v1/admin/x402/promo?code= flips `active` false with UPDATE ... IF
-- EXISTS (same precedent as the probe badge, 097 -- never upserts a phantom
-- row for a code that was never created). `resource` scopes the code to
-- exactly one paid route's resource id -- `starting_count` is the durable
-- total redemption budget this table is the source of truth for --
-- modules/x402/promo.py seeds a Redis key from it on first use (SET NX, so
-- concurrent first-uses cannot double-seed) and DECRements that Redis key
-- atomically on every attempt, which is the actual hot-path guard -- this
-- table is read once per attempt to resolve the code (never on the DECR
-- itself). `expires_at` is nullable (no expiry) and checked in Python
-- against the current time, not a Cassandra TTL -- a TTL would drop the row
-- entirely, and an admin needs to see and manage an expired code, not have
-- it vanish out from under the audit trail.
--
-- x402_promo_redemptions is the append-only audit log AND the whole
-- per-(code, wallet) abuse cap, in one table: its primary key IS (code,
-- wallet_hash), so the INSERT ... IF NOT EXISTS that writes the audit row is
-- ALSO the atomic once-per-wallet-per-code claim -- no separate
-- read-then-write race (CLAUDE.md section 3). It mirrors the settlement
-- ledger's honesty principle: only a redemption that actually bypassed
-- payment is ever recorded here (modules/x402/promo.py undoes its reserved
-- Redis slot, by re-incrementing, whenever this LWT insert does not apply or
-- itself fails, so a rejected attempt never burns a slot from the durable
-- total). `wallet_hash` is a sha256 hex digest of the caller-supplied wallet
-- address (X-Promo-Wallet / ?promo_wallet=) -- the raw address is never
-- stored or logged for this feature. The wallet is checked for syntactic
-- validity only (algosdk.encoding.is_valid_address) -- there is no signature
-- proof behind it, a deliberate, documented soft deterrent (owner-accepted),
-- not cryptographic ownership proof.
CREATE TABLE IF NOT EXISTS x402_promo_codes (
  code text PRIMARY KEY,
  resource text,
  starting_count int,
  created_at timestamp,
  expires_at timestamp,
  active boolean
);

CREATE TABLE IF NOT EXISTS x402_promo_redemptions (
  code text,
  wallet_hash text,
  redeemed_at timestamp,
  resource text,
  PRIMARY KEY ((code), wallet_hash)
);
