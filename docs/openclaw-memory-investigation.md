# OpenClaw memory/CPU investigation — is 600MB justified?

Status: research only, no code written, no lasting changes to the live
instance. Untracked file — not committed. Date: 2026-09-04.

## 0. What was asked

Owner, verbatim (condensed): "this agent is pretty stupid, is not doing
much... it's consuming six hundred megabytes... [to] read a few files, keep
a few things in memory, get a context and communicate with a few APIs...
what the fuck, why is it so heavy?"

This picks up where `docs/openclaw-pay-for-hire-feasibility.md`
(2026-09-04, earlier the same day) left off. That doc measured ~600MB RSS
once, on the live box, and *assumed* `tools.profile: minimal` would
"cut... memory... substantially below" that. This investigation tests that
assumption directly instead of repeating it, and answers the "why is it so
heavy" question with a real breakdown rather than a shrug.

**Method**: read-only inspection of the live `openclaw-gateway` process on
`92.222.76.121` (`root@`, service runs as `ubuntu`, per
`docs/ops-hosts.md`), plus controlled experiments in a fully isolated
`--profile memtest` sandbox (separate config dir, separate port, no
channels, no LLM providers wired up) so nothing touching the real
instance's config, sessions, or Telegram polling was ever modified. The
sandbox directory was deleted at the end; the live `openclaw.json` and the
live gateway process (PID 285503, same invocation since before this
investigation started) were never touched — verified below.

## 1. Direct answer

**Both — but not evenly split, and not the way the earlier doc assumed.**

- **~500-540MB of the ~600-660MB is a fixed platform floor**, present even
  in a freshly-started instance with *zero* channels connected, *zero* LLM
  providers configured, and only 6-13 of 64 plugins loaded. This part is
  "just how this specific framework is built" — not fixable by a config
  flag. It is real allocated memory (89% anonymous/private-dirty per
  `smaps_rollup`, not shared file-backed pages), so it's not an illusion of
  `ps`/`systemd` accounting either.
- **`tools.profile: minimal` does not meaningfully help — this is a
  correction to the earlier doc, verified empirically today, not a
  restatement of it.** Switching only `tools.profile` from `full` to
  `minimal` on an otherwise-identical config changed RSS by **less than the
  run-to-run noise** (527MB → 543MB, i.e. *higher* on the "minimal" run) and
  loaded the exact same 13 plugins in both cases. The config schema explains
  why: `tools.profile` is described as a *"predefined tool policy baseline...
  before applying allow/deny overrides"* — it's an allow-list gate on which
  tools an already-loaded plugin may expose to the agent, not a switch that
  changes which plugin code gets `require()`'d into the process.
- **The lever that actually is a plugin-loading gate (`plugins.entries.*.enabled`)
  works, but the savings are small.** Explicitly disabling the 7 heaviest
  candidate plugins (browser, canvas, cua-computer, talk-voice, linux-node,
  ollama, xai) dropped the loaded count from 13 to 6 and moved RSS from
  ~527-543MB to **536MB** — statistically indistinguishable from before.
  Going from the live instance's real 45 loaded plugins down to 6 in the
  isolated test corresponds to roughly **70-90MB total**, i.e. on the order
  of 1-2MB per plugin, not the "cut it substantially" the earlier doc hoped
  for.
- **There is a separate, real, fixable-shaped issue**: the long-lived
  `agent:main:main` heartbeat session is still sitting at 55-56% of its
  200K-token context window (unchanged in shape from the earlier doc's
  finding, still not compacted, still growing) days after the session
  started. That's a genuine "conversation history not being pruned"
  problem — but it's primarily a **token-cost** problem (already quantified
  in the earlier doc: $0.62 vs $4-22/month spread) more than a **process-RSS**
  problem; a 30-minute observation window here isn't long enough to cleanly
  attribute a specific MB delta to it (a real Telegram message also arrived
  during the observation window, confounding a clean before/after read).

So: the owner's instinct that 600MB is a lot for "read a few files, hold a
short context, call an LLM, call a couple tools" is **correct** — a
bare-bones implementation of that exact job would plausibly run at
one-tenth this footprint (§5). But the reason isn't sloppy plugin
management on this instance; it's that OpenClaw is architecturally a
maximalist single-process platform (computer-use, canvas, voice,
~20+ LLM-provider SDKs, a coding-agent toolchain with TypeScript/tree-sitter
bundled in, MCP/ACP protocol bridges, its own HTTP/dashboard server, vector
memory search, etc.) and all of that shares one Node process and one
dependency graph whether or not a given tenant ever touches it. `tools.profile`
does not un-load any of that graph.

## 2. Memory breakdown (measured)

### 2.1 Live process, right now

```
PID 285503, uptime at first sample: 2h50m (systemd unit last (re)started
2026-09-04 09:36:52 UTC — this is a recent restart, not a multi-day-old
process; host uptime is 5+ days, the gateway itself is much younger)

First sample:
  VmRSS   613,788 kB   VmHWM (peak-since-start) 1,115,088 kB
  VmData 1,733,400 kB  Threads: 12

~30 min later (no restart, one real Telegram exchange arrived in between):
  VmRSS   656,040 kB   VmHWM 1,115,088 kB (peak did not move — no new spike)
  VmData 1,774,028 kB
```

`smaps_rollup` at the first sample:

| Field | Value | What it means |
|---|---|---|
| Rss | 613,788 kB | total resident |
| Pss_Anon | 545,856 kB (89%) | anonymous/private memory — V8 heap, Buffers, native-addon allocations. This is genuine working memory, not shared/reclaimable file cache. |
| Pss_File | 65,088 kB (11%) | file-backed mappings (loaded `.node`/binary segments, shared libs) |
| Pss_Dirty | 545,904 kB | dirtied pages — confirms it's actively-used memory, not a big lazily-mapped reservation |

**Takeaway**: this isn't "Node reserved a big virtual range and only touched
a sliver of it" — the RSS number is close to 90% real anonymous/dirty
memory. The "why is it so heavy" question has a real answer, not an
accounting artifact.

### 2.2 Baseline: bare Node.js on the same box, same Node version

```
node -e 'setInterval(()=>{}, 1000)'   (Node v24.20.0, same host)
→ VmRSS: 43,472 kB (~42MB)
```

This is "the platform" cost — V8 startup + the Node runtime with zero
application code. Everything above 42MB is OpenClaw's own code + its
dependency graph + runtime state.

### 2.3 Isolated OpenClaw gateway, minimum realistic config (new today, controlled)

An isolated `--profile memtest` instance (own config/state dir, own port
19099, no channels, no LLM providers configured — so it can't spend money
or touch the real Telegram bot) was started and measured 12s after launch,
three times, varying only the plugin-loading knobs:

| Config | Plugins loaded | RSS @ 12s |
|---|---|---|
| `tools.profile: full`, default plugin set | 13 (anthropic, browser, canvas, cua-computer, device-pair, file-transfer, geolocation, linux-node, memory-core, ollama, openai, talk-voice, xai) | 527 MB |
| `tools.profile: minimal`, same plugin set (only the profile flag changed) | **13 — identical list** | 543 MB (higher, within noise) |
| `tools.profile: minimal`, + 7 heaviest plugins explicitly disabled via `plugins.entries.*.enabled: false` | 6 (anthropic, device-pair, file-transfer, geolocation, memory-core, openai) | 536 MB |

Three data points, three RSS values within 16MB of each other, despite the
plugin count more than doubling between the extremes. **The floor is not
plugin-count-driven.**

### 2.4 Where the fixed floor actually comes from (disk footprint as a proxy for what's compiled/bundled in)

```
~/.npm-global                                             897 MB  (whole OpenClaw install)
  openclaw/dist                                            212 MB  (bundled runtime code)
    openclaw/dist/extensions (all 64 plugins' code)          9.2 MB  ← plugin code itself is small
  openclaw/node_modules                                    672 MB  (dependency graph, shared regardless of profile)
    @anthropic-ai        343 MB   (by far the largest single dependency)
    @trycua                40 MB   (cua-computer / computer-use)
    @mistralai             28 MB
    typescript             24 MB   (bundled for the coding-agent toolchain)
    openai                 22 MB
    tree-sitter-bash       20 MB   (bundled for the coding-agent toolchain)
    @openclaw (internal)   20 MB
    @google                17 MB
    playwright-core        14 MB
    @opentelemetry         12 MB
    ... (highlight.js, zod, typebox, MCP SDK, ACP SDK, hono web server,
         kysely ORM, protobufjs, tar, lru-cache, ...)
~/.openclaw (live instance's runtime state, separate from the install)     1.7 GB total
  ~/.openclaw/npm (locally-installed *external* provider plugins,
                    e.g. deepseek/mistral/duckduckgo/parallel —
                    shared across all agents on this one gateway)          1.5 GB
  agents/ (per-agent session state)                                        107 MB
  browser/ (Chromium profile cache — unused by a text-only bot)             66 MB
  cache/ (model/tool cache)                                                 28 MB
  state/ (gateway/session runtime state)                                    15 MB
```

The plugin **code** itself is tiny (9.2MB across all 64 extensions — these
are thin wrapper files). What's heavy is the **dependency graph** each
plugin pulls in (SDKs for ~20 LLM providers, a coding-agent toolchain with
TypeScript and tree-sitter bundled in, a computer-use driver, opentelemetry,
a full web framework and ORM for the dashboard). That graph is one shared
`node_modules` tree for the whole process — `require`d modules stay in
memory once loaded regardless of which config flag nominally "disabled" the
plugin that pulled them in, because other still-enabled plugins or the core
gateway itself may share the same underlying dependency (e.g. `openai`'s
SDK is reused by multiple provider plugins; `@opentelemetry` is core
instrumentation, not plugin-specific).

## 3. Does `tools.profile: minimal` help? — No, corrected from the earlier doc

The earlier feasibility doc (§2.1, §5, §7) assumed `minimal` would
"shrink memory further" and estimated 150-250MB for a minimal-profile
tenant, explicitly flagged as "not directly measured, no minimal-profile
instance exists yet to sample." **That instance now exists (today, in the
isolated sandbox) and the assumption doesn't hold**: `tools.profile` alone
moved RSS by less than measurement noise (§2.3). Every density/cost number
in the earlier doc that was scaled down from the 598MB figure using a
"minimal should shrink it" assumption (the $1-2/mo infra-share line in
§2.4, the 150-250MB/instance density estimate in §6, the "smaller baseline
memory" bullet in §5) should be treated as **not supported** by today's
measurement — the realistic per-instance floor for OpenClaw, even
aggressively pared down, is closer to the ~530-540MB measured here than to
150-250MB.

What *did* move the needle, a little: explicitly disabling individual
plugins via `plugins.entries.*.enabled: false` (13→6 plugins, 527-543MB →
536MB — call it a genuine but small ~0-15MB saved, within the noise floor
of these short 12-second-settle measurements). This is a real, if modest,
lever; `tools.profile` is not.

## 4. Growth over uptime

The live gateway (PID 285503) has been running 2h50m-3h20m across this
investigation (started 2026-09-04 09:36:52 UTC by the systemd unit — a
recent restart, not multi-day). Over a ~30-minute window with no restart:

```
613,788 kB → 656,040 kB   (+42MB, +7%)
VmHWM (peak-ever-since-start) unchanged: 1,115,088 kB both times
```

RSS is not perfectly flat, but this window isn't clean evidence of an
unbounded leak either — a real Telegram message arrived and was answered
during the window (visible in `openclaw status`'s session table: a new
`agent:main:telegram:direct:...` session appeared "just now"), which is a
legitimate reason for new session buffers to allocate. Peak-since-start
(VmHWM) not moving is a mild point *against* runaway growth: if this were
classic unbounded accumulation, the high-water mark would be climbing too,
not just current RSS bouncing near it.

What *is* confirmed, and distinct from a process-memory leak: the
long-lived `agent:main:main` heartbeat session has sat at **55-56% of its
200K-token context window for multiple days** without ever compacting
(same finding as the earlier doc, now reconfirmed on a later measurement —
it was 55% on 2026-09-03/09-04 and is 56% now). That's real unpruned state,
but its cost shows up primarily as **LLM token spend on every future
heartbeat turn** (already quantified in the earlier doc), not as a
dominant contributor to the ~600MB RSS figure — a 100K-token cached string
buffer is on the order of a few hundred KB to low single-digit MB in
memory, not hundreds of MB. Don't conflate the two: the context-bloat bug
is real and worth fixing on its own (compaction is a config knob that
exists — `agents.defaults.heartbeat`/session maintenance — and isn't
being exercised), but it is not the explanation for the RSS number the
owner is asking about.

## 5. What a genuinely minimal loop would cost

Bare Node.js baseline measured on this exact box: **~42MB** for V8 + Node
runtime with no app code (§2.2). A real implementation of "read a few
files, hold a short context, call an LLM API, call a couple of tool APIs"
needs, on top of that baseline: an HTTPS client (built into Node, ~0 extra),
a JSON parser (built-in), a small conversation-history buffer in process
memory (KBs, not MBs, for anything short-lived), and maybe a couple of thin
SDK wrappers for the specific LLM provider and tool APIs actually in use
(a few MB each, not the ~20-provider graph OpenClaw loads regardless of
which one is configured). **Plausible order of magnitude: 60-150MB RSS**,
dominated by the Node baseline itself plus whatever the one chosen LLM
SDK and a small HTTP framework add — not the 500-600MB+ observed here.
That is roughly a **4-10x** difference, i.e. the "order of magnitude"
the owner's framing was reaching for is basically right, not an
exaggeration.

A different runtime (e.g. a small Python or Go process) wouldn't change
this qualitatively — the point isn't "Node is heavy," it's that OpenClaw's
specific dependency graph (computer-use drivers, ~20 LLM provider SDKs, a
coding-agent toolchain, telemetry, its own web server/ORM) is heavy, and a
bare interpreter/runtime baseline is a small fraction of the total either
way (Python's own baseline is smaller than Node's ~42MB, for what it's
worth, but that's not where the 500MB+ delta comes from).

## 6. Recommendation

**Not "just how Node agent frameworks are" as a blanket excuse, but also
not a quick-fix situation** — the one config change that looked like an
easy win (`tools.profile: minimal`) is empirically not one, as of today's
test. Concretely:

1. **Do not expect a `tools.profile` change to fix this.** It changes tool
   *policy* (what the agent is allowed to call), not what gets loaded into
   the process. If this gets revisited, don't re-propose it without new
   evidence — today's test is fairly conclusive on the live-adjacent
   sandbox.
2. **Disabling individual unused plugins (`plugins.entries.*.enabled: false`)
   is real but small** (tens of MB at most for this instance's actual
   unused set — browser/canvas/cua-computer/talk-voice/linux-node are all
   plugins a marketing/tester Telegram bot has no business having enabled,
   per `docs/ops-hosts.md`'s own description of what this instance is for).
   Worth doing for **attack-surface** reasons (device-control, computer-use,
   and voice plugins on an internet-reachable bot are a real exposure this
   instance doesn't need) even though it won't move the memory needle much.
3. **For *this* instance specifically** (a single always-on marketing/
   seller/tester agent, not the many-tiny-tenants hosted product the
   earlier doc was scoping): ~500-650MB is close to a hard floor for
   running upstream OpenClaw as shipped. Accepting it is reasonable if the
   value (multi-provider model routing, the x402/Algorand payment plugin
   already wired up, the coding-agent tooling, MCP/ACP bridges) is actually
   being used. If it isn't — if this instance really is "read a few files,
   answer a Telegram DM, call an LLM sometimes" — the honest conclusion is
   that **OpenClaw is the wrong-sized tool for this specific job**, and a
   bespoke ~100-200 line Node (or any language) script doing exactly that
   loop would plausibly run at roughly a tenth of the memory, with zero of
   the unused attack surface. This is a materially stronger version of the
   "maybe build something lighter" instinct than the earlier doc reached,
   because that doc's main counter-argument for "use OpenClaw, don't build"
   was partly a memory argument ("§2.1... `minimal` profile should shrink
   it further") that today's measurement retracts. The other reasons that
   doc gave to prefer OpenClaw (existing x402/Algorand payment plugin, the
   multi-tenant `agents` primitive for a *hosted* product) are unaffected
   by this correction and still stand for the multi-tenant hosted-checkin-bot
   idea specifically — this recommendation is about the single always-on
   agent on `92.222.76.121` today, not a reversal of that separate
   analysis.
4. **The stuck-at-55% session context** is a separate, real bug worth
   fixing on its own merits (cost, not RSS) — either lower
   `agents.defaults.heartbeat.every` less aggressively, or find and use
   whatever compaction/session-maintenance knob OpenClaw exposes (the
   config schema references `session.maintenance`-style settings; not
   fully explored here, flagged as a follow-up, not fixed).

## Verification that the live instance was left unchanged

```
Live config, checked after cleanup:
  tools.profile           = "full"      (unchanged from start of investigation)
  channels.telegram.enabled = true       (unchanged)

Live gateway process:
  Main PID 285503, same systemd Invocation ID (c2ebe8efcc9c4ca4bb1b8e77f87d99fc)
  throughout the entire investigation — never restarted, never reconfigured.

All experimentation happened under an isolated `--profile memtest`
config/state directory (own port 19099, telegram channel/plugin disabled
in that copy, no LLM provider credentials referenced), which was deleted
(`rm -rf ~/.openclaw-memtest`) at the end of the investigation.
```

## Open items not resolved here

- Could not get a true V8 heap-vs-external breakdown (old-space vs Buffers
  vs native-addon heap) from the *live* process without attaching an
  inspector or restarting with `--heapsnapshot-signal`, which would have
  meant modifying a live prod-adjacent process's startup flags — out of
  scope for a read-only investigation. `smaps_rollup`'s anonymous/file
  split (§2.1) is the closest safe proxy and is enough to answer "is this
  real memory" (yes) even though it can't cleanly separate V8 heap from
  native addon allocations within the anonymous portion.
- Did not locate or exercise OpenClaw's actual context-compaction /
  session-maintenance config knob for the stuck 55%-of-window session
  (§4) — flagged, not fixed, consistent with this being a read-only
  investigation task.
- The 12-second settle window used for the isolated-instance RSS
  measurements (§2.3) is short; a longer settle (60s+) might show slightly
  different absolute numbers, but the *relative* comparison between the
  three configs (which is the load-bearing finding — profile doesn't
  matter, plugin count barely does) used the same settle time for all
  three, so the comparison itself should be robust even if the absolute
  527/543/536 MB figures would drift a little with more settle time.
