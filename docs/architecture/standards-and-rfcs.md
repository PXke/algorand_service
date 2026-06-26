# Standards & RFCs — brick implementation index

Before implementing or changing a **brick**, check the references below. Prefer **normative** specs (IETF RFC, W3C, Algorand ARC/CAIP) over blog posts.

**Workflow:** [brick-implementation-guide.md](brick-implementation-guide.md)

**Brick docs** must include a **Standards & RFCs** section (see [modules/README.md](../modules/README.md)).

---

## How to use this index

| Priority | Source type | Examples |
|----------|-------------|----------|
| 1 | Algorand ARC / CAIP | ARC-0025, ARC-0060, CAIP-2, CAIP-122 |
| 2 | IETF RFC / BCP | RFC 8032 (Ed25519), RFC 8785 (JCS), RFC 9110 (HTTP) |
| 3 | W3C / WHATWG | Fetch (CORS), EIP-4361 (SIWA display) |
| 4 | Product docs | Typesense API, Cassandra CQL, Celery, Redis |
| 5 | Platform convention | Internal message formats (document in brick + ADR) |

If no RFC exists (e.g. custom upvote payload), define a **versioned platform convention** (`algorand-platform:…:v1:…`) and link Ed25519 / encoding RFCs for the crypto layer.

---

## Platform bricks

### `wallet-auth` / `wallet-auth-flutter` / `frontend-auth`

| Reference | Title | Use |
|-----------|-------|-----|
| [EIP-4361](https://eips.ethereum.org/EIPS/eip-4361) | Sign-In with Ethereum | SIWA human-readable message layout |
| [CAIP-122](https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-122.md) | Sign-In with Algorand | JSON payload in ARC-0060 `data` |
| [CAIP-2](https://github.com/ChainAgnostic/CAIPs/blob/master/CAIPs/caip-2.md) | Blockchain ID | `algorand:…` chain id in CAIP-122 |
| [ARC-0025](https://arc.algorand.foundation/ARCs/arc-0025) | WalletConnect for Algorand | WC session, chain ids 4160xx, `algo_signTxn` |
| [ARC-0060](https://arc.algorand.foundation/ARCs/arc-0060) | ARC-0060 AUTH | `algo_signData` / AUTH scope, signing input |
| [ARC-0001](https://arc.algorand.foundation/ARCs/arc-0001) | Wallet transaction JSON | `WalletTransaction` for `algo_signTxn` |
| [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785) | JSON Canonicalization (JCS) | ARC-0060 `clientDataHash` |
| [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) | Ed25519 | Signature verify |
| [RFC 4648](https://www.rfc-editor.org/rfc/rfc4648) | Base64 | Wire encoding |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | HTTP Semantics | 401, 429, cache headers (future) |

Deep dive: [wallet-auth-protocol.md](wallet-auth-protocol.md), ADR-0002.

### `session-store`

| Reference | Use |
|-----------|-----|
| Redis protocol / key TTL | Session + nonce storage |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | Session token as opaque bearer (no JWT in v1) |

### `web-platform`

| Reference | Use |
|-----------|-----|
| [Fetch — CORS](https://fetch.spec.whatwg.org/#cors-protocol) | Browser cross-origin to API |
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | Credentialed vs simple requests |
| EIP-4361 / CAIP-122 | `AUTH_DOMAIN` must match SIWA `domain` |

### `cassandra-schema-migrations`

| Reference | Use |
|-----------|-----|
| [Apache Cassandra CQL](https://cassandra.apache.org/doc/latest/cassandra/developing/cql/) | DDL/DML |
| Internal | `schema_migrations` ledger convention |

### `health-observability`

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | 200 vs 503 readiness patterns |
| K8s probe conventions (de-facto) | Liveness vs readiness split |

### `quality-ci`

| Reference | Use |
|-----------|-----|
| Project test vectors | ARC-0060 reference vector in `test_arc0060_verify.py` |

### `deployment`

| Reference | Use |
|-----------|-----|
| [systemd unit](https://www.freedesktop.org/software/systemd/man/systemd.service.html) | Service units |
| Internal | rsync release layout |

### `celery-redis-queues`

| Reference | Use |
|-----------|-----|
| [Celery docs](https://docs.celeryq.dev/) | Task routing, beat |
| Redis | Broker protocol |

---

## Chain bricks

### `conduit-cassandra`

| Reference | Use |
|-----------|-----|
| [Algorand go-algorand-sdk](https://github.com/algorand/go-algorand-sdk) | Transaction types, encoding |
| [Conduit](https://github.com/algorand/conduit) | Follower importer, exporter plugin API |
| Cassandra CQL | Table design |

### `chain-read`

| Reference | Use |
|-----------|-----|
| Algorand transaction reference | `pay` / `appl` / `axfer` fields in `txn_json` |
| Internal | `IndexedTransaction` mapping from exporter |

---

## Product 1 — Newspaper

### `service-registry`

| Reference | Use |
|-----------|-----|
| Algorand address format | Base32 checkum addresses |
| Internal | `match_kind` enum |

### `chain-tail-watcher`

| Reference | Use |
|-----------|-----|
| Algorand round semantics | Monotonic round processing |
| Internal | Redis cursor keys |

### `worker-scraper`

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | HTTP GET |
| [RFC 9309](https://www.rfc-editor.org/rfc/rfc9309) | robots.txt (good to have) |
| [HTML5](https://html.spec.whatwg.org/) | Parsing boundaries |

### `worker-pipeline`

| Reference | Use |
|-----------|-----|
| Internal | Unified diff format (document cap) |

### `article-compose`

| Reference | Use |
|-----------|-----|
| [CommonMark](https://spec.commonmark.org/) (informative) | Markdown body in feed |
| OWASP XSS Cheat Sheet | Sanitize before store |

### `article-store` / `news-api`

| Reference | Use |
|-----------|-----|
| Cassandra CQL | Time-series feed clustering |
| [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) | JSON API responses |

### `frontend-newspaper`

| Reference | Use |
|-----------|-----|
| [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) | JSON from API |
| CommonMark (future) | Render body |

---

## Product 2 — Suggestions

### `submission-on-chain`

| Reference | Use |
|-----------|-----|
| [Algorand transaction spec](https://developer.algorand.org/docs/get-details/transactions/) | `pay` type, amounts in microAlgos |
| ARC / developer docs | Treasury address encoding |

### `upvote-offchain`

| Reference | Use |
|-----------|-----|
| [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032) | Ed25519 signatures |
| Platform convention | `algorand-platform:upvote:v1:{id}:{wallet}` UTF-8 message |

### `suggestions-api` / `suggestions-store`

| Reference | Use |
|-----------|-----|
| [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110) | 401, 409, 400 |
| [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) | Request/response JSON |

### `frontend-suggestions`

| Reference | Use |
|-----------|-----|
| Same as `upvote-offchain` + `wallet-auth` | Sign + session header |

---

## Product 3 — Search

### `typesense-indexer` / `search-api`

| Reference | Use |
|-----------|-----|
| [Typesense API](https://typesense.org/docs/api/) | Collection schema, search |
| [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259) | JSON |

### `algorand-page-classifier`

| Reference | Use |
|-----------|-----|
| TBD before implementation | Document ML/data ethics separately |

### `frontend-search`

| Reference | Use |
|-----------|-----|
| [RFC 3986](https://www.rfc-editor.org/rfc/rfc3986) | Query string encoding |

---

## Workers

### `worker-security`

| Reference | Use |
|-----------|-----|
| Algorand txn encoding (msgpack) | Future full decode |
| Internal | Keyword heuristics until proper decode |

---

## Quick lookup by brick name

| Brick | Primary standards |
|-------|-------------------|
| `wallet-auth` | CAIP-122, ARC-0060, ARC-0025, RFC 8785, RFC 8032, EIP-4361 |
| `web-platform` | Fetch CORS, RFC 9110 |
| `upvote-offchain` | RFC 8032, platform message v1 |
| `submission-on-chain` | Algorand `pay` txn |
| `conduit-cassandra` | Conduit + go-algorand-sdk |
| `worker-scraper` | RFC 9110, HTML5 |
| `search-api` | Typesense API |
| `article-compose` | CommonMark (informative), OWASP XSS |

---

## Maintaining this index

When a brick gains a new wire format or crypto step:

1. Add row(s) here and in the brick’s **Standards & RFCs** section.
2. Add or extend tests (vectors from ARC/EIP where available).
3. Link from [wallet-auth-protocol.md](wallet-auth-protocol.md) or new protocol doc if cross-cutting.
