# Client-side encryption for Agent backup storage

This is a companion to the `storage` product's routes (see
[`x402-marketplace-api.md`](x402-marketplace-api.md#agent-backup-storage) for
the full route reference, [`x402-quickstart.md`](x402-quickstart.md) for the
walkthrough). It exists because agents keep asking the same question:
*"is my backup actually private, and how do I encrypt it before I upload?"*

## What we actually do with your bytes

Straight from `POST /api/v1/x402/storage/backups`'s own 402 offer
description: stored content is **opaque** — never scanned, indexed, or acted
on by us. That is true today and stays true for versioned backups too. It is
**not confidential from us**, though: an operator can inspect or remove a
specific backup for abuse/legal response (no other agent can — every read
requires proving control of the wallet that created it, fresh, on every
call — see the auth-challenge flow in the API reference). If that operator
capability matters to your threat model, encrypt before you upload. This
page gives you a real recipe, not "use encryption" hand-waving.

The `data` field in every write/read is base64 of whatever bytes you send —
we never decode it into anything else, so you're free to base64 the output
of a normal encryption tool directly.

## Recommended: `age` (simple, modern, small ciphertexts)

[`age`](https://github.com/FiloSottile/age) is a modern encryption tool
purpose-built for "encrypt this file to a key, decrypt it later" — no
passphrase-management ceremony, no cipher-suite bikeshedding, and it is
packaged for every major OS (`apt install age`, `brew install age`,
or a static binary from the releases page).

### 1. Generate a keypair once, keep the private key safe

```bash
age-keygen -o backup-key.txt
# Public key: age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpq...
```

`age-keygen` prints the public key to stdout (also saved as a comment inside
`backup-key.txt`). Keep `backup-key.txt` (the private key) somewhere that
survives whatever disaster this backup is *for* — it is the only thing that
can ever decrypt what you're about to upload. Losing it means the backup is
unrecoverable, by design; we never see it and can't help.

### 2. Encrypt, then base64, then upload

```bash
AGE_PUBLIC_KEY="age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpq..."

age -r "$AGE_PUBLIC_KEY" -o backup.bin.age agent-state.json
DATA_B64=$(base64 -w0 backup.bin.age)   # macOS: base64 -i backup.bin.age

curl -s -X POST "https://algorand-api.pxke.me/api/v1/x402/storage/backups?declared_size_bytes=$(stat -c%s backup.bin.age)" \
  -H "Content-Type: application/json" \
  -H "X-PAYMENT: <your signed x402 payment header>" \
  -d "{\"data\": \"$DATA_B64\", \"label\": \"agent-state-2026-09-06\"}"
```

`declared_size_bytes` must be the size of the **encrypted** file (what you
are actually charged for and actually uploading), not the plaintext's size —
age's ciphertext is slightly larger than the plaintext (a small fixed
header plus per-chunk MAC overhead), so size it off `backup.bin.age`, not
`agent-state.json`.

### 3. Retrieve and decrypt

```bash
curl -s "https://algorand-api.pxke.me/api/v1/x402/storage/backups/<backup_id>?wallet=<addr>&nonce=<nonce>&proof_method=signed_bytes&signature_b64=<sig>" \
  | python3 -c 'import sys, json, base64; sys.stdout.buffer.write(base64.b64decode(json.load(sys.stdin)["data"]))' \
  > backup.bin.age

age -d -i backup-key.txt -o agent-state.json backup.bin.age
```

The same recipe works for a specific historical version — swap the URL for
`GET .../versions/:version` and the response shape is identical (`data` is
still base64 of the same `age` ciphertext you originally uploaded for that
version).

### Passphrase variant (no keypair to manage)

If you'd rather remember a passphrase than manage a key file:

```bash
# Encrypt
age -p -o backup.bin.age agent-state.json    # prompts for a passphrase twice

# Decrypt
age -d -o agent-state.json backup.bin.age    # prompts for the same passphrase
```

## Alternative: `openssl` (no extra install on most systems)

If you can't install `age`, OpenSSL (already present on nearly every Linux/
macOS box) does the same job with AES-256 and a KDF-stretched passphrase:

```bash
# Encrypt (prompts for a passphrase twice)
openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
  -in agent-state.json -out backup.bin.enc

DATA_B64=$(base64 -w0 backup.bin.enc)
# ... upload backup.bin.enc's bytes (as $DATA_B64) exactly as in the age example above,
#     with declared_size_bytes = stat -c%s backup.bin.enc
```

```bash
# Decrypt (after retrieving and base64-decoding the same way as above)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -in backup.bin.enc -out agent-state.json
```

`-pbkdf2 -iter 200000` matters: without it, older OpenSSL defaults use a
much weaker key-derivation scheme. Use the same `-iter` value (or higher) on
decrypt that you used on encrypt, or the passphrase will silently fail to
derive the right key.

## What NOT to do

- Don't upload plaintext and rely on "nobody will look" — the operator
  access described above is real, not theoretical (it exists for abuse/legal
  response, same as every other paid storage product's terms).
- Don't reuse the same `age` passphrase or key across unrelated backups if
  you'd ever want to selectively share access to one without the others —
  a keypair per logical backup, or per agent identity, composes better than
  one universal key.
- Don't forget `declared_size_bytes` must match the size of the bytes you
  actually send (after encryption, before base64) — see
  `POST /storage/backups`'s own `size_mismatch` error in the API reference
  if this trips you up: payment is kept either way, so getting this right
  the first time saves you a wasted call.
