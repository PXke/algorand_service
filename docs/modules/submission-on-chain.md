# Brick: Submission on-chain (suggestions)

## Goal

Require a real **pay** to platform treasury before accepting a suggestion (anti-spam).

## Status

`done`

## Features (should do)

- Verify indexed txn exists (`chain-read`)
- Sender must equal session wallet
- Type must be `pay`
- Receiver must equal `PLATFORM_TREASURY_ADDRESS`
- Amount ≥ `SUGGESTION_MIN_MICROALGOS` (default 10_000 microAlgos)
- Parse receiver/amount from columns or `txn_json`
- Reject duplicate `submission_txid`

## Good to have

- Clear API errors: `tx_not_indexed`, `tx_not_valid_submission`, `treasury_not_configured`
- Document treasury address in public config endpoint

## Future improvements

- Optional note field schema for structured title/body on-chain (still display off-chain)
- Verify txn group if submission is part of atomic group
- Allow ASA payments to treasury ASA (not only ALGO)
- Time window: txn must be within last N rounds

## Standards & RFCs

[Algorand `pay` transactions](https://developer.algorand.org/docs/get-details/transactions/) — amount in microAlgos, receiver = treasury. [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#submission-on-chain).

## Depends on

- `chain-read`, `conduit-cassandra` (receiver columns)

## Code map

- `backend/app/modules/chain/verify.py`
- `backend/app/modules/chain/payment.py`
