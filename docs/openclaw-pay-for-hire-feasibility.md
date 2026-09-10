# OpenClaw pay-for-hire feasibility research

> **Note (2026-09-10):** the directory and KYA products mentioned in passing below were removed on 2026-09-10 — see [ADR-0006](adr/ADR-0006-x402-consolidation.md). The feasibility findings do not depend on them.

Status: research only, no code written. Untracked file — not committed.
Date: 2026-09-04.

## 0. What was asked

Owner idea (verbatim, condensed): sell hosted, preconfigured OpenClaw
instances at ~2 USDC/month, one payment for the month, free config endpoint
afterward, many small containers waking every ~30 minutes to "think for a
minute" and go back to sleep. Questions to answer: real resource cost, is
$2/month viable, does OpenClaw's license even allow reselling hosted access,
should we build our own agent core instead, and what would the config surface
look like.

This is **not yet on the roadmap** in `CLAUDE.md` §9.1 (items 1-25). It is a
distinct idea from KYA/KYB, the directory, or any listed product. Nothing
here is scoped or owner-approved to build — this document is the design-pass
input that section flags as required before code starts on anything with new
infra spend or financial exposure (the same flag applied to roadmap items
12/15/16/17/18b).

## 1. Method: empirical, not guessed

Two real hosts were inspected read-only over SSH, no configuration changed:

- `92.222.76.121` (`root@`, OpenClaw itself runs as `ubuntu`) — the existing
  OpenClaw marketing/seller/tester agent documented in `docs/ops-hosts.md`.
  This is a **single always-on instance with the "full" tool profile**, not a
  minimal checkin bot, and it is not part of this repo's deploy path. It is
  the closest real analogue we have to "one running OpenClaw instance," and
  every number below that came from it is a genuine measurement, not a
  simulation.
- `5.135.131.229` — the platform host that runs the newspaper + x402
  marketplace + workers, per `docs/ops-hosts.md`. Used only to sanity-check
  spare capacity, per the task's item 7.

## 2. Real per-instance cost breakdown (empirical)

### 2.1 Process footprint (measured via `systemctl --user status
openclaw-gateway`, `ps aux`, `du -sh`)

| Metric | Value | Source |
|---|---|---|
| RSS memory, steady state | **598-603 MB** (peak 1 GB since last restart) | `systemctl --user status`, live |
| CPU time | 2m30s over 1h20m wall = **~3.1% of one core**, sustained | same, delta-sampled twice ~6 min apart |
| Tasks (threads/processes) | 12 | same |
| npm install (`~/.npm-global/.../openclaw`) | 897 MB | `du -sh` |
| `~/.openclaw` home dir total | 1.7 GB | `du -sh` |
| — of which: local plugin npm packages (`~/.openclaw/npm`) | 1.5 GB | breakdown, shared across all agents on one gateway |
| — of which: per-agent session state (`agents/`) | 107 MB | breakdown |
| — of which: browser profile cache (`browser/`) | 66 MB | breakdown, **not needed** for a text-only checkin bot |
| — of which: model/tool cache (`cache/`) | 28 MB | breakdown |
| — of which: gateway/session runtime state (`state/`) | 15 MB | breakdown |

Host itself: 3.7 GB RAM total, 2 vCPU, 38 GB disk free of 38 GB total — a
small VPS-class box, already running near its comfortable ceiling with a
single full-profile instance (688 MB free / 1.3 GB "available" at the time
of inspection).

**Key finding**: almost all of the 1.7 GB `~/.openclaw` footprint (1.5 GB of
1.7 GB) is the **local plugin npm packages**, which are shared per Node
process, not per tenant. OpenClaw natively supports multiple **isolated
agents on one gateway process** (`openclaw agents add/list/bind/delete` —
"Manage isolated agents (workspaces + auth + routing)"), each with its own
workspace/session sqlite, sharing one Node runtime and one copy of the
plugin code. Per-tenant marginal disk cost, at the per-agent breakdown
above, is closer to **~100-150 MB** (session db + light cache), not the
full 1.7 GB, if tenants share a gateway process rather than each running
their own container with a full separate `~/.openclaw` + `node_modules`
install.

The live instance also runs **45 of 64 available plugins enabled** by
default under `tools.profile: "full"`, including things a paid checkin bot
would never need: browser, canvas, cua-computer (screen/computer control),
talk-voice, and roughly two dozen LLM-provider plugins (`anthropic`,
`openai`, `google`, `nvidia`, `together`, `vllm`, `sglang`, …) that only
matter if that specific provider is in use. `tools.profile` has a documented
`"minimal"` option (confirmed in the config JSON-schema, alongside `"coding"`,
`"messaging"`, `"full"`) — a hosted checkin-bot product should ship on
`minimal`, cutting both memory and attack surface substantially below the
598 MB measured here.

### 2.2 Idle vs. active resource shape (from config + logs, not guessed)

Read `~/.openclaw/openclaw.json` and `/tmp/openclaw/openclaw-*.log` on the
live box:

- **No LLM connection is held open.** The gateway is a long-running Node
  process that answers HTTP/webhook/channel events; it only calls out to a
  model provider when a turn actually runs (heartbeat tick, inbound message,
  cron/automation).
- **It does poll**, but the polling itself is cheap and unrelated to LLM
  spend: the Telegram channel plugin does an "isolated polling worker
  poll-start" every ~30 seconds (long-poll against Telegram's API, not the
  LLM), and a `health-monitor` runs every 300s. This explains the small
  constant CPU baseline (~3%) independent of actual "thinking."
- **The agent-level wake cycle is a heartbeat**, config key
  `agents.defaults.heartbeat.every`, default in the live config is **`2h`**,
  not 30 minutes — 30-minute cadence is achievable (the schema field is a
  free-form duration string with no floor found in the schema itself) but is
  4x more frequent than what this instance actually runs, and each cycle
  costs money, so cadence is a direct cost lever, not just a UX choice.
  Confirmed empirically: `grep -c "heartbeat poll"` found ~10 heartbeat
  events on 2026-09-03, consistent with a ~2h cadence.

### 2.3 Model + real token cost (measured via `openclaw sessions list --json`)

The live instance defaults to `mistral/mistral-small-latest` (with
`mistral-large` and `deepseek/deepseek-v4-flash` as fallbacks) — **not**
DeepSeek by default, even though DeepSeek is this project's own live
provider per `CLAUDE.md` §0. For unit economics below we price against
**DeepSeek V4 Flash**, since that is the model this project already pays for
in `workers/`, its pricing is in the live config, and it is the obvious
default for a cost-sensitive $2/month product:

```
deepseek-v4-flash: input $0.14/M tokens, output $0.28/M, cache-read $0.028/M
```

Real per-turn token counts, pulled from the live SQLite session store
(`openclaw sessions list --json`), not estimated:

| Session | Age | Last-turn input tokens | Last-turn output tokens | Accumulated context |
|---|---|---|---|---|
| `telegram:direct` (recent, active) | 17 min | 2,272 | 406 | 72,096 / 200,000 (36%) |
| `agent:main:main` (long-lived, mostly heartbeat/NO_REPLY turns) | started 2026-08-31, last touched 2026-09-03 (~3.5 days) | **109,019** | 100 | 109,019 / 200,000 (55%) |

The second row is the critical finding: a session left running through
repeated 2-hour heartbeats accumulated to **55% of a 200K context window in
3.5 days**, for a turn whose actual output was a 100-token `NO_REPLY`. Chat
APIs bill on the full input context sent per call (modulo caching), so **the
dominant cost driver for a "wake, think, sleep" product is context growth
between compactions, not the "thinking" itself.**

Cost per cycle, no caching assumed (conservative/worst-case for the
uncached fraction):

- **Typical/warm turn** (2,272 in / 406 out): (2272×0.14 + 406×0.28)/1e6 ≈
  **$0.00043/cycle**
- **Un-pruned/stale turn** (109,019 in / 100 out): (109019×0.14 +
  100×0.28)/1e6 ≈ **$0.0153/cycle**

At the owner's proposed 30-minute cadence (48 cycles/day, ~1,440/month):

- If every cycle stayed at the "typical/warm" size: 1,440 × $0.00043 ≈
  **$0.62/month** — comfortably under $2.
- If context is allowed to grow unchecked toward the stale-session shape
  observed live (context climbing from ~3K to 40K+ tokens across a day before
  any compaction fires, which is what happened on the real box over 3.5
  days): a rough average of ~20K input tokens/cycle gives (20000×0.14 +
  400×0.28)/1e6 ≈ $0.0029/cycle × 1,440 ≈ **$4.18/month** — over double the
  $2 price, on LLM tokens alone.
- Worst case, every cycle costing what the observed 109K-token turn cost:
  1,440 × $0.0153 ≈ **$22/month** — this is not a realistic sustained
  average (a whole month can't spend every cycle at 55%-of-window without a
  window overflow), but it shows the ceiling risk is real and large if
  compaction is left to its defaults.

`deepseek-v4-flash`'s cache-read price is 5x cheaper than fresh input
($0.028/M vs $0.14/M). If the API-level prompt cache is actually being hit
for the stable prefix (system prompt + tool schema + earlier turns), true
cost lands well below the "no caching" numbers above — but OpenClaw's
session-list output does not break out cached vs. uncached tokens, so this
project cannot confirm the live instance is benefiting from caching without
deeper log capture than was in scope here. **Flag this as unverified**, not
assumed favorably.

### 2.4 Bottom-line unit cost model

Per tenant per month, assuming a **trimmed, `tools.profile: minimal`
instance sharing a gateway process** (not a from-scratch container per
tenant) and **disciplined context management** (forced/aggressive compaction
every cycle, capped injected context — see §4):

| Component | Estimate | Basis |
|---|---|---|
| LLM tokens (DeepSeek V4 Flash, 1,440 cycles/mo, managed context) | $0.60 - $4 | measured per-cycle costs above, range reflects compaction discipline |
| Marginal compute/RAM (share of a host, minimal profile) | ~$1 - $2 | estimated from measured 598 MB full-profile RSS scaled down; **not directly measured**, no minimal-profile instance exists yet to sample |
| Marginal disk (session db + cache, shared plugin install) | negligible (~100-150 MB) | measured breakdown, §2.1 |
| OpenClaw licensing | $0 | MIT, confirmed §3 |
| Facilitator/settlement fees on the $2 payment | unknown, not researched here — see `docs/x402-facilitator.md` for the general fee shape | out of scope for this note |

## 3. $2/month unit economics verdict

**Break-even at best, likely a loss without deliberate engineering, on LLM
cost alone — before infra and payment-processing overhead are even added.**

- The **optimistic** path ($0.62/mo LLM + ~$1-2/mo infra share ≈ **$1.6-2.6/
  mo total cost**) is roughly break-even against $2/month, assuming disciplined
  context pruning that does not exist by default in the software as
  configured on the live instance.
- The **realistic** path, extrapolating from what actually happened on the
  live box without intervention (context growing toward tens of thousands of
  tokens per cycle before compaction), lands around **$5-6/mo total cost**
  against a $2/mo price — a clear loss, 2.5-3x underwater.
- This is **before** any facilitator fee on the $2 USDC settlement, before
  support/ops overhead, and before accounting for the fact that a real paying
  customer will actually use configured plugins/tools sometimes (this
  analysis is heartbeat-only "thinking," not real work — task-triggered runs
  would add more tokens on top).

**Verdict: not viable at $2/month as specified, unless (a) cadence is
lengthened from 30 min toward something closer to the 2h default already
observed live, (b) context is hard-capped/compacted aggressively every
cycle rather than left to OpenClaw's default behavior, and (c) tenants share
one gateway process instead of one container each.** All three are real
engineering asks, not free. A price closer to $3-5/month, or a 1-2h wake
interval instead of 30 minutes, would make the same architecture comfortably
profitable using the measured numbers above. This is a pricing/cadence
problem more than a "is OpenClaw itself too expensive" problem.

## 4. OpenClaw license / hosting-terms finding

**MIT license, confirmed by reading the actual file on the live install**
(`~/.npm-global/lib/node_modules/openclaw/LICENSE` and `package.json`):

```
"license": "MIT"
"author": "OpenClaw Foundation (https://openclaw.org)"
```

MIT text is the standard permissive grant: "Permission is hereby granted,
free of charge, ... to use, copy, modify, merge, **publish, distribute,
sublicense, and/or sell copies of the Software** ... subject to including the
copyright notice." **No AGPL-style "if you offer this as a network service
you must release your modifications" clause. No SaaS/hosting carve-out, no
Commons Clause, no field-of-use restriction found.** `THIRD_PARTY_NOTICES.md`
(the file that would disclose any vendored copyleft code) lists exactly one
entry — `@earendil-works/pi-tui`/pi-mono — also MIT, same permissive terms,
no additional obligations.

**Conclusion: reselling hosted OpenClaw instances as a paid service is not
blocked by OpenClaw's own license.** This is the one part of the task that
came back unambiguously green. (Not researched: openclaw.org's own website
Terms of Service, if any exist separately from the code license — worth a
quick check before committing to this publicly, but the code itself imposes
no obstacle.)

## 5. Build-vs-use-OpenClaw recommendation

**Use OpenClaw, don't build a custom agent core — with the caveat that "use
OpenClaw" means running it in `minimal` tool-profile behind aggressive
context controls, not the `full` profile observed live.**

What OpenClaw brings that a custom loop would cost real time to rebuild:

- A working **multi-tenant "agents" primitive already exists**
  (`openclaw agents add/list/bind/delete`) — isolated workspace, auth, and
  routing per agent on one gateway process. This is exactly the "many
  instances" shape the owner described, and it's already built, tested (by
  virtue of being shipped software), and documented.
- A **plugin ecosystem including the x402/Algorand payment path already in
  production use on the live box** (`@goplausible/openclaw-algorand-plugin`,
  per `docs/ops-hosts.md`) — this project would otherwise have to write and
  maintain its own x402-fetch tool for agent-initiated payments, duplicating
  work `workers/`'s and `backend/`'s x402 client already do differently.
- A real **config schema with `minimal`/`coding`/`messaging`/`full` tool
  profiles, per-agent model policy allowlists, context-limit knobs
  (`contextLimits.postCompactionMaxChars`, `session.maintenance.maxDiskBytes`),
  and heartbeat cadence/timeout controls** — these are precisely the levers
  needed to fix the §3 cost problem, and they already exist; a custom loop
  would need to invent equivalents.
- MIT license (§4) removes the one plausible reason to avoid it.

What a custom loop would gain, honestly stated:

- Full control over exactly what gets sent per turn (no fighting a general
  chat-framework's default compaction behavior) — but §2.3's finding is a
  **configuration problem** (defaults not tuned for a cost-capped product),
  not a structural OpenClaw limitation; the knobs to fix it exist.
- No dependency on a fast-moving external project's plugin ecosystem
  (45/64 plugins enabled by default is a lot of surface for a paid multi-
  tenant product to inherit, including several remote-control-flavored
  plugins like `cua-computer` and `linux-node` desktop/camera access that a
  hosted checkin-bot has no business exposing to a paying stranger's
  workspace).
- Smaller baseline memory (598 MB full-profile vs. a hand-rolled loop that
  could plausibly run in tens of MB) — but §2.1 already shows most of that
  is shared plugin code, not per-tenant, and `minimal` profile should shrink
  it further; this needs an actual minimal-profile measurement before
  claiming a specific number (see §7 assumptions).

**Recommendation: use OpenClaw's multi-agent primitive, `tools.profile:
minimal`, and its existing context-limit/heartbeat config surface. Do not
build a custom agent core.** The task the owner is actually worried about
("does hosting this bankrupt us") is a tuning and pricing problem, not a
build-vs-buy problem — the software already has the tuning knobs; they are
just not the defaults on the one live instance inspected. Building a custom
core would spend real engineering time re-deriving controls that already
ship, for a security/cost posture that's achievable by turning knobs OpenClaw
already exposes.

## 6. Platform capacity reality check (item 7)

`5.135.131.229` (measured live, read-only):

| Resource | Total | Used | Available/free | Note |
|---|---|---|---|---|
| RAM | 31 GB | 19 GB | 11 GB "available" | Already running Cassandra (9.4 GB RSS), algod (5.2 GB RSS), plus newspaper/x402 backend+celery+translate workers, plus **unrelated side services**: searxng, clamd (ClamAV daemon), mariadb, a `valar_daemon`, and an `oak/backend` app — this box is not dedicated to the newspaper/x402 stack alone. |
| CPU | 12 cores, Xeon E5-1650 v2 @ 3.5GHz (2013-era part) | load avg 1.00/0.97/1.01 | plenty of idle core-count headroom, but per-core throughput is old | LLM inference itself runs on the provider side (not local CPU), so per-tenant CPU draw should look like the ~3% measured on the OpenClaw box, not compute-bound |
| Disk | 145 GB | 100 GB (73%) | 38 GB | Getting tight; new tenant workspaces/session dbs would eat into this steadily |

**This host already carries more unrelated load than `docs/ops-hosts.md`'s
"shared nginx host, modest budget" framing suggests** — four processes
(searxng, clamd, mariadb, valar_daemon) with no connection to this repo are
running on it. Packing dozens of tenant OpenClaw containers onto the same
box that runs production newspaper + x402 marketplace is a real availability
risk (a noisy/misbehaving tenant agent competing for the same RAM/disk as
Cassandra), independent of the cost math in §3.

**Rough density estimate** (not measured — no minimal-profile tenant
instance exists to sample): if a `minimal`-profile tenant instance lands
around 150-250 MB RSS (scaling down from the measured 598 MB full-profile
figure, since `minimal` drops ~20 of the 45 enabled plugins including the
heaviest ones — browser/canvas/cua-computer), the 11 GB "available" RAM on
this host could nominally fit **35-70 such instances by memory alone**. But
given the disk headroom (38 GB free, already at 73%) and the desire not to
compete with prod Cassandra/algod, a realistic **practical ceiling on the
*existing* platform host is closer to 15-25 tenants** before it should be
treated as full and traffic moved to a second, dedicated host — and a
dedicated host is the safer recommendation regardless of the exact number,
given this box already carries unrelated production load per §1.

**Recommendation: do not co-locate this product's tenant containers with the
newspaper/x402 platform host.** Use a separate, purpose-bought VPS (the
92.222.76.121-class spec — 2 vCPU/4GB RAM handled one full-profile instance
with headroom to spare) sized to the pilot tenant count, and scale
horizontally with more such hosts as tenant count grows, rather than
absorbing tenants into the shared prod box's spare capacity.

## 7. Feature/config list proposal (item 8)

Grounded in the actual config schema pulled from the live box
(`openclaw config schema`), not invented. A paying customer's free config
endpoint could plausibly expose:

**Model / cost tier** (`agents.defaults.model.primary` /
`agents.defaults.modelPolicy.allow`):
- Budget tier: `deepseek/deepseek-v4-flash` (cheapest, what this project
  already runs in `workers/`)
- Mid tier: `deepseek/deepseek-v4-pro` or `mistral/mistral-large-latest`
  (12x and similarly higher cost per the live config's own `cost` blocks —
  price this tier accordingly, not at the same $2 flat rate)
- Fallback chain (`agents.defaults.model.fallbacks`) — whether to allow
  automatic fallback to a second model on primary failure

**Wake cadence** (`agents.defaults.heartbeat.every`, `.activeHours`):
- Interval (30m / 1h / 2h / 6h / off) — directly trades cost for
  responsiveness per §3; should probably not be sold below ~1h at $2/mo
  given the measured economics, or should carry a price scaled to cadence
- Active-hours window + timezone (heartbeat schema has `activeHours.start/
  end/timezone` already) — e.g., "only think during business hours"

**Tool/plugin profile** (`tools.profile`, `plugins.entries.*.enabled`):
- Base profile: `minimal` (default, cheapest/safest) / `messaging` / `coding`
  — `full` should not be offered at $2/mo given §2's footprint and the
  desktop-control-flavored plugins (`cua-computer`, `linux-node`) it drags in
- Individual plugin toggles for anything genuinely useful to a hosted
  checkin agent: `duckduckgo`/`parallel` web search, `telegram` (if the
  tenant wants their bot reachable via their own Telegram), `file-transfer`,
  `webhooks`
- Explicitly **not** offered at this price point: browser/canvas/computer-use
  plugins (heavy, and the wrong shape for an unattended checkin bot), any
  plugin that reaches a node the tenant doesn't control

**Persona / system prompt**:
- Workspace-scoped instructions (the config already reads
  `agents.defaults.workspace` for filesystem/AGENTS.md-style context) —
  expose a small free-text "what should this agent care about / check on"
  field, capped in size (the schema's own
  `contextLimits.postCompactionMaxChars`, max 50,000, is the platform's own
  sanity bound for this kind of injected text — a paid tenant config should
  cap well under that, both for cost and to bound blast radius)
- Notification/output channel: where heartbeat output goes (Telegram DM,
  webhook, or silent-unless-alert via `channels.defaults.heartbeatVisibility`,
  which already distinguishes "show OK" vs. "show alerts only")

**Cost/context guardrails** (recommend **mandatory**, not optional, given
§2.3's finding):
- Forced compaction cadence / max context size per cycle — this is the knob
  that determines whether the product is profitable or not; should not be
  left at OpenClaw's defaults, and should not be exposed as a customer
  choice that could blow past cost, given customers have no visibility into
  what this actually costs the provider (i.e., this project)
- A hard monthly token/cycle budget per tenant with graceful degradation
  (skip cycles, not silent failure) if a tenant's context genuinely can't
  compact down — protects against the $22/mo worst case in §2.3 becoming a
  real bill

## 8. Overall feasibility recommendation

**Go, with changes — not "go as specified."**

The specific plan as described (many Docker containers, 30-minute wake,
$2/month flat, whatever OpenClaw ships with by default) does not clear
break-even on the measured numbers in §3; realistic behavior observed on the
one real OpenClaw instance this project can inspect shows context growth
alone would push a naive deployment to 2-3x the $2 price in LLM cost.

The idea is feasible with three concrete changes, all achievable using
functionality that already exists in OpenClaw (no fork, no custom agent core
needed, per §5):

1. **Share one gateway process across many tenants** via `openclaw agents
   add`, instead of one container per tenant — this is the biggest single
   lever on both memory (§2.1: 1.5 GB of the 1.7 GB footprint is shared
   plugin code) and ops complexity.
2. **Ship on `tools.profile: minimal`** with an explicit, short allow-list of
   plugins per tier, not the `full` profile the live instance happens to run.
3. **Cap context/compaction hard, not by default behavior** — this is the
   one finding with the most financial teeth (§2.3's $0.62 vs. $4-22/month
   spread is *entirely* about whether this is engineered or left alone), and
   it should be treated as a launch-blocking requirement, not a nice-to-have.

With those three changes, either lengthen the default wake interval toward
1-2h (closer to what the live instance actually runs, and to what the
$0.62/mo "typical" figure assumes) or price the 30-minute tier above $2 —
the current $2/30-min combination is the one part of the spec that the
numbers don't support as stated.

**Separately**: do not co-locate tenant containers on `5.135.131.229` (§6) —
budget for a dedicated host sized like the 92.222.76.121 box (2 vCPU/4GB
comfortably ran one full-profile instance) for the pilot, and treat "how many
tenants fit" as an empirical question to re-measure once a real
`minimal`-profile instance exists, since every density number in this
report above the directly-measured full-profile figures is an estimate, not
a measurement.

## Open items not resolved here (flagged, not answered)

- **openclaw.org's own website Terms of Service** were not checked (only the
  code license was) — worth a quick look before publicly advertising hosted
  resale, even though the code itself imposes no restriction.
- **Whether DeepSeek's API-level prompt caching is actually active** in
  OpenClaw's request path was not confirmed — the §2.3 "no caching" cost
  numbers are the conservative bound; real cost could be meaningfully lower
  if caching is working, but this needs deeper request-log capture than was
  in scope for a read-only survey.
- **No `minimal`-profile instance exists yet to measure** — every density/
  memory number below the directly-measured 598 MB full-profile figure in
  this report is a scaled-down estimate, flagged as such inline, not a
  measurement. A short pilot (`openclaw agents add` with `tools.profile:
  minimal` on a spare box) would convert these into real numbers cheaply,
  before committing to the wider build.
- **x402 facilitator/settlement fees on the $2/month payment itself** were
  not researched for this note; see `docs/x402-facilitator.md` for the
  general mechanics, but the fee's effect on this specific unit economics
  was out of scope here.
- **This idea is not in `CLAUDE.md` §9.1's numbered roadmap.** If pursued,
  it needs an explicit owner decision to add it (with a number/sequencing
  slot), the same gate items 5/9/10/11/12/16/19/21/22/23/25 are already held
  to, given it carries both new infra spend (dedicated host, §6) and a
  container/multi-tenancy security surface not yet designed here (this note
  did not attempt a sandboxing/isolation design for tenant containers running
  arbitrary configured agent workloads next to each other).

## Revised economics: bring-your-own-key model (2026-09-04 correction)

**The business model above has been corrected by the owner.** Sections 1-7
assumed *we* pay for the LLM tokens a hosted instance consumes, and §3
concluded $2/month doesn't cover that ($0.62-$22/mo, realistically $4-6/mo).
**That assumption no longer holds.** The actual plan: we rent out the Docker
**container** (compute/hosting) for one month. The customer brings their own
LLM API key and picks their own model/provider — that cost sits on them
entirely, never on us. We may optionally offer wallet-creation convenience
if they want to fund an agent wallet, but we sponsor no model spend. This
section supersedes §3's verdict; §1, §2.1/2.2 (the empirical footprint
numbers), §4 (license), §5 (build-vs-buy), §6 (platform capacity), and §7
(config surface) are all unaffected and still stand — this section does not
re-derive them, it re-prices against the corrected question: **does $2/month
cover container hosting alone, with zero LLM cost attributed to us?**

No fresh SSH work was needed: the resource footprint (§2.1) — 598-603 MB RSS
steady-state, ~3.1% of one CPU core sustained, per full-profile instance —
already exists and hasn't changed. What was missing was real market pricing
for hosting that footprint, and a check on whether the customer's own LLM
calls could route through anything we operate.

### A. Two cost framings

**Framing 1 — marginal cost on the existing platform host's reclaimed spare
capacity.** §6 already measured `5.135.131.229`'s spare capacity: 11 GB
"available" RAM out of 31 GB, load average ~1.0 on 12 cores, but only 38 GB
disk free (73% used) and several unrelated services (searxng, clamd,
mariadb, valar_daemon) already sharing the box. Running one more ~600 MB /
3%-CPU Node process on a host that's already up, already paid for, and
already metered on a flat/unmetered bandwidth plan costs effectively **$0 in
incremental cash** — there's no per-GB-hour billing to trip. The real
constraint here isn't money, it's the same one §6 already flagged:
**disk headroom and blast-radius risk against production Cassandra/algod**,
which is why §6 recommended *against* co-locating tenant containers there
and put a practical ceiling of ~15-25 tenants on that host regardless of
cash cost. So: for a small pilot (single digits to ~15-20 tenants), the
reclaimed-capacity framing means $2/month is close to pure margin on a cash
basis — but it's capacity-bounded, not free-forever, and inherits §6's
"don't co-locate with prod" recommendation as an operational (not financial)
constraint.

**Framing 2 — dedicated/VPS infrastructure sized for many tenants.** Real
2026 market pricing (Hetzner Cloud, EU region, shared-vCPU line — the same
class as the existing 92.222.76.121 box, which is itself a 2 vCPU/3.7 GB/38
GB VPS):

| Plan | vCPU | RAM | Disk | Price/mo | €/GB RAM |
|---|---|---|---|---|---|
| CX22 | 2 | 4 GB | 40 GB | €3.79 (~$4.10) | €0.95 |
| **CX32** | 4 | 8 GB | 80 GB | **€6.80 (~$7.35)** | **€0.85 (best)** |
| CX42 | 8 | 16 GB | 160 GB | €16.40 (~$17.75) | €1.03 |
| CX52 | 16 | 32 GB | 320 GB | €32.40 (~$35.05) | €1.01 |

(Sourced live 2026-09-04; Hetzner's per-GB price is best at CX32, not at the
larger tiers, so a density box doesn't need to be huge to be efficient.)

Taking CX32 (€6.80/mo, ~$7.35/mo) as the density reference, with ~1-1.5 GB
reserved for host OS + Docker daemon overhead, leaves roughly 6.5-7 GB
usable:

| Tool profile | Per-container RSS | Containers/box (RAM-bound) | Cost/tenant/mo |
|---|---|---|---|
| `full` (measured, §2.1) | ~600 MB | ~10-11 | **~$0.68-0.73** |
| `minimal` (estimated, §2.1/§7's own recommendation) | ~150-250 MB | ~28-35 | **~$0.21-0.26** |

CPU is not the binding constraint in either row: even 30 tenants at the
measured ~3% CPU each is ~90% of *one* of CX32's 4 vCPUs, comfortably inside
budget. Bandwidth (20 TB included) is far more than a checkin-bot polling a
messaging API every 30s and making occasional small LLM calls will ever use
— and per item B below, those LLM calls aren't our bandwidth anyway once
billing is on the customer's own key, though the outbound bytes still
transit our container's NIC either way, which is immaterial at this volume.

### B. $2/month verdict against compute-only cost

| Framing | Cost/tenant/mo | $2 price vs. cost |
|---|---|---|
| Reclaimed platform-host capacity | ~$0 cash (capacity-bounded, not cash-bounded) | Comfortably profitable on cash basis; real constraint is tenant-count ceiling from §6, not price |
| Dedicated density box, `full` profile | ~$0.68-0.73 | **~$1.27-1.32/mo gross margin (63-66%)** |
| Dedicated density box, `minimal` profile | ~$0.21-0.26 | **~$1.74-1.79/mo gross margin (87-90%)** |

**This is a complete reversal of §3's verdict.** §3 found $2/month couldn't
cover realistic LLM cost ($4-22/mo) because it was pricing tokens we no
longer pay for. Once only compute/hosting is being priced, $2/month is
comfortably profitable under every framing checked here, including the most
conservative one (full tool profile, one isolated container per tenant, no
gateway-sharing). It gets more profitable, not less, if the `minimal`-profile
/ shared-gateway efficiencies §5 and §7 already recommended are actually
built — those were originally justified as *necessary to survive* the
LLM-cost problem; under the corrected model they're pure margin upside
instead of a break-even requirement.

Two costs outside this section's scope still apply and aren't zeroed out by
the correction: the x402 facilitator/settlement fee on the $2 USDC payment
itself (unresearched, flagged in the original "Open items" list below) and
ordinary support/ops overhead (backups, monitoring, incident response) —
neither is compute/hosting, and neither was quantified here.

### C. Hidden shared-infrastructure cost check (item 8 of the task)

Checked OpenClaw's actual request flow, not assumed: **there is no hidden
shared-infrastructure cost.** Per OpenClaw's own docs
(`docs.openclaw.ai/concepts/model-providers`, `docs.openclaw.ai/gateway/config-tools`,
`docs.openclaw.ai/gateway/security`) and consistent with the live box's own
config already read for §2.2 of this document:

- Each model provider is configured with its own `baseUrl` and `apiKey`
  under `models.providers.<id>` — credentials are stored in a local SQLite
  auth store **on the tenant's own container/box**, not centrally.
- The Gateway process calls that `baseUrl` **directly** from inside the
  container when a turn runs (§2.2 already established there's no held-open
  connection — it dials out only per-turn). There is no default "OpenClaw
  Cloud" relay, proxy, or model-routing service sitting between the
  container and the provider that OpenClaw itself operates.
- The Gateway defaults to binding to loopback (`docs.openclaw.ai/gateway/security`),
  i.e. it's built for local/self-hosted operation, not a hosted-relay
  architecture.
- Third-party tutorials exist for people who *choose* to insert their own
  cost-control proxy in front of OpenClaw (e.g. rate-limiting an API key
  across many agents) — that's an optional pattern some operators adopt, not
  something OpenClaw ships or requires by default, and nothing in the live
  box's config (§2.3, §7) shows this instance using one.

Net: a customer's bring-your-own-key LLM traffic goes straight from their
rented container to their chosen provider (DeepSeek, OpenAI, Anthropic,
whatever `baseUrl` they configure). It never touches this project's backend,
Cassandra, Redis, or any shared platform component — the only infrastructure
of ours in the loop at all is the VPS/host the container runs on, which is
exactly the compute/hosting cost already priced in §A/§B above. **Confirmed,
not assumed: no hidden LLM-adjacent cost exists in this model.**

One minor unresearched edge, flagged rather than assumed away: whether
OpenClaw itself makes any lightweight phone-home call (update check,
license/telemetry ping) to `openclaw.org` infrastructure was not confirmed
either way in the docs pulled here. Even if it exists, it would be a
negligible, infrequent, low-byte call to OpenClaw's own servers, not ours —
irrelevant to this project's cost, only worth knowing about for its
own privacy-disclosure reasons before advertising the product publicly.

### D. Updated bottom line

**$2/month works, comfortably, once the product is correctly scoped as
compute/hosting only.** The original blocking finding (§3's $4-22/mo LLM
cost) doesn't apply to the corrected bring-your-own-key model at all — that
cost is now the customer's, in full, by design. Against compute/hosting
alone, $2/month clears cost under every framing checked (near-zero on
reclaimed capacity, ~$0.21-0.73/tenant/mo on dedicated density
infrastructure), leaving healthy margin before the still-unresearched
facilitator fee and ops overhead are subtracted. The engineering
recommendations already made in §5/§7 (minimal tool profile, shared-gateway
multi-tenancy, dedicated host away from prod per §6) remain worth doing —
they now improve margin rather than being required to avoid a loss — and
the license/build-vs-buy/capacity findings in §3-§7 are otherwise unchanged
by this correction.
