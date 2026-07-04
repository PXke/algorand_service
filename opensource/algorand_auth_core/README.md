# algorand_auth_core

Minimal Algorand primitives for **wallet authentication** only:

- Base32-checksummed address decode
- Algod `GET /v2/transactions/params` via `package:http`
- Unsigned 0-ALGO self-payment msgpack encoding (ARC-0025 login txn)

This replaces the `algorand_dart` + `dio` dependency chain in `wallet_auth_flutter`.
Encoding is verified against `algorand_dart` 1.0.3 in `test/algorand_dart_compat_test.dart`.

`algorand_dart` remains a **dev_dependency** here for that regression test only; it is
not shipped to the app.
