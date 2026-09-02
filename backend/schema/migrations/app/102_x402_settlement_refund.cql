USE algorand_platform;

-- Refund tracking on the x402 settlement ledger (090, extended by 095's
-- fulfilled flag).
--
-- modules/x402/paid_request.run_with_refund lets a route send the full
-- settled amount back to the payer when its product write raises after
-- payment already settled (owner requirement 2026-09-02: "not a 500, not a
-- 400 -- a 200 or your money back"). refund_status is written once a refund
-- is attempted ("sent" | "failed" | "skipped"). refund_tx_id is the ASA
-- transfer's own txid when a refund was actually sent, null otherwise. Both
-- stay null on a row that never needed a refund attempt -- do not confuse
-- "no refund needed" (fulfilled=true) with "refund not yet attempted"
-- (fulfilled=false, refund_status=null): the latter is still an open
-- reconciliation item.
ALTER TABLE x402_settlements ADD refund_tx_id text;
ALTER TABLE x402_settlements ADD refund_status text;

ALTER TABLE x402_settlements_by_tx ADD refund_tx_id text;
ALTER TABLE x402_settlements_by_tx ADD refund_status text;
