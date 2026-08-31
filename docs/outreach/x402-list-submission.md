# x402 List (x402-list.com) — submission draft

Status: **DRAFT ONLY — not sent.** Nothing has been POSTed to x402-list.com
and no form has been opened in a browser. Do not submit until the owner has
supplied a real email address (see the flag below) and, ideally, confirmed
Algorand is an acceptable network with x402-list.com's maintainers first.

## What was verified about their submission mechanics (2026-08-31)

- Agent-first directory indexing x402-payable APIs; machine-readable catalog
  at `x402-list.com/llms-full.txt`, OpenAPI at `x402-list.com/openapi.json`,
  human form at `x402-list.com/submit`.
- Programmatic submission: `POST https://x402-list.com/api/v1/submit`
  (`operationId: submitService`). Body is one of two shapes selected by an
  optional `"type"` field: omit it (or any value other than `"facilitator"`)
  for a **service** submission — this is our case, since PXke is a service
  that *uses* the GoPlausible facilitator, it is not itself a facilitator.
- **Service submission fields** (from the live OpenAPI schema,
  `ServiceSubmissionRequest`), all required unless noted:
  - `url` (uri) — base URL of the x402 service.
  - `email` (email) — submitter contact. **Not supplied here — see flag below.**
  - `service_name` (string)
  - `description` (string)
  - `website_url` (uri) — public website URL.
  - `category` (string) — must be one of the live `/api/v1/categories`
    values, confirmed 2026-08-31: `AI, Blockchain, Compute, Content, Data,
    Finance, Other, Verification`.
  - `endpoints` (string[], max 50) — endpoint paths probed for a valid
    HTTP 402. **Only include paths that answer 402 on a bare, parameter-less
    request** — see the endpoint choice note below.
  - `notes` (string, optional).
- **Cost / hosting rule**: our `url` (`algorand-api.pxke.me`) is our own
  domain, not a free host (vercel.app, workers.dev, etc.) or a dev tunnel, so
  this submission is **free**. Each email may submit once per submission
  type every 7 days; a pending submission auto-rejects after 7 days if not
  reviewed, with an email notification.
- **Networks they currently track** (`GET /api/v1/networks`, confirmed
  2026-08-31): Base, Solana, Polygon, Arbitrum One, Avalanche, Base Sepolia,
  Arc Network Testnet, Solana Devnet — all `eip155:*` or `solana:*` CAIP-2
  ids. **Algorand does not appear.** This is a real gap, not an assumption:
  their tracked-network registry is EVM/Solana-only today. The submission
  form has no network field for a *service* (only the facilitator branch
  asks for `networks[]`), so a service submission itself isn't blocked by
  this — but their review process, uptime probing, or leaderboard display
  may not know what to do with an `algorand:...` CAIP-2 network the first
  time they see one. Hence the inquiry below, meant to go **before or
  alongside** the submission, not skip it.

### Endpoint choice for the `endpoints` field

Their prober appears to hit each path with a bare, parameter-less request
and expect a `402`. Several PXke paid routes require a request body or a
required query parameter and would legitimately 400 (not 402) on a bare
probe per PXke's own "validation happens before the gate" rule — that is
correct PXke behavior, not a bug, but it would read as a broken listing to
an automated prober. Only two PXke routes are guaranteed to answer a bare,
parameter-less `402`:

- `GET /api/v1/x402/ping` — no params, no body, $0.001, exists specifically
  to prove a client's payment flow works.
- `GET /api/v1/x402/features/demand` — `limit` is optional, so a bare `GET`
  still hits the payment gate.

Everything else (`POST /list`, `POST /board`, `GET /news/search?q=...`,
`GET /grades/score?url=...`, `GET /grades/top?tag=...`, etc.) needs a body or
a required query param first and is listed instead in `description`/`notes`
below, with a pointer to the full catalog.

## 1. Inquiry to send first (or alongside the submission)

Send this as a message to x402-list.com (their contact channel, or as the
`notes` field if there's no separate contact path — check for a support
email or Discord/X handle on the site before sending; none was found in the
fetched content, so this likely has to go in via `notes` or a support form
not yet located):

> Hi — we're submitting PXke, a live x402 marketplace running entirely on
> **Algorand mainnet** (CAIP-2 `algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=`),
> settled through the GoPlausible facilitator
> (`https://facilitator.goplausible.xyz/`), paying in USDC, EURQ or USDQ.
> We noticed `GET /api/v1/networks` on x402-list.com currently only lists
> EVM (`eip155:*`) and Solana networks — Algorand doesn't appear. Before we
> submit, can you confirm Algorand/GoPlausible is something your review
> process and directory can list and probe correctly (network display,
> uptime checks, category filters), or whether Algorand support needs to be
> added on your end first? Happy to provide anything you need to verify the
> facilitator or the live 402 response. Thanks!

## 2. Ready-to-send submission (fill in email, then POST)

```json
{
  "url": "https://algorand-api.pxke.me",
  "email": "REPLACE_WITH_OWNER_EMAIL",
  "service_name": "PXke x402 Marketplace",
  "description": "Composite x402 marketplace on Algorand mainnet: pay-per-call news article search/read (PXke Algorand newspaper), an endpoint directory (list/search other x402 services), a visibility board, a feature-request board with paid demand ranking, and endpoint grading. One payTo, settled through the GoPlausible facilitator in USDC, EURQ or USDQ. Full machine-readable catalog with every route and live price at GET /api/v1/x402.",
  "website_url": "https://algorand.pxke.me/x402",
  "category": "Data",
  "endpoints": [
    "/api/v1/x402/ping",
    "/api/v1/x402/features/demand"
  ],
  "notes": "Network: Algorand mainnet, CAIP-2 algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=. Facilitator: GoPlausible, https://facilitator.goplausible.xyz/. payTo: KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII. Assets accepted: USDC (ASA 31566704), EURQ (ASA 2768422954), USDQ (ASA 2768603795), all 6 decimals. This is a Composite-category entry (several endpoints, one payTo, individually discoverable) per the Algorand Global x402 Challenge's own category definitions -- 25 routes total, most free (catalog, search, board feed, features browse, grades index/summary, news headlines) and several paid ($0.001-$0.10). Only /ping and /features/demand answer a bare parameter-less 402 probe; other paid routes need a JSON body or a required query param (documented in the catalog's per-route input_example and at GET /api/v1/x402) so they are not listed in the endpoints array to avoid a false-negative probe. Full docs: https://algorand.pxke.me/x402 and the live catalog at GET /api/v1/x402."
}
```

Equivalent curl (do not run until the email field is filled in and the
inquiry above has had a chance to land):

```bash
curl -s -X POST https://x402-list.com/api/v1/submit \
  -H "Content-Type: application/json" \
  -d @submission.json
```

## What still needs the owner

- **A real email address** in the `email` field. None was invented here per
  instructions — this draft is not submittable until that's filled in.
- A decision on whether to wait for x402-list.com's answer on Algorand
  support before submitting, or submit anyway and let manual review sort it
  out (their own doc says every submission is manually reviewed regardless).
- Confirmation of which `category` reads best — "Data" was chosen because
  most of PXke's read surface (news, directory listings, grades, feature
  demand) is fundamentally data-serving, but "Blockchain" or "Finance" are
  defensible alternate picks given the endpoint-directory/grading products
  are x402-ecosystem infrastructure, not general data.
