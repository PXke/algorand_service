<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let items: Array<Record<string, unknown>> = $state([])
  let title = $state('')
  let body = $state('')
  let keywords = $state('')
  let refreshDays = $state(0)
  let loading = $state(true)
  let submitting = $state(false)
  let assigningId = $state<string | null>(null)
  let error = $state<string | null>(null)

  async function load() {
    loading = true
    error = null
    try {
      const res = await admin.listBriefs()
      items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function create() {
    if (!title.trim()) return
    submitting = true
    error = null
    try {
      await admin.createBrief({
        title: title.trim(),
        body_markdown: body.trim(),
        keywords: keywords
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
          .join(', '),
        status: 'active',
        refresh_every_days: refreshDays,
      })
      title = ''
      body = ''
      keywords = ''
      refreshDays = 0
      onmessage?.('Brief assigned to the writer agent')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      submitting = false
    }
  }

  async function assign(id: string) {
    assigningId = id
    try {
      await admin.assignBriefNow(id)
      onmessage?.('Queued for the writer agent')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      assigningId = null
    }
  }

  function formatLastRun(epoch: unknown): string {
    const n = Number(epoch)
    if (!n) return 'not run yet'
    return new Date(n * 1000).toLocaleString()
  }

  function keywordsLabel(b: Record<string, unknown>): string {
    const kw = b.keywords
    if (Array.isArray(kw)) return kw.map(String).join(', ')
    return String(kw ?? '')
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <h2>Writer briefs</h2>
    <button class="btn" type="button" onclick={() => load()}>Refresh</button>
  </div>

  <form
    class="panel stack form-panel"
    onsubmit={(e) => {
      e.preventDefault()
      void create()
    }}
  >
    <h3>Assign the writer a topic</h3>
    <p class="intro">
      Writes an original article on this topic now. Set a refresh cadence to keep it updated in
      place instead of writing a new one each time.
    </p>
    <label class="field">
      <span>Topic / working title</span>
      <input bind:value={title} required placeholder="State of Algorand DeFi Q3" />
    </label>
    <label class="field">
      <span>Focus keywords</span>
      <input bind:value={keywords} placeholder="comma-separated" />
    </label>
    <label class="field">
      <span>Editorial pointers (markdown)</span>
      <textarea rows="5" bind:value={body} placeholder="Angle, sources to cite, tone…"></textarea>
    </label>
    <label class="field narrow">
      <span>Refresh every N days</span>
      <input type="number" min="0" bind:value={refreshDays} />
      <span class="hint">0 = one-off, no recurring refresh</span>
    </label>
    <div class="form-actions">
      <button class="btn btn-primary" type="submit" disabled={submitting}>
        {submitting ? 'Assigning…' : 'Assign to writer'}
      </button>
    </div>
  </form>

  <h3 class="section-title">Assigned briefs</h3>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !items.length}
    <p class="muted">Nothing assigned yet.</p>
  {:else}
    {#each items as b (b.brief_id)}
      {@const briefId = String(b.brief_id ?? '')}
      {@const refresh = Number(b.refresh_every_days ?? 0)}
      {@const linked = String(b.linked_article_id ?? '')}
      {@const cadence = refresh > 0 ? `Refreshes every ${refresh} d` : 'One-off'}
      {@const kw = keywordsLabel(b)}
      <article class="panel brief-card">
        <div class="brief-main">
          <strong>{String(b.title ?? '')}</strong>
          <div class="brief-meta">
            {#if kw}<span>{kw}</span>{/if}
            <span>{cadence}</span>
            {#if linked}
              <span class="mono">article: {linked.slice(0, 12)}…</span>
              <span>last run: {formatLastRun(b.last_run_at_epoch)}</span>
            {:else}
              <span>no article yet</span>
            {/if}
          </div>
        </div>
        <div class="brief-actions">
          <span class="status-chip">{String(b.status ?? '—')}</span>
          <button
            class="btn btn-sm"
            type="button"
            disabled={assigningId === briefId}
            onclick={() => assign(briefId)}
          >
            {#if assigningId === briefId}
              Queuing…
            {:else if linked}
              Refresh now
            {:else}
              Write now
            {/if}
          </button>
        </div>
      </article>
    {/each}
  {/if}
</div>

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  h2,
  h3 {
    margin: 0;
  }
  h2 {
    font-size: 1.25rem;
  }
  .section-title {
    font-size: 1rem;
    margin-top: 4px;
  }
  .intro {
    margin: 0;
    font-size: 0.88rem;
    color: var(--muted);
    line-height: 1.45;
  }
  .form-panel {
    gap: 14px;
  }
  .hint {
    font-size: 0.82rem;
    color: var(--subtle);
  }
  .field.narrow input {
    max-width: 120px;
  }
  .form-actions {
    display: flex;
    justify-content: flex-end;
  }
  .brief-card {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    flex-wrap: wrap;
  }
  .brief-main {
    flex: 1;
    min-width: 200px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .brief-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 12px;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .brief-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  .status-chip {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    padding: 4px 8px;
    border-radius: 6px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--muted);
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .btn-sm {
    padding: 6px 12px;
    font-size: 12.5px;
    white-space: nowrap;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
