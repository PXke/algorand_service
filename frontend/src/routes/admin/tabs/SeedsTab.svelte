<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  const MATCH_KINDS = ['contains', 'prefix', 'exact', 'regex', 'domain'] as const

  let items: Array<Record<string, unknown>> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let filterKind = $state<string | null>(null)
  let editing = $state(false)

  let serviceId = $state('')
  let displayName = $state('')
  let scrapeUrl = $state('')
  let matchKind = $state('contains')
  let matchValue = $state('')
  let enabled = $state(true)
  let saving = $state(false)

  let mergeOpen = $state(false)
  let mergeTarget = $state('')
  let mergeFold = $state<Set<string>>(new Set())
  let merging = $state(false)
  let mergeError = $state<string | null>(null)

  function inferKind(s: Record<string, unknown>): string {
    const fromField = String(s.source_kind ?? s.kind ?? '').toLowerCase()
    if (fromField && fromField !== 'unknown') return fromField
    const sid = String(s.service_id ?? '').toLowerCase()
    if (sid.includes('discord')) return 'discord'
    if (sid.includes('reddit')) return 'reddit'
    const url = String(s.scrape_url ?? '').toLowerCase()
    if (url.startsWith('discord:')) return 'discord'
    if (url.startsWith('reddit:')) return 'reddit'
    if (!url) return 'chain_only'
    return 'web'
  }

  const seeds = $derived(items.filter((x) => x.origin !== 'domain'))

  const kindCounts = $derived.by(() => {
    const counts: Record<string, number> = {}
    for (const s of seeds) {
      const k = inferKind(s)
      counts[k] = (counts[k] ?? 0) + 1
    }
    return counts
  })

  const visible = $derived(
    filterKind ? seeds.filter((s) => inferKind(s) === filterKind) : seeds,
  )

  const enabledSeeds = $derived(
    seeds.filter((s) => s.enabled === true && String(s.service_id ?? '').length > 0),
  )

  function matchRuleLabel(kind: string, value: string): string {
    if (!kind && !value) return '—'
    if (!value) return kind
    return `${kind} · ${value}`
  }

  function resetForm() {
    serviceId = ''
    displayName = ''
    scrapeUrl = ''
    matchKind = 'contains'
    matchValue = ''
    enabled = true
    editing = false
  }

  function populateForm(s: Record<string, unknown>) {
    serviceId = String(s.service_id ?? '')
    displayName = String(s.display_name ?? '')
    scrapeUrl = String(s.scrape_url ?? '')
    matchKind = String(s.match_kind ?? 'contains')
    matchValue = String(s.match_value ?? '')
    enabled = s.enabled !== false
    editing = true
  }

  async function load() {
    loading = true
    error = null
    try {
      const res = await admin.listSources()
      const all = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
      items = all
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function save() {
    const id = serviceId.trim()
    if (!id || !scrapeUrl.trim()) return
    saving = true
    try {
      await admin.upsertSource({
        service_id: id,
        display_name: displayName.trim() || id,
        scrape_url: scrapeUrl.trim(),
        match_kind: matchKind,
        match_value: matchValue.trim(),
        enabled,
      })
      resetForm()
      onmessage?.('Source saved')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      saving = false
    }
  }

  async function remove(id: string) {
    if (!confirm(`Delete source "${id}"? This cannot be undone.`)) return
    try {
      await admin.deleteSource(id)
      if (serviceId === id) resetForm()
      onmessage?.('Source deleted')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function doMerge() {
    if (!mergeTarget || mergeFold.size === 0) {
      mergeError = 'Pick a target and at least one source to fold in.'
      return
    }
    merging = true
    mergeError = null
    try {
      const folded = [...mergeFold]
      const target = mergeTarget
      await admin.mergeSources({
        target_service_id: target,
        fold_service_ids: folded,
      })
      mergeOpen = false
      mergeTarget = ''
      mergeFold = new Set()
      onmessage?.(`Merged ${folded.length} source(s) into ${target}`)
      await load()
    } catch (e) {
      mergeError = e instanceof Error ? e.message : String(e)
    } finally {
      merging = false
    }
  }

  function toggleFold(id: string, checked: boolean) {
    const next = new Set(mergeFold)
    if (checked) next.add(id)
    else next.delete(id)
    mergeFold = next
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <div>
      <h2>Seeds</h2>
      <p class="intro">
        Curated scrape sources — Discord channels, Reddit subs, and web feeds the crawler polls.
      </p>
    </div>
    <div class="toolbar-actions">
      {#if seeds.length >= 2}
        <button class="btn btn-outlined" type="button" onclick={() => (mergeOpen = true)}>Merge</button>
      {/if}
      <button class="btn" type="button" onclick={() => load()}>Refresh</button>
    </div>
  </div>

  <div class="filters">
    <button type="button" class="chip" class:active={filterKind === null} onclick={() => (filterKind = null)}>
      All ({seeds.length})
    </button>
    {#each ['discord', 'reddit', 'web'] as kind}
      {#if (kindCounts[kind] ?? 0) > 0}
        <button
          type="button"
          class="chip kind-{kind}"
          class:active={filterKind === kind}
          onclick={() => (filterKind = filterKind === kind ? null : kind)}
        >
          {kind} ({kindCounts[kind]})
        </button>
      {/if}
    {/each}
  </div>

  <form
    class="panel stack form-panel"
    onsubmit={(e) => {
      e.preventDefault()
      void save()
    }}
  >
    <h3>{editing ? 'Edit source' : 'Add / upsert source'}</h3>
    <div class="form-grid">
      <label class="field">
        <span>Service ID</span>
        <input bind:value={serviceId} required disabled={editing} placeholder="my-discord-channel" />
      </label>
      <label class="field">
        <span>Display name</span>
        <input bind:value={displayName} placeholder="Human-readable label" />
      </label>
      <label class="field full">
        <span>Scrape URL</span>
        <input bind:value={scrapeUrl} required placeholder="discord:… / reddit:… / https://…" />
      </label>
      <label class="field">
        <span>Match kind</span>
        <select bind:value={matchKind}>
          {#each MATCH_KINDS as mk}
            <option value={mk}>{mk}</option>
          {/each}
        </select>
      </label>
      <label class="field">
        <span>Match value</span>
        <input bind:value={matchValue} placeholder="domain or pattern" />
      </label>
    </div>
    <label class="check-row">
      <input type="checkbox" bind:checked={enabled} />
      <span>Enabled — crawler will poll this source</span>
    </label>
    <div class="form-actions">
      {#if editing}
        <button class="btn" type="button" onclick={resetForm}>Cancel edit</button>
      {/if}
      <button class="btn btn-primary" type="submit" disabled={saving}>
        {saving ? 'Saving…' : editing ? 'Save changes' : 'Save source'}
      </button>
    </div>
  </form>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !visible.length}
    <div class="empty panel">
      <p><strong>No sources match this filter.</strong></p>
      <p class="subtle">Add a seed above or clear the filter.</p>
    </div>
  {:else}
    {#each visible as s (s.service_id)}
      {@const id = String(s.service_id ?? '')}
      {@const kind = inferKind(s)}
      {@const isEnabled = s.enabled !== false}
      <article class="panel card">
        <div class="card-head">
          <div class="card-title">
            <strong>{String(s.display_name ?? id)}</strong>
            <span class="kind-badge kind-{kind}">{kind}</span>
            <span class="status-badge" class:off={!isEnabled}>{isEnabled ? 'enabled' : 'disabled'}</span>
          </div>
          <div class="card-actions">
            <button class="btn btn-sm" type="button" onclick={() => populateForm(s)}>Edit</button>
            <a class="btn btn-sm btn-outlined" href="/news?service_id={encodeURIComponent(id)}">Articles</a>
            <button class="btn btn-sm danger-text" type="button" onclick={() => remove(id)}>Delete</button>
          </div>
        </div>
        <dl class="meta">
          <div><dt>Service ID</dt><dd class="mono">{id}</dd></div>
          {#if s.scrape_url}
            <div><dt>Scrape URL</dt><dd class="mono truncate">{String(s.scrape_url)}</dd></div>
          {/if}
          <div>
            <dt>Match rule</dt>
            <dd>{matchRuleLabel(String(s.match_kind ?? ''), String(s.match_value ?? ''))}</dd>
          </div>
        </dl>
      </article>
    {/each}
  {/if}
</div>

{#if mergeOpen}
  <div class="overlay" role="dialog" aria-modal="true" aria-labelledby="merge-title">
    <div class="dialog panel stack">
      <h3 id="merge-title">Merge sources</h3>
      <p class="subtle">
        Fold several services into one target — articles and crawl history move to the survivor.
        Pick the canonical service, then check sources to absorb.
      </p>
      <label class="field">
        <span>Target (survivor)</span>
        <select
          bind:value={mergeTarget}
          onchange={() => {
            const next = new Set(mergeFold)
            next.delete(mergeTarget)
            mergeFold = next
          }}
        >
          <option value="">— select —</option>
          {#each enabledSeeds as s}
            <option value={String(s.service_id)}>{String(s.display_name ?? s.service_id)}</option>
          {/each}
        </select>
      </label>
      <p class="label">Fold into target</p>
      <div class="fold-list">
        {#each enabledSeeds.filter((s) => String(s.service_id) !== mergeTarget) as s}
          {@const fid = String(s.service_id)}
          <label class="check-row">
            <input
              type="checkbox"
              checked={mergeFold.has(fid)}
              onchange={(e) => toggleFold(fid, (e.currentTarget as HTMLInputElement).checked)}
            />
            <span>
              <strong>{String(s.display_name ?? fid)}</strong>
              <span class="subtle mono">{fid}</span>
            </span>
          </label>
        {/each}
      </div>
      {#if mergeError}<p class="err">{mergeError}</p>{/if}
      <div class="form-actions">
        <button class="btn" type="button" onclick={() => (mergeOpen = false)}>Cancel</button>
        <button class="btn btn-primary" type="button" disabled={merging} onclick={() => doMerge()}>
          {merging ? 'Merging…' : 'Merge'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
  }
  .toolbar-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
  h2,
  h3 {
    margin: 0;
  }
  h2 {
    font-size: 1.25rem;
  }
  .intro {
    margin: 4px 0 0;
    font-size: 0.88rem;
    color: var(--muted);
    max-width: 52ch;
    line-height: 1.45;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip {
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    font-size: 12.5px;
    font-weight: 600;
    padding: 6px 12px;
    border-radius: 999px;
  }
  .chip.active {
    background: var(--accent-soft);
    color: var(--primary);
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  }
  .form-panel h3 {
    font-size: 1rem;
  }
  .form-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  .form-grid .full {
    grid-column: 1 / -1;
  }
  @media (max-width: 560px) {
    .form-grid {
      grid-template-columns: 1fr;
    }
  }
  .check-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    font-size: 0.92rem;
    cursor: pointer;
  }
  .check-row input {
    margin-top: 3px;
  }
  .check-row span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .form-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .card-title {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .card-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .kind-badge {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    padding: 2px 8px;
    border-radius: 6px;
  }
  .kind-discord {
    background: color-mix(in srgb, #5865f2 15%, var(--panel));
    color: #5865f2;
  }
  .kind-reddit {
    background: color-mix(in srgb, #ff4500 12%, var(--panel));
    color: #c2410c;
  }
  .kind-web {
    background: color-mix(in srgb, var(--gain) 12%, var(--panel));
    color: var(--gain);
  }
  .status-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--gain) 12%, var(--panel));
    color: var(--gain);
  }
  .status-badge.off {
    background: var(--surface);
    color: var(--muted);
    border: 1px solid var(--border);
  }
  .meta {
    margin: 0;
    display: grid;
    gap: 8px;
  }
  .meta div {
    display: grid;
    grid-template-columns: 100px 1fr;
    gap: 8px;
    font-size: 0.88rem;
  }
  .meta dt {
    margin: 0;
    color: var(--subtle);
    font-weight: 600;
  }
  .meta dd {
    margin: 0;
    word-break: break-all;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.85em;
  }
  .truncate {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .btn-sm {
    padding: 6px 10px;
    font-size: 12.5px;
  }
  .danger-text {
    color: var(--danger);
  }
  .empty {
    text-align: center;
    padding: 24px;
  }
  .empty p {
    margin: 0 0 6px;
  }
  .label {
    margin: 0;
    font-size: 0.82rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: var(--subtle);
  }
  .fold-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 220px;
    overflow: auto;
  }
  .overlay {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--on-surface) 45%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 200;
  }
  .dialog {
    width: min(480px, 100%);
    max-height: 90vh;
    overflow: auto;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
