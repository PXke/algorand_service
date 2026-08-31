# gold-402 — PR-ready entry draft

Status: **DRAFT ONLY — not sent.** No fork, branch, commit, or PR has been
created against `github.com/Haustorium12/gold-402`. Nothing here has touched
that repository. This file is the exact patch content plus the exact
commands someone with a GitHub account should run.

## What was verified about their contribution mechanics (2026-08-31)

- Curated by 24K Labs, hand-checked directory of x402 resources, contributed
  via PR against `github.com/Haustorium12/gold-402`, entries live as
  markdown in `directory/*.md`, 13 shelves.
- **Acceptance bar for a service** (from `CONTRIBUTING.md`): the URL
  resolves and answers a valid HTTP 402 (or a `/.well-known/x402` manifest
  pointing at one); it's genuinely x402 (HTTP 402 + `X-Payment`), not just
  "accepts USDC"; not a duplicate; one factual-line description, no
  marketing language; a working example request if the endpoint takes
  parameters. **Every submitted endpoint is actually probed before merge** —
  a failing one gets a friendly fix-it note, not a silent rejection.
- **Entry format**: `` [Name](url) — One factual sentence, starting
  uppercase, ending with a period. `` — em-dash separator, no badges. In
  practice (checked against a dozen live entries in `directory/apis.md`,
  e.g. WebberSites, M0 URL Extraction, Israel Company Verify), multi-product
  services routinely chain several semicolon-joined clauses under that "one
  sentence," add an `` Example: `METHOD /path {...}` `` line when the
  endpoint needs a body/params, and append parenthetical links
  (`([Docs](...))`, `([OpenAPI](...))`) — matched below.
- **Where it goes**: PXke is a pay-per-call API surface an agent calls
  directly (news search/read, directory queries, board, grading), the same
  shape as `directory/apis.md`'s existing marketplace-style entries (PayAPI
  Market, WebberSites x402 Data API) rather than `directory/ecosystem.md`
  (which is for wallets/frameworks/protocol infra, not callable paid
  endpoints). Placed in the **Business Intelligence** subsection of
  `directory/apis.md`, right after the existing **PayAPI Market** entry —
  closest existing neighbor (another "marketplace of x402 APIs" entry).
- **The example endpoint was live-verified**, not invented: `POST
  https://algorand-api.pxke.me/api/v1/x402/list` with the exact JSON body
  below returned a real `402` with a valid `PAYMENT-REQUIRED` offer header
  on 2026-08-31 (checked with curl before writing this file — this is what
  gold-402's own submission gate will see when it probes with the `Example:`
  body instead of `{}`).
- **Contact line is optional** — their default is to open an issue on the
  repo the entry links to if something breaks; PXke's entry links to a live
  endpoint, not a repo, so gold-402 has no repo to open an issue against.
  Consider adding a `Contact:` line to the PR description (see the ready
  commands below) so a broken listing reaches someone — this needs the
  owner's chosen contact (email, X/Telegram handle, etc.), not invented here.

## The entry (exact patch content)

Add this line to the bottom of the **Business Intelligence** subsection in
`directory/apis.md`, immediately after the `PayAPI Market` line and before
the `---` that follows it:

```markdown
- [PXke x402 Marketplace](https://algorand-api.pxke.me/api/v1/x402/list) — Composite x402 marketplace on Algorand mainnet, settled through the GoPlausible facilitator in USDC, EURQ or USDQ under one payTo: pay-per-call search/read from the PXke Algorand newspaper ($0.01-$0.02), an endpoint directory to list or search other x402 services ($0.10/30 days), a visibility board ($0.05/14 days), a feature-request board with paid demand ranking ($0.02-$0.05), and endpoint grading ($0.02-$0.03); free catalog with every route and live price at `GET /api/v1/x402`. Example: `POST /api/v1/x402/list {"url":"https://api.example.com/v1/quote","price":"$0.01","description":"Live FX quote, one currency pair per call.","assets":["USDC"],"tags":["fx","market-data"],"category":"finance"}`. ([Docs](https://algorand.pxke.me/x402))
```

Notes on the content itself:
- Prices are pulled straight from the live catalog (`GET
  https://algorand-api.pxke.me/api/v1/x402`, fetched 2026-08-31), not
  memorized — re-check before merging if time has passed.
- The `Example:` body is copy-pasted from the marketplace's own
  `input_example` for `POST /list`, and is the exact body used to confirm
  the live `402` above.
- No `/.well-known/x402` manifest or `openapi.json`/`llms.txt` exists at
  `algorand-api.pxke.me` (all confirmed `404` on 2026-08-31) — do not add
  those links, they'd be dead. `https://algorand.pxke.me/x402` (the public
  docs/landing page, confirmed `200`) is the one link included beyond the
  entry's own URL.

## Commands to run (not executed — for whoever has a GitHub account to do this)

```bash
# 1. Fork the repo (requires a real GitHub account/session)
gh repo fork Haustorium12/gold-402 --clone=true --remote=true

cd gold-402

# 2. Branch
git checkout -b add-pxke-x402-marketplace

# 3. Add the entry -- open directory/apis.md, find the "Business
#    Intelligence" section, insert the line above right after the
#    "PayAPI Market" entry. (Or use the sed one-liner below if the
#    PayAPI Market line text hasn't changed upstream.)
sed -i '/\[PayAPI Market\]/a - [PXke x402 Marketplace](https://algorand-api.pxke.me/api/v1/x402/list) — Composite x402 marketplace on Algorand mainnet, settled through the GoPlausible facilitator in USDC, EURQ or USDQ under one payTo: pay-per-call search/read from the PXke Algorand newspaper ($0.01-$0.02), an endpoint directory to list or search other x402 services ($0.10/30 days), a visibility board ($0.05/14 days), a feature-request board with paid demand ranking ($0.02-$0.05), and endpoint grading ($0.02-$0.03); free catalog with every route and live price at `GET /api/v1/x402`. Example: `POST /api/v1/x402/list {"url":"https://api.example.com/v1/quote","price":"$0.01","description":"Live FX quote, one currency pair per call.","assets":["USDC"],"tags":["fx","market-data"],"category":"finance"}`. ([Docs](https://algorand.pxke.me/x402))' directory/apis.md

# 4. Verify the diff looks right (should be exactly one added line)
git diff directory/apis.md

# 5. Commit
git add directory/apis.md
git commit -m "Add PXke x402 Marketplace"

# 6. Push and open the PR, titled exactly "Add PXke x402 Marketplace"
git push -u origin add-pxke-x402-marketplace

gh pr create \
  --repo Haustorium12/gold-402 \
  --title "Add PXke x402 Marketplace" \
  --body "$(cat <<'EOF'
Adds PXke, a composite x402 marketplace on Algorand mainnet (news search/read,
endpoint directory, visibility board, feature-request board, endpoint
grading), settled through the GoPlausible facilitator in USDC/EURQ/USDQ under
one payTo. Example request in the entry probes a real 402 (verified live
before opening this PR).

Contact: REPLACE_WITH_OWNER_CONTACT_OR_OMIT_THIS_LINE
EOF
)"
```

## What still needs the owner

- **A GitHub account** to actually fork/branch/push/open the PR — none of
  the commands above have been run.
- A decision on the `Contact:` line in the PR body: leave it out entirely
  (gold-402's default then falls back to opening an issue against the
  entry's linked URL, which isn't a repo — so in practice a broken listing
  would have no clean way to reach anyone) or supply a real contact. Not
  invented here.
- Confirmation that `directory/apis.md`'s "Business Intelligence" section
  and the "PayAPI Market" neighbor are still the right anchor by the time
  this is actually opened — their directory is edited continuously (weekly
  "New This Week" additions), so re-check the file hasn't reorganized before
  running the `sed` command above; the entry text itself doesn't depend on
  that anchor being exact, the placement does.
