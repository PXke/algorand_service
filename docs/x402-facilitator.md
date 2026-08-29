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
