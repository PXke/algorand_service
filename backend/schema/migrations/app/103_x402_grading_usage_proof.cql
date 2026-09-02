USE algorand_platform;

-- Usage-proof-of-payment for grading (owner ask 2026-09-02): every grade
-- submission now names a payment txid, independently verified on-chain
-- against the graded endpoint's own payTo before the payment gate -- see
-- app/modules/x402_grading/services/usage_proof.py. This does NOT change
-- credibility weighting (services/credibility.py, untouched). It is a
-- visible per-grade flag only.
--
-- ADD COLUMN is safe here, no primary key involved. A pre-103 row has no
-- value for this column at all -- reads back null, mapped to False in
-- Python (CassandraGradeStore._row_to_grade), never as a false claim that
-- an old grade was verified.
ALTER TABLE x402_grades ADD usage_verified boolean;
