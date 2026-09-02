# Owner-supplied article sources ("enrich context") — design document (not yet built)

Owner ask (2026-09-02): *"Would there be possible to add some sources for a
selected article? An example: I have an exclusive interview with the creator
of AlgoSprout. Or extra data. How would we do it? Like enrich context."*

Prompted by a real case from the same session: the sproutalgo.com article
("Sprout relaunches on Algorand Python contracts, opens 25,000 ALGO Builder
Challenge", article `f24efe5f-e5df-4eb5-a5b6-6ff6ef7a0013`, review
`a3c1c2fa-0f59-483f-b762-ae46e380043c`) is on hold: the writer researched, the
`company_backing` completeness signal failed because nothing verifiable about
who is behind Sprout exists in public sources — but the owner personally has
exclusive material that answers exactly that question, and today there is no
way to hand it to the pipeline.

This document designs that hand-off: attach owner-held evidence (an interview
transcript, extra data) to a SPECIFIC article, and have the next recompose of
that article treat it as first-class grounding material, the same way the
writer's own auto-fetched research already is.

Scope: `workers/` (Celery pipeline) + `backend/` (admin API) + the admin
frontend. Nothing here touches the x402 marketplace.

---

## 0. The one conclusion the owner should read first (gate interaction)

The owner's mental model is likely "my interview will make the gate pass."
That is mostly **not** how it works, and the feature is still worth building.
Three separate mechanisms are in play, and they react differently:

**(a) Completeness rules (`company_backing`, `human_identity`) — not the
lever, and on this path they don't gate anyway.** These rules
(`workers/app/modules/gatekeeper/completeness.py`) are keyed on **tool
names**: "if the drafted article contains trigger words like *incorporated /
ltd / founder*, did `query_corporate_registry` /
`screen_sanctions_and_pep` appear in the tool trace?" They are an audit that
mandatory *verification actions ran*, not that the answer was found. Two
consequences:

1. Owner-pasted text is not a tool run, and we will **never** store it under
   a real tool name (e.g. a fake `query_corporate_registry` trace row) to
   satisfy a rule — that forges the audit trail the rule exists to protect.
   The synthetic trace entry is always named `admin_supplied_source`
   (section 3), which no completeness rule accepts, on purpose.
2. It doesn't matter for the held-article flow, because completeness is
   **display-only metadata on every recompose/held-review path** — excluded
   from `gate_ok` since 2026-07-12 (see `_grade_and_gate`'s docstring in
   `workers/app/modules/newspaper/tasks/publish_tasks.py`). Completeness
   only *gates* on the fresh-publish auto-approve path
   (`_fresh_auto_approve_passes`), which is how the Sprout article got held
   in the first place. Once an article is in the review queue, the exit is
   the owner's own Approve click — a human override that already exists.
   The recomposed draft will land back in the queue with fresh metadata,
   and the owner approves it. No completeness rule needs to change, and in
   v1 none does.

   (Also worth knowing: after the 2026-09-02 fix, completeness triggers read
   the **drafted article**, not raw source pages. If the enriched draft
   legitimately says "incorporated as … Ltd" because the interview said so,
   the recompose's own research loop still calls the registry tool itself —
   the writer re-researches on every recompose — so the tool name lands in
   the trace the honest way, whatever it finds.)

**(b) Numeric entailment (`gk_factuality`) — directly and materially
helped.** `numeric_entailment_score` grounds every figure in the drafted
article against the stored tool trace. Today, if the writer were handed an
interview only as prompt text and quoted "25,000 ALGO prize pool" or "3
core contributors" from it, those figures would have **no trace anchor** and
would be flagged ungrounded — the feature would make articles *fail harder*.
That is why section 3 injects the owner source into the trace as well as the
prompt: figures the writer takes from the interview become grounded anchors,
exactly like a tool result. This is the one gate signal the feature
genuinely improves, and it's also what keeps `gk_factuality` from being the
*new* reason an enriched recompose gets held.

**(c) The writer itself — the real point of the feature.** The main value is
not gate arithmetic; it's that the writer can finally *write the true
claims*: "Sprout's creator told PXke Algorand in an interview that…". A
company-backing story the public web cannot support becomes an article with
an exclusive, attributed source. The gate then has something real to measure
instead of an absence.

**Expectation to set in the UI copy**: attaching a source never auto-passes
or auto-publishes anything. It gives the writer exclusive material for the
next recompose; the recomposed draft still lands in the review queue for the
owner's Approve. That final click is, and remains, the human override for
"the public web can't verify this, but I can."

---

## 1. Input shape: pasted text first, URL fetch later, no file upload

**v1 accepts one shape: pasted text**, with a short mandatory label and an
optional attribution URL (stored as provenance metadata, not fetched).

- *Pasted text* covers the owner's own examples (interview transcript,
  "extra data") and is the only shape with zero new failure modes: no
  fetch, no parser, no SSRF surface, no Playwright lifecycle. Label example:
  `"Exclusive interview with the Sprout creator, 2026-09-02"` — the label is
  what the writer is told about provenance, so it must say what the material
  *is* and when it was obtained.
- *URL fetch* is Phase 2, not v1. When it comes, the backend must **not**
  fetch (backend rules: stream + cap remote fetches, and Playwright lives in
  `workers/`); instead the URL is stored with `kind='url'` and the
  **recompose task** fetches it at compose time via the existing
  `get_scraper_for_url(...)` helper — same scrape path
  `_recompose_published_source_text` already uses, same politeness and
  fallback behavior. Deferred because the pasted-text version already solves
  the stated case and URL content the crawler can reach is mostly content the
  writer's own `fetch_url` tool can already reach.
- *File upload* is out of scope entirely. There is no admin upload
  infrastructure, backend rule "bound request bodies" applies, and a
  transcript/document is text — paste it. A future need for PDFs would be
  its own design.

Bounds: `content` capped at `ADMIN_SOURCE_MAX_CHARS` (new setting,
`workers/app/core/config.py` + backend `Settings`, default 100 000 chars —
roughly a 90-minute interview transcript), label at 200, attribution URL at
512. The backend rejects oversize with a 400, never truncates silently.

## 2. Storage: a new first-class table, projected into the trace at compose time

Two candidate homes were considered:

**Rejected: writing owner sources directly into `investigation_findings` as
synthetic rows.** Tempting (zero schema change; the gate reads it as-is), but
wrong as the *canonical* store:

- `investigation_findings` is a machine-written, append-only audit trail of
  what the agent actually did. It has no update/delete path, and mixing
  curated human input into it means "remove that source" is impossible and
  every future reader of the trail must know some rows are not tool runs.
- It is keyed by `service_id == source_url`, not `article_id` — but the owner
  attaches evidence to *an article*. Sharing one URL key means the source
  would leak into every future compose for that URL with no way to scope it.
- The read side is `LIST … LIMIT INVESTIGATION_TRACE_MAX_ENTRIES` (200),
  newest-first: a source written once would age out of the window as later
  composes append their own ~dozens of rows. The owner's evidence silently
  disappearing after N recomposes is exactly the kind of bug section 2 of
  CLAUDE.md exists to prevent.
- `result_json` rows are capped at `INVESTIGATION_RESULT_MAX_CHARS` (16 000)
  — a transcript doesn't fit in one row anyway.

**Chosen: a new table, `article_admin_sources`, keyed by `article_id`, plus
a per-compose projection into the trace.** Canonical storage is durable,
editable, and scoped to the article; at recompose time the active sources are
projected into the compose session's trace as `admin_supplied_source` entries
(section 3), which is re-done fresh on every recompose — so the gate and
chart-grounding always see it, and it can never age out of the LIST window,
because it rides with each compose's own batch.

### 2.1 Schema sketch (migration `backend/schema/migrations/app/107_article_admin_sources.cql`)

```cql
USE algorand_platform;

-- Owner-attached evidence for a specific article (exclusive interviews,
-- documents, extra data the public web cannot provide). Canonical store;
-- projected into investigation_findings as `admin_supplied_source` trace
-- entries at each recompose, never written there directly.
CREATE TABLE IF NOT EXISTS article_admin_sources (
  article_id uuid,          -- the live/held article this evidence belongs to
  source_id timeuuid,       -- attach time, newest first
  added_by text,            -- admin wallet (audit)
  label text,               -- provenance the writer is told, e.g. "Exclusive interview with X, 2026-09-02"
  kind text,                -- 'text' (v1); 'url' reserved for Phase 2
  attribution_url text,     -- optional; provenance metadata in v1, fetch target in Phase 2
  content text,             -- the pasted material (bounded by ADMIN_SOURCE_MAX_CHARS)
  status text,              -- 'active' | 'removed'
  added_at timestamp,
  removed_at timestamp,
  PRIMARY KEY ((article_id), source_id)
) WITH CLUSTERING ORDER BY (source_id DESC);
```

- Access patterns are all single-partition: admin UI list, recompose-time
  load. Every read carries a LIMIT (say 50 — nobody attaches 50 sources; if
  they do, newest 50 win and the UI says so). No `ALLOW FILTERING`, no
  second projection needed.
- Removal is a **soft** `status='removed'` (+ `removed_at`), not a DELETE:
  a recomposed live article's provenance ("what evidence informed this
  version?") must survive the owner later retiring the source. The
  projection (section 3) only includes `active` rows.
- Manifest: new `[[migrations]]` entry in `schema/migrations/manifest.toml`
  (version 107, stream "app"), no semicolons inside CQL comments — per the
  existing convention.
- Statements: read side is needed by `workers/`, write side by `backend/`,
  so the prepared statements go in
  `shared/algorand_shared/admin_source_statements.py` (the CLAUDE.md rule for
  shared CQL — same pattern as `algorand_shared/article_statements.py`),
  never duplicated into both services' `statements.py`.

### 2.2 Why keyed by `article_id`, and how each recompose path resolves it

The owner selects *an article* (held or live), so `article_id` is the natural
key — but the two recompose paths hold it differently:

- `recompose_review(review_id)` already extracts `old_article_id` from the
  review row's metadata (it reuses it as the draft row id). Sources are
  loaded by that id. A review row with no `article_id` in metadata (shouldn't
  happen for compose-produced reviews) simply has no attach affordance — the
  UI hides it rather than inventing a key.
- `recompose_published(article_id)` receives the live article id directly.
  The new draft it mints gets its own id, but sources stay attached to the
  **live** id — which is also what survives `apply_recomposed_article`'s
  content swap, so the attachment naturally persists across recomposes and
  future weekly-cadence refreshes.

Persistence across cadence recomposes is deliberate: an exclusive interview
stays true grounding material next month too. To keep it from silently going
stale, the projected prompt block includes each source's `added_at` and the
same "may be days or years old — judge against today's date" instruction the
source material block already carries; the owner retires a source via the UI
when it stops being true.

## 3. Compose-time injection: prompt block + trace seed, together, always

`compose_scrape_article` (`workers/app/modules/ai/llm_compose.py`) gains one
optional parameter, `admin_sources: list[AdminSource] | None = None`
(defaulting to None so every existing call site — fresh publishes, briefs,
benchmarks — is untouched). When present, two things happen, and it is a
design invariant of this feature that they **always happen together**:

1. **Prompt block.** A clearly-labeled section is appended to the compose
   user prompt, *after* the scraped source material, with its own clip
   budget (new `ADMIN_SOURCE_PROMPT_MAX_CHARS`, so owner material never
   competes with the scrape for `LLM_MAX_SOURCE_CHARS`):

   ```
   ## OWNER-SUPPLIED SOURCE MATERIAL (exclusive to this newspaper)
   The publisher personally obtained the material below; it is not on the
   public web. Treat it as a primary source. Attribute claims drawn from it
   to its stated provenance (e.g. "told PXke Algorand in an interview"),
   never to the service's website. Judge its age against today's date.

   ### [label] (added 2026-09-02)
   <content>
   ```

   This mirrors the precedent of `recompose_published`'s
   `extra_source_material` "## RETIRING PRIOR COVERAGE" block — labeled
   provenance folded into source material — but as a structured parameter
   rather than string-splicing at the call site, because unlike that one-off
   batch flag this also has to reach the trace.

2. **Trace seed.** Before the research loop starts, one synthetic entry per
   source is appended to the session's `trace` list:
   `{"tool": "admin_supplied_source", "arguments": {"label": ..., "added_at": ..., "attribution_url": ...}, "result": {"text": <chunk>}}`
   — chunked at `INVESTIGATION_RESULT_MAX_CHARS` (16 000) per entry
   (`part 1/3` markers in the arguments) so `store_investigation_findings`'s
   per-row cap never silently truncates a transcript. Seeding the live
   session list gets three consumers for free, with zero gatekeeper changes:
   - `_record_compose_telemetry` persists it to `investigation_findings`
     with the compose's own batch → `gate_draft`'s
     `load_investigation_trace(source_url)` sees it → interview figures
     ground `numeric_entailment_score` (section 0b).
   - `chart_tools`' session-trace grounding accepts chart values sourced
     from the interview.
   - The compose-session transcript (Sessions tab) shows the owner exactly
     what the writer was given, in the same `tool(args) -> result` shape as
     everything else.

   The tool name `admin_supplied_source` is reserved: it must never collide
   with a real writer tool, and no completeness rule lists it (section 0a).

**Failure semantics at the seam (invariants #8/#9 applied):** the recompose
tasks load sources *before* any LLM spend. "No rows" is a normal, silent
no-sources recompose. A Cassandra **read error** on an admin-triggered
recompose fails **closed**: return an explicit
`{"status": "admin_sources_unavailable"}` (and, on the review path,
re-enqueue the review exactly like `_recompose_via_writer`'s existing LLM
failure branch) rather than proceeding without the owner's evidence — the
owner clicked recompose *because of* the attachment; composing without it
burns a paid compose to produce the same inadequate article, and per
invariant #8 an error must never be presented downstream as "none found".
Nothing has been paid for at that point, so invariant #1 (never discard a
finished compose) is not implicated; once compose *has* run, this feature
changes nothing about the existing storage paths.

## 4. Trigger: attach, then recompose — two deliberate steps

Attaching a source stores it and does nothing else. The recompose is the
owner's existing, explicit second click:

- Held article → the review queue's **Recompose** button
  (`admin_recompose_review` → `recompose_review`), which now loads and
  injects the article's active sources.
- Live article → the Articles tab's **Recompose** button
  (`admin_recompose_article` → `recompose_session_service` →
  `recompose_published`), same injection.

Why not auto-trigger on attach: the owner may paste several pieces (the
transcript, then a follow-up figure) — auto-firing on the first attach pays
for a compose against half the evidence, and `recompose_published`'s
pending-review veto would then block the second, corrected attempt behind
the first's still-open review. Two steps also keeps this feature additive:
zero changes to when composes happen, only to what they're given. The only
UI nicety: after a successful attach, the panel nudges "Source attached —
Recompose when ready" next to the existing button.

What the owner sees if the recompose fails — unchanged from today, which is
already invariant-clean, verified against the current code:

- `recompose_review`: the old review is completed (`recomposing`) up front to
  free the 1-slot queue, and on LLM failure/peak-hours deferral the original
  proposal is **re-enqueued** with a `recompose_failed` marker
  (`_recompose_via_writer`), so the held article and its review row survive;
  the attached sources are not consumed and the owner just clicks again.
- `recompose_published`: the live article is untouched until
  `apply_recomposed_article` succeeds (store-before-mark already enforced at
  that call site); failures leave an explicit error status and, at worst, an
  unlisted on-hold draft — never a mutated live page.

## 5. Backend API (`backend/app/modules/admin/api/routes.py`)

Three handlers, every one calling `require_admin_wallet` first, bodies
bounded, errors via `json_error_response` (never `str(exc)` on a 500):

```
POST   /api/v1/admin/articles/:article_id/sources
       {label, content, attribution_url?}        -> {source_id}
GET    /api/v1/admin/articles/:article_id/sources -> {sources: [...]}  (LIMIT 50, content elided to a preview + length)
DELETE /api/v1/admin/articles/:article_id/sources/:source_id -> soft-remove
```

- POST validates the article exists (`store.get_article`), bounds all fields
  (400 on oversize, no silent truncation), stamps `added_by` from the
  *verified* admin wallet (never the raw header).
- No Celery dispatch here — attach is storage-only (section 4); the existing
  recompose endpoints are reused untouched except that their worker tasks now
  load sources.
- msgspec Structs for request/response models, per the codebase convention.

## 6. Admin UI (frontend)

The review queue lives in
`frontend/src/routes/admin/tabs/ClassifierTab.svelte` (per-item detail pane
with the existing Recompose button); live articles in `ArticlesTab.svelte`
(also with a Recompose action). The affordance, v1 on the ClassifierTab
detail pane, is deliberately minimal:

- A collapsible **"Owner sources (N)"** section under the draft preview:
  list of attached sources (label, added date, first ~200 chars, Remove),
  plus a label input + textarea + "Attach" button. Shown only when the
  review's metadata carries an `article_id`.
- A small count badge next to the Recompose button ("Recompose · 1 source
  attached") so the owner can see the recompose will be enriched before
  clicking.
- Frontend rules apply as usual: shared async-state helpers (no hand-rolled
  loading/try/catch), AbortController/sequence guard on the fetches, any
  rendered source preview is plain text (no `{@html}` at all — safer than
  sanitizing), and the handful of new strings land in **all 9 locale files**
  with identical key sets.

Phase 2 adds the same panel to ArticlesTab's expanded article row (the
workers-side wiring for live articles ships in Phase 1 regardless, so an
API-attached source on a live article already works — the tab UI is just the
convenient way in).

## 7. Phased build plan

**Phase 1 — the AlgoSprout case, end to end** (attach text to a specific
article, recompose it, evidence grounds the draft):

1. Migration 107 + manifest entry; `AdminSourceStmts` in
   `shared/algorand_shared/admin_source_statements.py`.
2. `workers/`: `admin_source_store.py` (load-active-by-article, bounded),
   `compose_scrape_article(admin_sources=...)` prompt block + trace seed,
   wiring in `recompose_review` and `recompose_published` (load before
   compose, fail-closed on read error per section 3), new config settings in
   `core/config.py`.
3. `backend/`: the three admin endpoints + msgspec schemas.
4. `frontend/`: ClassifierTab panel + badge; 9 locale files.
5. Tests (no-network, fake Cassandra at the seam): store round-trip +
   bounds; prompt-block and trace-seed presence given `admin_sources`;
   chunking at the 16k row cap; `recompose_review` re-enqueue still intact
   when sources are attached and compose fails; the fail-closed read-error
   path; entailment regression — an article figure present only in an
   `admin_supplied_source` trace entry grounds. (Both recompose tasks are on
   CLAUDE.md's "hot paths with no regression tests" list — these are the
   exact-path tests section 6 requires.)

**Phase 2 — convenience:** `kind='url'` fetched at recompose time via the
existing scraper helper; ArticlesTab panel; attach-time content preview.

**Phase 3 — only if real usage asks for it:** sources on *fresh* composes
(pre-attaching evidence to a service before its first article — needs a
service-level key, a different feature); reader-facing "exclusive"
attribution styling; folding an `attribution_url` into the Stage-2
auto-appended `## Sources` block. None of these are needed for the stated
ask, and each has its own design questions — flag and stop, don't grow
Phase 1 into them.

## 8. Invariants check (CLAUDE.md section 2, explicitly)

- **#1 finished compose never discarded / #2 store before mark / #4 no
  ungrounded fallback**: untouched — the feature adds inputs before compose
  and changes nothing after it; both recompose paths' existing
  failure/apply ordering is reused as-is.
- **#3 every gate runs on every provider**: no gate is bypassed or
  conditioned; the gate simply sees one more (honestly-labeled) trace entry.
- **#8 empty is not "none found"**: the source-load seam distinguishes
  "no rows" (proceed) from "read error" (fail closed, explicit status).
- **Completeness audit integrity**: `admin_supplied_source` never
  impersonates a real tool name, so "did the mandatory check run" stays a
  true statement about what ran.

## 9. Assumptions made

- "Selected artifact" means an existing article (held or live), not a
  publish-queue row or a service — both recompose entry points are
  article-anchored, and the stated example is.
- v1 deliberately does not let owner sources flip any gate verdict; the
  owner's Approve click on the recomposed draft is the override (section 0).
  If, after real use, the owner wants "my attached evidence should satisfy
  `company_backing`", that's a one-line rules change
  (`required_any=(..., "admin_supplied_source")`) — but it should be an
  explicit owner decision, because it converts a "verification ran" audit
  into a "someone vouched" audit, and it only matters on the fresh-publish
  auto-approve path anyway.
- One owner-operator: no per-source review workflow, no multi-admin
  attribution UI beyond storing `added_by`.
