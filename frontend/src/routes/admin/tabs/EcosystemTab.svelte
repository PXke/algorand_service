<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'
  import { LatestOnly } from '../../../lib/asyncGuard'
  import { ECOSYSTEM_CATEGORIES, ECOSYSTEM_REJECT_REASONS } from '../../../lib/api/ecosystem'
  import { isHttp } from '../../../lib/sanitizeHtml'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  const STATUSES = ['pending', 'approved', 'rejected'] as const

  let filterStatus = $state<(typeof STATUSES)[number]>('pending')
  let items: Array<Record<string, unknown>> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let seeding = $state(false)

  // Expanded-row editing state, keyed by slug -- only one row's detail is
  // fetched/held at a time in practice, but a plain map avoids any
  // cross-row bleed if more than one is opened.
  let expanded = $state<string | null>(null)
  let detail: Record<string, unknown> | null = $state(null)
  let detailLoading = $state(false)
  let editName = $state('')
  let editDescription = $state('')
  let editCategory = $state('other')
  let editTagsText = $state('')
  let rejectReason = $state<(typeof ECOSYSTEM_REJECT_REASONS)[number]>('other')
  let busy = $state<Set<string>>(new Set())
  let draftHint = $state<string | null>(null)

  // "Suggest a change" queue (owner ask, 2026-09-08) -- a separate, smaller
  // list with its own status filter, independent of the project queue above.
  const REQUEST_STATUSES = ['pending', 'resolved', 'dismissed'] as const
  let requestFilterStatus = $state<(typeof REQUEST_STATUSES)[number]>('pending')
  let requests: Array<Record<string, unknown>> = $state([])
  let requestsLoading = $state(true)
  let requestsError = $state<string | null>(null)
  let requestsBusy = $state<Set<string>>(new Set())
  const requestsInflight = new LatestOnly()

  async function loadRequests() {
    const { signal, stale } = requestsInflight.next()
    requestsLoading = true
    requestsError = null
    try {
      const res = await admin.listEcosystemRequests(requestFilterStatus, signal)
      if (stale()) return
      requests = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      requestsError = e instanceof Error ? e.message : String(e)
    } finally {
      if (!stale()) requestsLoading = false
    }
  }

  function selectRequestStatus(status: (typeof REQUEST_STATUSES)[number]) {
    requestFilterStatus = status
    void loadRequests()
  }

  async function resolveRequest(requestId: string, status: 'resolved' | 'dismissed') {
    requestsBusy = new Set(requestsBusy).add(requestId)
    try {
      await admin.resolveEcosystemRequest(requestId, status)
      onmessage?.(`Request ${status}`)
      await loadRequests()
    } catch (e) {
      requestsError = e instanceof Error ? e.message : String(e)
    } finally {
      const next = new Set(requestsBusy)
      next.delete(requestId)
      requestsBusy = next
    }
  }

  const inflight = new LatestOnly()

  async function load() {
    const { signal, stale } = inflight.next()
    loading = true
    error = null
    try {
      const res = await admin.listEcosystemQueue(filterStatus, signal)
      if (stale()) return
      items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      error = e instanceof Error ? e.message : String(e)
    } finally {
      if (!stale()) loading = false
    }
  }

  function selectStatus(status: (typeof STATUSES)[number]) {
    filterStatus = status
    expanded = null
    void load()
  }

  async function toggleExpand(slug: string) {
    if (expanded === slug) {
      expanded = null
      detail = null
      return
    }
    expanded = slug
    detail = null
    draftHint = null
    detailLoading = true
    try {
      const res = await admin.getEcosystemEntry(slug)
      detail = res
      editName = String(res.name ?? '')
      editDescription = String(res.description ?? '')
      editCategory = String(res.category ?? 'other')
      editTagsText = Array.isArray(res.tags) ? (res.tags as string[]).join(', ') : ''
      rejectReason = 'other'
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      detailLoading = false
    }
  }

  function parseTags(): string[] {
    return editTagsText
      .split(',')
      .map((tg) => tg.trim().toLowerCase())
      .filter(Boolean)
  }

  async function approve(slug: string) {
    busy = new Set(busy).add(slug)
    try {
      await admin.decideEcosystemEntry(slug, {
        decision: 'approve',
        name: editName.trim() || undefined,
        description: editDescription.trim() || undefined,
        category: editCategory,
        tags: parseTags(),
      })
      onmessage?.(`Approved ${editName || slug}`)
      expanded = null
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      const next = new Set(busy)
      next.delete(slug)
      busy = next
    }
  }

  async function reject(slug: string) {
    busy = new Set(busy).add(slug)
    try {
      await admin.decideEcosystemEntry(slug, { decision: 'reject', reason: rejectReason })
      onmessage?.(`Rejected ${slug} (${rejectReason})`)
      expanded = null
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      const next = new Set(busy)
      next.delete(slug)
      busy = next
    }
  }

  async function remove(slug: string) {
    if (!confirm(`Delete registry entry "${slug}"? This cannot be undone.`)) return
    try {
      await admin.deleteEcosystemEntry(slug)
      onmessage?.('Entry deleted')
      expanded = null
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  async function draftBlurb(slug: string) {
    draftHint = 'Requesting a grounded draft from the model — refresh in a moment to see it.'
    try {
      await admin.draftEcosystemBlurb(slug)
    } catch (e) {
      draftHint = e instanceof Error ? e.message : String(e)
    }
  }

  function useDraft() {
    if (detail && typeof detail.draft_description === 'string' && detail.draft_description) {
      editDescription = detail.draft_description
    }
  }

  async function seed() {
    seeding = true
    try {
      const res = await admin.seedEcosystem()
      onmessage?.(`Seeded ${res.created ?? 0} entries (${res.skipped_existing ?? 0} already existed)`)
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      seeding = false
    }
  }

  $effect(() => {
    void load()
  })

  $effect(() => {
    void loadRequests()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <div>
      <h2>Algorand Open Registry</h2>
      <p class="intro">
        Free, human-reviewed ecosystem-project directory (roadmap item 26). Every submission —
        seeded or public — lands here pending. Seeded entries (from the crawler's own
        ecosystem_listed set) are a trusted-source fast lane: usually a one-click approve after a
        quick name/category/description fix.
      </p>
    </div>
    <div class="toolbar-actions">
      <button class="btn" type="button" onclick={() => load()}>Refresh</button>
      <button class="btn btn-outlined" type="button" onclick={seed} disabled={seeding}>
        {seeding ? 'Seeding…' : 'Seed from ecosystem_listed'}
      </button>
    </div>
  </div>

  <div class="filters">
    {#each STATUSES as s (s)}
      <button
        type="button"
        class="chip status-{s}"
        class:active={filterStatus === s}
        onclick={() => selectStatus(s)}
      >
        {s}
      </button>
    {/each}
  </div>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !items.length}
    <div class="empty panel">
      <p><strong>No {filterStatus} entries.</strong></p>
    </div>
  {:else}
    {#each items as item (item.slug)}
      {@const slug = String(item.slug ?? '')}
      {@const isOpen = expanded === slug}
      <article class="panel card">
        <div class="card-head">
          <div class="card-title">
            <strong>{String(item.name ?? slug)}</strong>
            <span class="muted">{String(item.domain ?? '')}</span>
            <span class="category-badge">{String(item.category ?? 'other')}</span>
            {#if item.source === 'seeded'}
              <span class="trusted-badge">trusted source</span>
            {/if}
          </div>
          <div class="card-actions">
            {#if isHttp(String(item.url ?? ''))}
              <a class="btn btn-sm btn-outlined" href={String(item.url)} target="_blank" rel="noopener noreferrer">
                Visit
              </a>
            {:else}
              <span class="btn btn-sm btn-outlined" title="Submitted URL is not a valid http(s) link">
                Visit (invalid url)
              </span>
            {/if}
            <button class="btn btn-sm" type="button" onclick={() => toggleExpand(slug)}>
              {isOpen ? 'Close' : 'Review'}
            </button>
          </div>
        </div>

        {#if isOpen}
          {#if detailLoading}
            <p class="muted">Loading detail…</p>
          {:else if detail}
            <div class="review-panel">
              <div class="form-grid">
                <label class="field full">
                  <span>Name</span>
                  <input bind:value={editName} />
                </label>
                <label class="field full">
                  <span>Description</span>
                  <textarea bind:value={editDescription} rows="3"></textarea>
                </label>
                <label class="field">
                  <span>Category</span>
                  <select bind:value={editCategory}>
                    {#each ECOSYSTEM_CATEGORIES as c (c)}
                      <option value={c}>{c}</option>
                    {/each}
                  </select>
                </label>
                <label class="field">
                  <span>Tags</span>
                  <input bind:value={editTagsText} placeholder="wallet, multisig" />
                </label>
              </div>

              {#if detail.contact}
                <p class="subtle">Private contact (admin-only): {detail.contact}</p>
              {/if}
              {#if detail.category_suggestion}
                <p class="subtle">
                  Submitter suggested a new category: “{detail.category_suggestion}” — pick the
                  closest existing category above, or leave as-is for now.
                </p>
              {/if}
              {#if detail.reachable === false}
                <p class="warn">Currently unreachable (last checked liveness failed).</p>
              {/if}

              <div class="draft-row">
                <button class="btn btn-sm" type="button" onclick={() => draftBlurb(slug)}>
                  Draft a blurb (DeepSeek, grounded, never auto-published)
                </button>
                {#if detail.draft_description}
                  <button class="btn btn-sm btn-outlined" type="button" onclick={useDraft}>
                    Use draft: “{String(detail.draft_description)}”
                  </button>
                {/if}
                {#if draftHint}
                  <span class="subtle">{draftHint}</span>
                {/if}
              </div>

              <div class="decision-row">
                <select bind:value={rejectReason}>
                  {#each ECOSYSTEM_REJECT_REASONS as r (r)}
                    <option value={r}>{r}</option>
                  {/each}
                </select>
                <button
                  class="btn btn-sm danger-text"
                  type="button"
                  onclick={() => reject(slug)}
                  disabled={busy.has(slug)}
                >
                  Reject
                </button>
                <button
                  class="btn btn-sm"
                  type="button"
                  onclick={() => remove(slug)}
                  disabled={busy.has(slug)}
                >
                  Delete
                </button>
                <button
                  class="btn btn-sm btn-primary"
                  type="button"
                  onclick={() => approve(slug)}
                  disabled={busy.has(slug)}
                >
                  {filterStatus === 'approved' ? 'Save changes' : 'Approve'}
                </button>
              </div>
            </div>
          {/if}
        {/if}
      </article>
    {/each}
  {/if}

  <div class="requests-section">
    <h2>Suggest a change</h2>
    <p class="intro">
      Free, anonymous requests against already-listed entries — a change note or a removal ask.
      Act through the ordinary edit/delete controls above, then resolve or dismiss the request here.
    </p>

    <div class="filters">
      {#each REQUEST_STATUSES as s (s)}
        <button
          type="button"
          class="chip status-{s}"
          class:active={requestFilterStatus === s}
          onclick={() => selectRequestStatus(s)}
        >
          {s}
        </button>
      {/each}
    </div>

    {#if requestsLoading}
      <p class="muted">Loading…</p>
    {:else if requestsError}
      <p class="err">{requestsError}</p>
    {:else if !requests.length}
      <div class="empty panel">
        <p><strong>No {requestFilterStatus} requests.</strong></p>
      </div>
    {:else}
      {#each requests as r (r.request_id)}
        {@const requestId = String(r.request_id ?? '')}
        <article class="panel card">
          <div class="card-head">
            <div class="card-title">
              <strong>{String(r.slug ?? '')}</strong>
              <span class="category-badge">{String(r.kind ?? '')}</span>
            </div>
            {#if requestFilterStatus === 'pending'}
              <div class="card-actions">
                <button
                  class="btn btn-sm"
                  type="button"
                  onclick={() => resolveRequest(requestId, 'dismissed')}
                  disabled={requestsBusy.has(requestId)}
                >
                  Dismiss
                </button>
                <button
                  class="btn btn-sm btn-primary"
                  type="button"
                  onclick={() => resolveRequest(requestId, 'resolved')}
                  disabled={requestsBusy.has(requestId)}
                >
                  Mark resolved
                </button>
              </div>
            {/if}
          </div>
          <p class="request-message">{String(r.message ?? '')}</p>
          {#if r.contact}
            <p class="subtle">Private contact (admin-only): {String(r.contact)}</p>
          {/if}
        </article>
      {/each}
    {/if}
  </div>
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
  h2 {
    margin: 0;
    font-size: 1.25rem;
  }
  .intro {
    margin: 4px 0 0;
    font-size: 0.88rem;
    color: var(--muted);
    max-width: 68ch;
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
  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
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
  .category-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--muted);
  }
  .trusted-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--primary) 12%, var(--panel));
    color: var(--primary);
  }
  .btn-sm {
    padding: 6px 10px;
    font-size: 12.5px;
  }
  .danger-text {
    color: var(--danger);
  }
  .review-panel {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 10px;
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
  .field span {
    display: block;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    margin-bottom: 2px;
  }
  .draft-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .decision-row {
    display: flex;
    align-items: center;
    gap: 8px;
    justify-content: flex-end;
    flex-wrap: wrap;
  }
  .warn {
    color: var(--danger);
    font-size: 0.85rem;
    margin: 0;
  }
  .empty {
    text-align: center;
    padding: 24px;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }

  .requests-section {
    display: flex;
    flex-direction: column;
    gap: 12px;
    margin-top: 8px;
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  .requests-section h2 {
    margin: 0;
    font-size: 1.05rem;
  }
  .request-message {
    margin: 8px 0 0;
    font-size: 0.9rem;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>
