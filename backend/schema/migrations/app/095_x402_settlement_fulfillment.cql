USE algorand_platform;

-- Fulfillment status on the x402 settlement ledger (090).
--
-- require_paid_request settles the payment and writes the ledger row, and
-- the route's product write happens afterwards. Before this migration a
-- product write that raised left the payer charged with no durable record
-- that they got nothing for it. fulfilled is written false at settlement time
-- and flipped to true by modules/x402/settlement.py's mark_fulfilled once the
-- route has stored the product (store before mark). A row that stays false
-- is a paid-but-unfulfilled settlement for an operator to reconcile.
ALTER TABLE x402_settlements ADD fulfilled boolean;

-- By-txid lookup for the ledger. x402_settlements is keyed
-- ((day), settled_at, tx_id) and a route only holds the txid, so flipping
-- fulfilled needs the other two key parts -- and finding them by tx_id alone
-- would be ALLOW FILTERING on a non-partition column (CLAUDE.md section 4:
-- add a lookup table instead, same precedent as 087/089). Denormalizes the
-- whole row so an operator can also answer "what did this txid buy" with one
-- point read. Written second, after the ledger row, so its presence implies
-- the ledger row exists.
CREATE TABLE IF NOT EXISTS x402_settlements_by_tx (
  tx_id text PRIMARY KEY,
  day text,
  settled_at timestamp,
  asset_id text,
  amount_atomic text,
  payer text,
  resource text,
  network text,
  eur_value double,
  fulfilled boolean
);
