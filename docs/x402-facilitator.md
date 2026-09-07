# x402 / GoPlausible facilitator — verified reference

> Looking for the marketplace's own routes, prices and payment flow as an
> agent would use them? See [x402-marketplace-api.md](x402-marketplace-api.md)
> and the live catalog at `GET https://algorand-api.pxke.me/api/v1/x402`.

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
- Public read endpoints, **all confirmed live 2026-08-30** by fetching them
  (no auth needed):
  - `GET {base}/` — endpoint index (lists everything below).
  - `GET {base}/docs/openapi.json` — the facilitator's OpenAPI document.
  - `GET {base}/data/leaderboards?cat=<merchants|resources|payers|assets|networks|countries>&range=ALL&limit=<n ≤ 50>&offset=<n>`
    — the leaderboard data behind the UI, one category per call.
  - `GET {base}/data/merchants/{id}` — one merchant's **analytics** roll-up
    (settle count, volume, resources with real payments). Our id here is
    `3e5946af2c9756b6` (keyed on `payTo`, so every route sharing our `payTo`
    rolls up under it — the Composite category in practice).
  - `GET {base}/data/transactions` — settled transactions.
  - `GET {base}/api/receipt/{txId}` — the receipt for one settlement.
  - Bazaar catalog: `GET {base}/discovery/resources`,
    `GET {base}/discovery/merchants`, `GET {base}/discovery/all`.
  - UI: `{base}/dashboard` and `{base}/dashboard/leaderboards?cat=resources`.

**IMPORTANT — the Bazaar namespace uses a DIFFERENT merchant id than the
analytics namespace** (found live 2026-09-06, cost real confusion before
this was known): `GET /discovery/resources?merchantId=3e5946af2c9756b6`
returns `total: 0` and `GET /discovery/merchants/3e5946af2c9756b6` returns
`Merchant not found` — NOT because we have nothing catalogued, but because
that's the wrong id for this namespace. The Bazaar id is
`S1NBVk9ZVFZOQjdBNk5LQ000VzJXQk9P` (base64url of the first 24 chars of our
`payTo`); resource ids in this namespace are similarly base64url of
`METHOD:url`. Always check Bazaar state with the Bazaar id, never the
analytics id — they look interchangeable (both 16-ish char opaque strings)
but are not.

### How a route gets into the Bazaar (verified live 2026-08-30, corrected 2026-09-06)

The facilitator catalogs a resource in the Bazaar **from the 402 offer's
`resource.url`, after a `verify` call on it** — NOT necessarily a full
settlement. `extensions/bazaar/facilitator.py` reads the bazaar extension
and the `description`/`mimeType` off the **payment payload the paying
client echoes back**, not from anything the resource server sends directly
— a real merchant (`proptech.watch`) shows `settleCount: 0, resourceCount: 4`
in the Bazaar, confirmed live. Consequence: **a non-conforming x402 client
that doesn't echo the bazaar extension means the route is never catalogued,
no matter how correctly we declare it** — there is no server-initiated
registration call anywhere in the facilitator SDK.

Other consequences, observed on our own merchant entry:

- A settlement whose offer carried a non-URL `resource` (e.g. the bare ledger
  id `x402-directory-list`) **settles fine and counts on the leaderboard**
  (`challenge: true`, because the tag rides on the asset's `extra`) but is
  **never catalogued** (`bazaar: false`) — there is no URL to catalog.
- Only an offer whose `resource.url` is an absolute public URL gets a Bazaar
  entry, and it gets one **per distinct URL**, so a templated route must
  advertise its template, not one URL per path-parameter value.
- A discovery extension on a body-taking route (POST/PUT/PATCH) MUST declare
  `body_type="json"`, even when the route takes no body at all (a
  path-parameter-only action like a vote or a follow) — the query-params
  declaration's schema only admits `method` in `["GET","HEAD","DELETE"]`,
  and the resource server injects the real method at request time, so a
  body-method route declared without `body_type` fails the facilitator's
  own `validate_discovery_extension` and is **silently never catalogued,
  even after a real settlement** (found live 2026-09-05 on the
  feature-vote route; see `modules/x402/discovery.py:describe_json_endpoint`).

`backend/app/modules/x402/guard.py` therefore advertises
`settings.x402_public_api_base` + the request path as the offer's
`resource.url` (`_resource_url`), while the short id passed as `resource=`
stays what the settlement ledger records. Routes with a path parameter pass
`resource_path=` to override the advertised path with the template (the
feature-vote route advertises `/api/v1/x402/features/{request_id}/vote`).
Our merchant reads `bazaar: true` with 7 challenge-tagged, catalogued
routes as of 2026-09-06 (out of 27 paid routes) — the other 20 are blocked
by a separate bug, also found and fixed 2026-09-05/06: most of our paid
routes validated their request body BEFORE running the payment gate, so an
unpaid probe (which is how any x402 client, and the facilitator's own
`verify` call, first learns the price) got a plain validation error instead
of ever seeing the 402 challenge. Fixed via a shared
`app/modules/x402/paid_request.py:challenge_if_unpaid()` primitive applied
across every paid module. See `test_x402_bazaar_extension_sweep.py` for the
regression coverage (drives every real route registrar unpaid against a
stub facilitator and validates the emitted extension survives the
facilitator's own parser).

### Merchant profile enrichment (name/description/website/logo/banner)

Confirmed live 2026-09-06: our merchant record's `name`/`description`/
`website`/`logo`/`categories` are all `null` in the Bazaar (`data/merchants`
analytics view shows the same). This makes us unfindable by
`discovery/merchants`'s own `search`/`category` filters. Two independent
enrichment paths exist, neither is populated for us yet:

1. **OpenGraph tags on the merchant's own domain root.** The facilitator's
   metadata enrichment engine fetches each merchant domain roughly once a
   day and reads `og:title`/`og:description`/`og:image` (Algorand's own
   "Enabling x402 payments on Algorand" guide confirms this explicitly).
   Our domain root (`https://algorand-api.pxke.me/`) served a bare nginx
   404 with no HTML until a fix landed 2026-09-06 (undeployed as of this
   writing) — see `deploy/nginx/algorand-platform.conf`'s `location = /`
   and `shared/merchant-landing/index.html`.
2. **`GET {base}/sponsorship/merchant-info?address={payTo}`** — this is
   where a merchant's `title`/`description`/`url`/`logo`/`banner` actually
   live (confirmed: querying our own `payTo` here returns the same 5 null
   fields). This payload is populated as part of **purchasing an SU
   (Settlement Unit) sponsorship card** (`POST /sponsorship/purchase/{tier}`,
   $10 minimum tier) — a real, one-time purchase of facilitator gas credit,
   paid to GoPlausible's own treasury, NOT to any of our own endpoints, so
   it carries no wash-volume exposure and doesn't touch our USDC Volume
   score. It also adds a sponsor badge and a chain-scoped Sponsors
   leaderboard placement. This is the only mechanism found (after an
   exhaustive read of the installed `x402` package and the facilitator's
   full OpenAPI doc) that lets a merchant profile stop being anonymous —
   there is no free/self-serve way to set these fields. Spending real money
   on this is an owner decision, not something to do unattended.

The facilitator also exposes an MCP interface (`GET /mcp`, `POST
/mcp/call`, 9 tools) mirroring the same plain-HTTP discovery data with
fewer filters — not a richer or alternate registration path, just another
transport for the same read-only catalog.

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

Confirmed from `x402/extensions/bazaar/resource_service.py` (in active use
by every paid module: `modules/x402_directory/`, `x402_board/`,
`x402_features/`, `x402_grading/`, `x402_news/`, `modules/kya/api/routes.py`).

**Do not call `declare_discovery_extension(..., output={"example": ...})`
with a bare dict** — that is what the submission guide shows, and it 500s the
route before it ever emits a 402: the installed `x402-avm==2.0.2` reads
`output.example` as an attribute, so `output` must be an `OutputConfig`. Both
KYA paid routes shipped with that bug (fixed 2026-08-30). The repo's wrapper
`modules/x402/discovery.py:describe_json_endpoint` makes the mistake
structurally impossible, so always go through it:

```python
from app.modules.x402.discovery import describe_json_endpoint

describe_json_endpoint(
    body_type="json",        # POST/PUT/PATCH whose input is a JSON body; omit for
                             # GET/HEAD/DELETE (query-params extension, the default)
    input={...},             # example input
    input_schema={...},      # JSON Schema for the input
    output_example={...},    # wrapped into OutputConfig(example=...) for you
)
```

which is, underneath:

```python
from x402.extensions.bazaar import declare_discovery_extension
from x402.extensions.bazaar.resource_service import OutputConfig

declare_discovery_extension(
    input=..., input_schema=..., body_type=...,
    output=OutputConfig(example={...}),
)
```

`body_type="json"` matters: without it the package builds a query-params
extension, which describes a body-taking POST incorrectly. HTTP method is
inferred from the route, not passed explicitly. Pass the result as
`extensions=` to `require_payment(...)` / `require_paid_request(...)`, which
sets `RouteConfig.extensions` — see `modules/x402/guard.py`.

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
- ~~Dashboard/leaderboard URL paths~~ — **verified live 2026-08-30**, see the
  endpoint list in the GoPlausible section above. The full response schemas
  of `/data/*` and `/discovery/*` beyond the fields we read (`challenge`,
  `bazaar`, merchant id, settlement counts) are not documented here — read
  `{base}/docs/openapi.json` rather than trusting a paraphrase.
- Whether the leaderboard counts *only* facilitator-settled payments (assumed
  yes, per the rules' own framing of "processed through the submitted x402
  endpoint") — plain wallet transfers almost certainly do not count. What
  *is* confirmed: a facilitator-settled payment on an offer carrying the tag
  shows as `challenge: true` on our merchant entry whether or not the route
  was Bazaar-catalogued.
- EURQ/USDQ/USDT asset ids — not in any official material, needs its own
  research pass if pursued.
