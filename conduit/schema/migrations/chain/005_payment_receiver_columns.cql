-- Upgrade path for environments created from 003 before receiver columns existed.

ALTER TABLE transactions_by_round ADD receiver text;
ALTER TABLE transactions_by_round ADD amount_microalgos bigint;
ALTER TABLE transactions_by_id ADD receiver text;
ALTER TABLE transactions_by_id ADD amount_microalgos bigint;
ALTER TABLE transactions_by_sender ADD receiver text;
ALTER TABLE transactions_by_sender ADD amount_microalgos bigint;
