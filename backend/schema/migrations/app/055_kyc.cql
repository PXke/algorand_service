USE algorand_platform;

-- KYC-as-a-service (Algorand Global x402 Challenge product): a wallet
-- enrolls once, free, self-service -- we compute trust signals and store a
-- level. Third parties later pay an x402-gated endpoint to look this up,
-- splitting the fee with the enrolled wallet. wallet_address is the natural
-- partition key: every read pattern is "give me the current level for this
-- wallet". Re-enrollment overwrites (latest snapshot, not history).
CREATE TABLE IF NOT EXISTS kyc_enrollments (
  wallet_address text PRIMARY KEY,
  enrolled_at timestamp,
  updated_at timestamp,
  consent_signature_b64 text,
  wallet_age_round bigint,
  recent_tx_count int,
  kyc_level text
);

-- Append-only: every paid lookup (found or not) plus its payout outcome,
-- keyed for admin review and payout retry. Not the primary read path
-- (kyc_enrollments is) -- this is the audit trail, since kyc_enrollments
-- overwrites on re-enrollment and a failed payout needs somewhere to be
-- found again for a manual retry.
CREATE TABLE IF NOT EXISTS kyc_lookup_events (
  wallet_address text,
  created_at timeuuid,
  payer_address text,
  payment_txid text,
  found boolean,
  payout_status text,
  payout_txid text,
  payout_error text,
  PRIMARY KEY ((wallet_address), created_at)
) WITH CLUSTERING ORDER BY (created_at DESC);
