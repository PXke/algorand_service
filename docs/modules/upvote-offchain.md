# Brick: Off-chain upvotes

## Goal

One wallet → one upvote per suggestion via **Ed25519** signature (no on-chain vote txn).

## Status

`done`

## Features (should do)

- Canonical message: `algorand-platform:upvote:v1:{suggestion_id}:{wallet_address}`
- Verify signature with wallet public key (`algorand_verify`)
- `POST` upvote with `signature_b64`
- Reject duplicate `(suggestion_id, wallet)`
- Allow same wallet on **different** suggestions

## Good to have

- Return `upvote_count` after successful vote
- Pluggable `signature_verifier` in tests

## Future improvements

- Timestamp/nonce inside signed payload for cross-platform replay policy
- Upvote removal (signed “unvote”) 
- Weighted votes (governance token snapshot) — new brick
- Display verify failures with SIWA-style detail codes

## Standards & RFCs

| Reference | Use |
|-----------|-----|
| [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) | Ed25519 verify |
| Platform convention | `algorand-platform:upvote:v1:{id}:{wallet}` UTF-8 message |

[standards-and-rfcs.md](../architecture/standards-and-rfcs.md#upvote-offchain).

## Depends on

- `suggestions-store`, `wallet-auth`

## Code map

- `backend/app/modules/suggestions/services/upvote_service.py`
- `backend/app/modules/suggestions/services/upvote_message.py`
