# x402 / GoPlausible facilitator — verified reference

Compiled 2026-08-29 for the Algorand Global x402 Challenge build. Every value
here was checked against a primary source (the official rules PDF, the
challenge's own submission-guide blog post, the `algorandfoundation/x402-demo`
repo, or — where those were silent or vague — the actual `x402-avm==2.0.2`
package already installed in `backend/.venv`). Do not substitute a value from
memory or a different package version; re-verify if `x402-avm` is upgraded.

## Competition facts (from the official rules PDF + registration page)

- **Registration window**: closes 11:45pm ET **Sept 1, 2026**, then the form
  is disabled. Eligibility to register requires a paid x402 endpoint already
  **deployed and reachable on Algorand Mainnet**, using the GoPlausible
  facilitator. Registration is per-Individual/Team, real identity (name,
  email, "Profile Information", legal attestations) — not something an agent
  submits unattended.
- **Final Presentation submission window**: Sept 2 → Sept 29. To be eligible
  for one of the 10 finalist slots, the project must be **top 50 on the
  leaderboard** by Sept 29, and all requested project info submitted.
- **Assessment criteria, evenly weighted**: Volume ("the amount of **USDC**
  processed through the submitted x402 endpoint" — not multi-asset volume),
  Use-case quality (x402 must be load-bearing, not bolted on), Sustained
  potential, Innovation.
- **Entry categories** (from the submission-guide blog, not in the rules PDF):
  - **Standard** — one project, one endpoint, one price.
  - **Composite** — several endpoints under one project, same `payTo`,
    individually discoverable, roll up under one merchant on the leaderboard.
    **This is almost certainly our category** — the directory/KYC/probe/
    bounties marketplace is exactly this shape.
  - **Orchestrator** — the product's own endpoint settles the client's
    payment first, then pays *other teams'* downstream endpoints from its own
    wallet as part of its workflow. Only the probe (paying other listed
    endpoints) resembles this, and it's explicitly excluded from ranking
    (labelled probe traffic) — so Composite, not Orchestrator, is the right
    top-level category.
  - Full definitions: https://algorand.co/blog/the-x402-global-challenge-is-live-how-to-build-submit-your-entry
- **Anti-gaming enforcement is real, not just good practice**: the
  Administrator explicitly reserves the right to audit and exclude
  "artificial volume, wash transactions, repeated self-payments" from
  leaderboard results (Official Rules §14). Every safeguard in CLAUDE.md §2
  (no self-payment except the labelled probe, amount-weighted not
  count-weighted ranking) is a disqualification-risk mitigation, not
  optional polish.
- EURQ/USDQ/USDT asset ids appear **nowhere** in official challenge material
  — multi-asset support needs independent research (Quantoz's own docs /
  Algorand asset explorer) and doesn't move the Volume score anyway. Treat as
  Phase 1, not a Phase 0 blocker.

Sources: [Official Rules PDF](https://algorand.co/hubfs/x402%20competition%20Official%20Rules.pdf) · [Registration page](https://algorand.co/global-x402-challenge) · [Submission guide](https://algorand.co/blog/the-x402-global-challenge-is-live-how-to-build-submit-your-entry) · [Developer guide](https://algorand.co/agentic-commerce/x402/developers)

## GoPlausible facilitator

- Base URL (default in this repo's `settings.x402_facilitator_url`):
  `https://facilitator.goplausible.xyz/`
- Endpoint paths, confirmed from the installed `x402/http/facilitator_client.py`:
  - `POST {base}/verify`
  - `POST {base}/settle`
  - `GET {base}/supported` (called once at process startup by
    `get_resource_server()` in `modules/x402/client.py` — do not call this
    per-request)
- Dashboard/leaderboard (from the submission guide, exact paths unverified
  live — check before relying on them): `{base}/dashboard`,
  `{base}/dashboard/leaderboards`, `{base}/discovery/resources`,
  `{base}/discovery/merchants`.

## CAIP-2 network ids and USDC asset ids

Verified directly from `x402/mechanisms/avm/constants.py` in the installed
package (authoritative — this is what our own code actually imports):

```python
MAINNET_GENESIS_HASH = "wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="
TESTNET_GENESIS_HASH = "SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="

ALGORAND_MAINNET_CAIP2 = f"algorand:{MAINNET_GENESIS_HASH}"
# = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="

ALGORAND_TESTNET_CAIP2 = f"algorand:{TESTNET_GENESIS_HASH}"
# = "algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="

USDC_MAINNET_ASA_ID = 31566704
USDC_TESTNET_ASA_ID = 10458941
```

`backend/app/core/config.py`'s existing `x402_network` default
(`"algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI="`) already matches
`ALGORAND_TESTNET_CAIP2` exactly — confirmed correct, no drift. **The mainnet
flip (§4.2) is: `x402_network = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8="`**,
done once, deliberately, per CLAUDE.md-to-be.

## The challenge tag — do NOT follow the official docs literally

The submission guide says: *"Simply add a field named 'tag' to your extra
field in your resource server x402 config."* **This does not work as stated**
against the installed `x402-avm==2.0.2`. Already root-caused in this repo
(`modules/x402/client.py`, `CHALLENGE_TAG` docstring): reading
`x402/server_base.py` shows `PaymentRequirements.extra` is built *only* from
`AssetAmount.extra` — `PaymentOption.extra` (which is what the naive reading
of the docs points at) is never read anywhere in that code path.

The only place the tag actually reaches the response is through the **money
parser** — `ExactAvmScheme.register_money_parser()`, the package's own
sanctioned extension point. This repo already implements it correctly:

```python
CHALLENGE_TAG = "x402-global-challenge"

def _tagged_money_parser(amount: float, network: str) -> AssetAmount:
    return AssetAmount(
        amount=str(to_atomic_amount(amount, DEFAULT_DECIMALS)),
        asset=str(get_usdc_asa_id(network)),
        extra={"decimals": DEFAULT_DECIMALS, "tag": CHALLENGE_TAG},
    )

def register_tagged_exact_avm_scheme(server, networks):
    scheme = ExactAvmScheme()
    scheme.register_money_parser(_tagged_money_parser)
    for network in ([networks] if isinstance(networks, str) else networks):
        server.register(network, scheme)
```

Any new paid module **must** route through `register_tagged_exact_avm_scheme`
(or the shared `get_resource_server()` singleton, which already does), never
construct its own `ExactAvmScheme()` directly — a module that does will ship
without the tag and silently fail the leaderboard's `src=x402-global-challenge`
requirement.

## Bazaar discovery extension

Confirmed from `x402/extensions/bazaar/resource_service.py` (already in
active use, `modules/kyc/api/routes.py`):

```python
from x402.extensions.bazaar import declare_discovery_extension

declare_discovery_extension(
    input={...},          # example input: query params (GET/HEAD/DELETE) or body (POST/PUT/PATCH)
    input_schema={...},   # JSON Schema for the input
    output={"example": {...}},
)
```

HTTP method is inferred from the route, not passed explicitly. Pass the
result as `RouteConfig.extensions` in `require_payment(...)` — see
`modules/x402/guard.py`'s existing usage.

Full merchant-level registration (`bazaar_resource_server_extension`) is
already wired once, process-wide, in `get_resource_server()` — new modules
don't re-register it, they just declare their own route's discovery
extension via `require_payment(extensions=...)`.

## Phase 0 acceptance — PASSED 2026-08-30

A real payment round-tripped end-to-end on TestNet against the live
GoPlausible facilitator: `POST /api/v1/x402/list` with no payment → `402` with
a correct offer → built + signed a real payment → retried → `200` with a
`settlement_tx_id` → independently confirmed on-chain via the public indexer
(not just trusted from our own backend's response) → confirmed the listing
appears in `GET /api/v1/x402/search`. Settlement tx
`VXFLM6A225ODFIV52XET7CZTYV22TF32562FA5IPZXJ74QHUXVNQ`, confirmed round
66808163, `asset-id 10458941`, sender = payer wallet, receiver = `payTo`
wallet, group-settled alongside the facilitator's own fee-payer leg (fee: 0 on
our transaction — the gasless abstraction genuinely works).

Two real prerequisites this surfaced, easy to miss:

1. **The `payTo` wallet must itself opt into every ASA it's meant to receive**,
   the same as any Algorand account. Ours (`x402_pay_to_address`) had never
   opted into TestNet USDC — every attempted settlement failed simulation with
   `receiver error: must optin, asset 10458941 missing from <payTo>` until it
   was opted in (and briefly funded with a small amount of ALGO to cover its
   own min-balance and the opt-in fee — 0.1 ALGO covers the asset's
   min-balance bump plus the flat 1000-microAlgo fee). This is a one-time
   setup step per network (repeat it for mainnet's real `payTo` before the
   mainnet flip), not a per-payment concern.
2. **The payer wallet must opt into the same ASA before it can be paid *from*,
   not just before it can receive** — obvious in hindsight, easy to forget
   when funding a fresh TestNet wallet via a dispenser that only sends ALGO by
   default.

### The package's own `ClientAvmSigner` docstring example is wrong in three places

`x402/mechanisms/avm/__init__.py`'s module docstring — the officially
documented pattern for implementing a client-side signer — does not work
against the installed `x402-avm==2.0.2` runtime behavior. All three bugs are
the same shape: the docstring's example threads `algosdk.encoding.*` helpers
that operate on **base64 strings** through a real interface that operates on
**raw msgpack bytes** at every step. Concretely, against
`unsigned_txns: list[bytes]`:

```python
# WRONG — the docstring's own example, reproduced verbatim:
def sign_transactions(self, unsigned_txns, indexes_to_sign):
    result = []
    for i, txn_bytes in enumerate(unsigned_txns):
        if i in indexes_to_sign:
            txn = algosdk.encoding.msgpack_decode(txn_bytes)   # (1) fails
            signed = txn.sign(self._secret_key)                # (2) fails
            result.append(algosdk.encoding.msgpack_encode(signed))  # (3) wrong type
        else:
            result.append(None)
    return result
```

1. `algosdk.encoding.msgpack_decode(enc)` does `base64.b64decode(enc)`
   internally before unpacking — but `txn_bytes` here is **raw msgpack
   bytes**, not base64. Passing it raises `binascii.Error: Incorrect padding`
   (or, less obviously, `msgpack.exceptions.ExtraData`, depending on what the
   raw bytes happen to decode to as garbage base64).
2. `Transaction.sign(private_key)` also does `base64.b64decode(private_key)`
   internally — it wants the base64 **string** form of the secret key. But
   the docstring's own `__init__` already converts the mnemonic-derived key to
   **raw bytes** (`self._secret_key = base64.b64decode(mnemonic.to_private_key(...))`,
   done so `encode_address(self._secret_key[32:])` can slice it) — so by the
   time `sign()` is called, the raw-bytes form is passed where a base64
   string is required, same failure again.
3. `algosdk.encoding.msgpack_encode(obj)` returns a base64 **string**
   (`base64.b64encode(msgpack.packb(...)).decode()`), but the caller
   (`x402/mechanisms/avm/exact/client.py`) does `base64.b64encode(signed)` on
   whatever `sign_transactions` returns — it wants **raw bytes** back, not
   a string. Returning the string raises
   `TypeError: a bytes-like object is required, not 'str'`.

**Working implementation** — keep the base64-string secret key form for
signing, derive raw bytes only locally for the address, unpack `txn_bytes`
with plain `msgpack.unpackb` (not `msgpack_decode`) before handing it to
`msgpack_decode` (which special-cases dict input and skips its own base64
step), and unwrap `msgpack_encode`'s base64 string back to raw bytes before
returning:

```python
import base64
import msgpack
import algosdk
from algosdk import mnemonic

class WorkingAvmSigner:
    def __init__(self, mnemonic_phrase: str) -> None:
        self._secret_key_b64 = mnemonic.to_private_key(mnemonic_phrase)
        raw = base64.b64decode(self._secret_key_b64)
        self._address = algosdk.encoding.encode_address(raw[32:])

    @property
    def address(self) -> str:
        return self._address

    def sign_transactions(self, unsigned_txns, indexes_to_sign):
        result = []
        for i, txn_bytes in enumerate(unsigned_txns):
            if i in indexes_to_sign:
                txn_dict = msgpack.unpackb(txn_bytes, raw=False)
                txn = algosdk.encoding.msgpack_decode(txn_dict)
                signed = txn.sign(self._secret_key_b64)
                result.append(base64.b64decode(algosdk.encoding.msgpack_encode(signed)))
            else:
                result.append(None)
        return result
```

This only affects **client-side** signer implementations (anything paying
*through* our marketplace, or a test harness proving Phase 0) — it does not
affect our own server-side code, which never implements `ClientAvmSigner` and
was already correct.

## What's still unverified — check live before relying on it

- Exact response shape of `{base}/verify` and `{base}/settle` beyond what
  `facilitator_client.py`'s typed wrappers already assume — trust the
  package's parsing, don't hand-roll a second interpretation.
- Dashboard/leaderboard URL paths (`/dashboard/leaderboards` etc.) — listed
  above from secondary-source scraping, not confirmed against a live
  response.
- Whether the leaderboard counts *only* facilitator-settled payments (assumed
  yes, per the rules' own framing of "processed through the submitted x402
  endpoint") — plain wallet transfers almost certainly do not count.
- EURQ/USDQ/USDT asset ids — not in any official material, needs its own
  research pass if pursued.
