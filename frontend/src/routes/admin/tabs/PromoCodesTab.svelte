<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'
  import { x402Api, type X402CatalogRoute } from '../../../lib/api/x402'
  import { LatestOnly } from '../../../lib/asyncGuard'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let items: Array<Record<string, unknown>> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let filterActive = $state<boolean | null>(null)

  // Paid routes from the live catalog (GET /api/v1/x402), refreshed whenever
  // this tab mounts -- the create form's resource dropdown is built from
  // this list rather than hardcoded, so a new paid route shows up here
  // automatically, and a typo can never scope a code to a resource id that
  // does not exist.
  let resources: X402CatalogRoute[] = $state([])
  let resourcesLoading = $state(true)
  let resourcesError = $state<string | null>(null)

  let code = $state('')
  let resource = $state('')
  let startingCount = $state(10)
  let expiresOn = $state('') // yyyy-mm-dd, blank = never expires
  let saving = $state(false)

  const inflightCodes = new LatestOnly()
  const inflightResources = new LatestOnly()

  const activeCount = $derived(items.filter((i) => i.active === true).length)
  const inactiveCount = $derived(items.length - activeCount)
  const visible = $derived(
    filterActive === null ? items : items.filter((i) => (i.active === true) === filterActive),
  )

  async function loadCodes() {
    const { signal, stale } = inflightCodes.next()
    loading = true
    error = null
    try {
      const res = await admin.listPromoCodes(signal)
      if (stale()) return
      items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      error = e instanceof Error ? e.message : String(e)
    } finally {
      if (!stale()) loading = false
    }
  }

  async function loadResources() {
    const { signal, stale } = inflightResources.next()
    resourcesLoading = true
    resourcesError = null
    try {
      const catalog = await x402Api.catalog({ signal })
      if (stale()) return
      const byResource = new Map<string, X402CatalogRoute>()
      for (const r of catalog.routes ?? []) {
        if (r.paid && r.resource) byResource.set(r.resource, r)
      }
      resources = [...byResource.values()]
      if (!resource && resources.length) resource = resources[0]!.resource!
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      resourcesError = e instanceof Error ? e.message : String(e)
    } finally {
      if (!stale()) resourcesLoading = false
    }
  }

  function resourceLabel(r: X402CatalogRoute): string {
    const price = r.price_usd != null ? ` · $${r.price_usd}` : ''
    return `${r.method} ${r.path}${price} — ${r.resource}`
  }

  function resetForm() {
    code = ''
    startingCount = 10
    expiresOn = ''
  }

  function expiresAtEpoch(): number {
    if (!expiresOn) return 0
    const parsed = Date.parse(`${expiresOn}T00:00:00Z`)
    return Number.isNaN(parsed) ? 0 : Math.floor(parsed / 1000)
  }

  function formatEpoch(raw: unknown): string {
    const n = Number(raw)
    if (!n) return '—'
    return new Date(n * 1000).toLocaleString()
  }

  function formatExpiry(raw: unknown): string {
    const n = Number(raw)
    if (!n) return 'Never'
    return new Date(n * 1000).toLocaleDateString()
  }

  async function save() {
    const c = code.trim()
    if (!c || !resource || startingCount < 1) return
    saving = true
    error = null
    try {
      await admin.createPromoCode({
        code: c,
        resource,
        starting_count: Math.floor(startingCount),
        expires_at_epoch: expiresAtEpoch(),
      })
      resetForm()
      onmessage?.('Promo code created')
      await loadCodes()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      saving = false
    }
  }

  async function deactivate(c: string) {
    if (!confirm(`Deactivate promo code "${c}"? Remaining uses are lost immediately.`)) return
    try {
      await admin.deletePromoCode(c)
      onmessage?.('Promo code deactivated')
      await loadCodes()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  $effect(() => {
    void loadCodes()
  })

  $effect(() => {
    void loadResources()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <div>
      <h2>Promo Codes</h2>
      <p class="intro">
        Admin-issued codes that bypass payment for a bounded number of uses on one x402 resource.
        A redemption is never a real payment — nothing is settled or written to the ledger.
      </p>
    </div>
    <div class="toolbar-actions">
      <button class="btn" type="button" onclick={() => loadCodes()}>Refresh</button>
    </div>
  </div>

  <div class="filters">
    <button
      type="button"
      class="chip"
      class:active={filterActive === null}
      onclick={() => (filterActive = null)}
    >
      All ({items.length})
    </button>
    {#if activeCount > 0}
      <button
        type="button"
        class="chip"
        class:active={filterActive === true}
        onclick={() => (filterActive = filterActive === true ? null : true)}
      >
        active ({activeCount})
      </button>
    {/if}
    {#if inactiveCount > 0}
      <button
        type="button"
        class="chip"
        class:active={filterActive === false}
        onclick={() => (filterActive = filterActive === false ? null : false)}
      >
        inactive ({inactiveCount})
      </button>
    {/if}
  </div>

  <form
    class="panel stack form-panel"
    onsubmit={(e) => {
      e.preventDefault()
      void save()
    }}
  >
    <h3>Issue a code</h3>
    <div class="form-grid">
      <label class="field">
        <span>Code</span>
        <input bind:value={code} required maxlength="64" placeholder="LAUNCH2026" />
      </label>
      <label class="field">
        <span>Resource</span>
        {#if resourcesLoading && !resources.length}
          <select disabled><option>Loading…</option></select>
        {:else if resourcesError}
          <select disabled><option>Could not load resources</option></select>
        {:else if !resources.length}
          <select disabled><option>No paid resources found</option></select>
        {:else}
          <select bind:value={resource} required>
            {#each resources as r (r.resource)}
              <option value={r.resource}>{resourceLabel(r)}</option>
            {/each}
          </select>
        {/if}
      </label>
      <label class="field">
        <span>Starting count</span>
        <input type="number" bind:value={startingCount} min="1" step="1" required />
      </label>
      <label class="field">
        <span>Expires (blank = never)</span>
        <input type="date" bind:value={expiresOn} />
      </label>
    </div>
    {#if resourcesError}
      <p class="err">Resource list unavailable: {resourcesError}</p>
    {/if}
    <div class="form-actions">
      <button
        class="btn btn-primary"
        type="submit"
        disabled={saving || !resources.length}
      >
        {saving ? 'Creating…' : 'Create code'}
      </button>
    </div>
  </form>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !visible.length}
    <div class="empty panel">
      <p><strong>No promo codes match this filter.</strong></p>
      <p class="subtle">Issue one above.</p>
    </div>
  {:else}
    {#each visible as it (it.code)}
      {@const c = String(it.code ?? '')}
      {@const isActive = it.active === true}
      {@const remaining = it.remaining}
      <article class="panel card">
        <div class="card-head">
          <div class="card-title">
            <strong class="mono">{c}</strong>
            <span class="status-badge" class:off={!isActive}>
              {isActive ? 'active' : 'inactive'}
            </span>
          </div>
          <div class="card-actions">
            {#if isActive}
              <button class="btn btn-sm danger-text" type="button" onclick={() => deactivate(c)}>
                Deactivate
              </button>
            {/if}
          </div>
        </div>
        <dl class="meta">
          <div><dt>Resource</dt><dd class="mono">{String(it.resource ?? '')}</dd></div>
          <div>
            <dt>Remaining</dt>
            <dd>
              {remaining == null ? 'unknown (Redis unreachable)' : `${remaining} / ${it.starting_count}`}
            </dd>
          </div>
          <div><dt>Expires</dt><dd>{formatExpiry(it.expires_at_epoch)}</dd></div>
          <div><dt>Created</dt><dd>{formatEpoch(it.created_at_epoch)}</dd></div>
        </dl>
      </article>
    {/each}
  {/if}
</div>

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
    max-width: 62ch;
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
  @media (max-width: 560px) {
    .form-grid {
      grid-template-columns: 1fr;
    }
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
    grid-template-columns: 110px 1fr;
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
    word-break: break-word;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.85em;
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
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
