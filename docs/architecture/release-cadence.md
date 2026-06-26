# Release cadence

We ship on a **fixed rolling rhythm**, not continuous deploy-to-mainnet. This drives how we scope bricks and products.

## 4-week cycle

```text
Week 1–2   DEVELOP     Build / extend bricks toward a version cut
           ↓
           FREEZE      Tag a version; feature scope locked for this cycle
Week 3–4   TESTNET     Deploy frozen build to TestNet; try it; fix only blockers
           ↓
           RELEASE     If TestNet is good → release (mainnet or public TestNet product)
           ↓
           (repeat)
```

| Phase | Duration | What happens |
|-------|----------|----------------|
| **Development** | 2 weeks | Implement bricks; merge to main; internal/local dev |
| **Freeze** | End of week 2 | Version bump; scope closed; build artifact for QA |
| **TestNet validation** | 2 weeks | Deploy frozen build; real wallets; Conduit on TestNet; feedback |
| **Release** | After week 4 | Ship if validation passes; otherwise slip fixes into next dev window or hotfix policy (TBD) |

**Effective release window:** ~**4 weeks** per public version (2 dev + 2 TestNet).

## What this implies for bricks

- **Brick definitions** should be completable within a **dev window** or explicitly span cycles (e.g. Search P3).
- **P1 + P2 joint ship** targets a **single freeze** — one web app with News + Suggestions nav.
- **TestNet-first** config (treasury, algod, Conduit, `AUTH_DOMAIN`) is the default for the 2-week validation phase.
- **No TTL / moderation / classifier** until a later cycle unless explicitly pulled into scope.
- **Version tags** and deploy scripts should support redeploying the **same frozen artifact** during TestNet weeks.

## Environments

| Environment | When |
|-------------|------|
| Local / dev | Development weeks |
| TestNet | Validation weeks (frozen build) |
| Production / public | After successful TestNet cycle |

## Config per cycle

Document in release notes each freeze:

- `PLATFORM_TREASURY_ADDRESS` (TestNet)
- Conduit catch-up round / genesis
- Minimum suggestion payment (`SUGGESTION_MIN_MICROALGOS`)
- Flutter `apiBaseUrl` + `AUTH_DOMAIN` for web testers

## Related

- [products-and-bricks.md](products-and-bricks.md) — what ships in a given cycle
- `deploy/` — package + systemd for repeatable TestNet deploys
