# Brick implementation guide

Use this checklist whenever you **implement** or ** materially change** a brick.

## 1. Read the brick doc

- [modules/README.md](../modules/README.md) — template
- `docs/modules/<brick>.md` — Features / Good to have / Future improvements
- [products-and-bricks.md](products-and-bricks.md) — dependencies

## 2. Standards & RFCs (required)

1. Open [standards-and-rfcs.md](standards-and-rfcs.md) for your brick name.
2. Read **normative** sources (RFC, ARC, CAIP, W3C) — not only Stack Overflow.
3. Add or update the brick’s **Standards & RFCs** table if you introduce a new format.
4. Add **test vectors** when the spec provides them (e.g. ARC-0060, EIP-4361 layout).

If the spec is ambiguous, document the choice in the brick doc or an ADR.

## 3. Implement with minimal scope

- Match existing code style and paths in **Code map**.
- Do not expand into adjacent bricks without updating their docs.

## 4. Verify

- Unit tests for crypto, parsing, and message formats.
- Manual TestNet check when the brick touches chain or wallets.

## 5. Update status

- Set brick **Status** in `docs/modules/<brick>.md`.
- Update the table in [modules/README.md](../modules/README.md) and [products-and-bricks.md](products-and-bricks.md).

## Cross-cutting protocol docs

| Topic | Doc |
|-------|-----|
| Wallet login | [wallet-auth-protocol.md](wallet-auth-protocol.md) |
| API errors | `PlatformError` + `{ "error": { "code", "message" } }` in `app/core/errors.py` |
| CQL deploy | [cql-migrations.md](cql-migrations.md) |
| Release | [release-cadence.md](release-cadence.md) |
