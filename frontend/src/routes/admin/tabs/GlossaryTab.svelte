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
  let loading = $state(true)
  let error = $state<string | null>(null)
  let filterStatus = $state<string | null>(null)
  let editing = $state(false)

  let slug = $state('')
  let term = $state('')
  let definition = $state('')
  let aliasesText = $state('')
  let status = $state<'draft' | 'published'>('draft')
  let saving = $state(false)

  const statusCounts = $derived.by(() => {
    const counts: Record<string, number> = {}
    for (const t of items) {
      const s = String(t.status ?? 'draft')
      counts[s] = (counts[s] ?? 0) + 1
    }
    return counts
  })

  const visible = $derived(
    filterStatus ? items.filter((t) => String(t.status ?? 'draft') === filterStatus) : items,
  )

  function slugify(text: string): string {
    return text
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
  }

  function resetForm() {
    slug = ''
    term = ''
    definition = ''
    aliasesText = ''
    status = 'draft'
    editing = false
  }

  function populateForm(t: Record<string, unknown>) {
    slug = String(t.slug ?? '')
    term = String(t.term ?? '')
    definition = String(t.definition ?? '')
    aliasesText = Array.isArray(t.aliases) ? (t.aliases as string[]).join(', ') : ''
    status = t.status === 'published' ? 'published' : 'draft'
    editing = true
  }

  async function load() {
    loading = true
    error = null
    try {
      const res = await admin.listGlossary()
      items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function save() {
    const s = (editing ? slug : slugify(term)).trim()
    if (!s || !term.trim() || !definition.trim()) return
    saving = true
    try {
      await admin.upsertGlossaryTerm({
        slug: s,
        term: term.trim(),
        definition: definition.trim(),
        aliases: aliasesText
          .split(',')
          .map((a) => a.trim())
          .filter(Boolean),
        status,
      })
      resetForm()
      onmessage?.('Glossary entry saved')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      saving = false
    }
  }

  async function remove(s: string) {
    if (!confirm(`Delete glossary entry "${s}"? This cannot be undone.`)) return
    try {
      await admin.deleteGlossaryTerm(s)
      if (slug === s) resetForm()
      onmessage?.('Glossary entry deleted')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <div>
      <h2>Glossary</h2>
      <p class="intro">
        Term definitions linked to from article bodies. Only "published" entries are public and
        linkable — drafts (including anything the writer suggests) stay invisible until you edit
        and publish them.
      </p>
    </div>
    <div class="toolbar-actions">
      <button class="btn" type="button" onclick={() => load()}>Refresh</button>
    </div>
  </div>

  <div class="filters">
    <button
      type="button"
      class="chip"
      class:active={filterStatus === null}
      onclick={() => (filterStatus = null)}
    >
      All ({items.length})
    </button>
    {#each ['published', 'draft'] as s}
      {#if (statusCounts[s] ?? 0) > 0}
        <button
          type="button"
          class="chip status-{s}"
          class:active={filterStatus === s}
          onclick={() => (filterStatus = filterStatus === s ? null : s)}
        >
          {s} ({statusCounts[s]})
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
    <h3>{editing ? 'Edit entry' : 'Add entry'}</h3>
    <div class="form-grid">
      <label class="field">
        <span>Term</span>
        <input bind:value={term} required placeholder="Liquid staking" />
      </label>
      <label class="field">
        <span>Slug</span>
        <input value={editing ? slug : slugify(term)} disabled placeholder="auto-generated" />
      </label>
      <label class="field full">
        <span>Definition</span>
        <textarea bind:value={definition} required rows="3" placeholder="Plain-language explanation"
        ></textarea>
      </label>
      <label class="field full">
        <span>Aliases (comma-separated, also auto-link to this entry)</span>
        <input bind:value={aliasesText} placeholder="liquid governance, mALGO staking" />
      </label>
      <label class="field">
        <span>Status</span>
        <select bind:value={status}>
          <option value="draft">Draft (not public)</option>
          <option value="published">Published (public + linkable)</option>
        </select>
      </label>
    </div>
    <div class="form-actions">
      {#if editing}
        <button class="btn" type="button" onclick={resetForm}>Cancel edit</button>
      {/if}
      <button class="btn btn-primary" type="submit" disabled={saving}>
        {saving ? 'Saving…' : editing ? 'Save changes' : 'Save entry'}
      </button>
    </div>
  </form>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !visible.length}
    <div class="empty panel">
      <p><strong>No glossary entries match this filter.</strong></p>
      <p class="subtle">Add one above.</p>
    </div>
  {:else}
    {#each visible as t (t.slug)}
      {@const s = String(t.slug ?? '')}
      {@const isPublished = t.status === 'published'}
      {@const aliases = Array.isArray(t.aliases) ? (t.aliases as string[]) : []}
      <article class="panel card">
        <div class="card-head">
          <div class="card-title">
            <strong>{String(t.term ?? s)}</strong>
            <span class="status-badge" class:off={!isPublished}>
              {isPublished ? 'published' : 'draft'}
            </span>
            {#if t.created_by === 'model' || String(t.created_by ?? '').startsWith('writer:')}
              <span class="suggested-badge">suggested by writer</span>
            {/if}
          </div>
          <div class="card-actions">
            <button class="btn btn-sm" type="button" onclick={() => populateForm(t)}>Edit</button>
            {#if isPublished}
              <a class="btn btn-sm btn-outlined" href="/glossary/{s}" target="_blank">View</a>
            {/if}
            <button class="btn btn-sm danger-text" type="button" onclick={() => remove(s)}
              >Delete</button
            >
          </div>
        </div>
        <p class="definition">{String(t.definition ?? '')}</p>
        {#if aliases.length}
          <p class="subtle aliases">Aliases: {aliases.join(', ')}</p>
        {/if}
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
    max-width: 60ch;
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
  .form-grid textarea {
    font: inherit;
    resize: vertical;
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
    margin-bottom: 8px;
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
  .suggested-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--primary) 12%, var(--panel));
    color: var(--primary);
  }
  .definition {
    margin: 0;
    font-size: 0.92rem;
    line-height: 1.5;
  }
  .aliases {
    margin: 8px 0 0;
    font-size: 0.82rem;
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
