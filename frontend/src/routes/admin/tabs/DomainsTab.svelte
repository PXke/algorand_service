<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  const PAGE_SIZE = 25
  const FILTERS = ['all', 'pending', 'approved', 'dead_end'] as const

  let filter = $state<(typeof FILTERS)[number]>('all')
  let page = $state(0)
  let items: Array<Record<string, unknown>> = $state([])
  let total = $state(0)
  let autoApprovedToday = $state(0)
  let loading = $state(true)
  let error = $state<string | null>(null)
  let newDomain = $state('')
  let addFullSite = $state(true)
  let adding = $state(false)
  let busy = $state<Set<string>>(new Set())

  const totalPages = $derived(total === 0 ? 1 : Math.ceil(total / PAGE_SIZE))
  const displayPage = $derived(page + 1)

  function humanizeRelevanceReasons(raw: string): string {
    if (!raw) return ''
    const parts: string[] = []
    for (const tag of raw.split(';')) {
      const t = tag.trim()
      if (!t) continue
      const i = t.indexOf(':')
      const key = i === -1 ? t : t.slice(0, i)
      const value = i === -1 ? '' : t.slice(i + 1)
      switch (key) {
        case 'known_domain':
          parts.push(`known Algorand domain (${value})`)
          break
        case 'keywords':
          parts.push(`${value} Algorand keyword${value === '1' ? '' : 's'} found`)
          break
        case 'exact':
          parts.push(`mentions "${value}" directly`)
          break
        case 'reject_noise':
          parts.push(`${value} off-topic term${value === '1' ? '' : 's'} (algorithm/algebra/etc.)`)
          break
        case 'below_threshold':
          parts.push('no positive signals found')
          break
        default:
          parts.push(t)
      }
    }
    return parts.join(', ')
  }

  function relevanceColor(score: number): string {
    if (score >= 0.4) return '#2E7D32'
    if (score >= 0.2) return '#B7791F'
    return '#9AA0A6'
  }

  function statusDotColor(item: Record<string, unknown>): string {
    const pending = item.frontier_status === 'pending'
    const relevant = item.is_relevant === true
    if (pending) return '#B7791F'
    if (relevant) return '#2E7D32'
    return 'var(--danger)'
  }

  function normalizeDomainInput(raw: string): string {
    return raw
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, '')
      .split('/')[0]!
  }

  function domainHref(item: Record<string, unknown>): string {
    const pendingUrl = String(item.pending_url ?? '')
    const domain = String(item.domain ?? '')
    const target = pendingUrl || domain
    if (!target) return '#'
    return target.startsWith('http') ? target : `https://${target}`
  }

  function filterLabel(f: (typeof FILTERS)[number]): string {
    const labels: Record<(typeof FILTERS)[number], string> = {
      all: 'All',
      pending: 'Pending',
      approved: 'Approved',
      dead_end: 'Dead end',
    }
    if (filter === f && total > 0) return `${labels[f]} (${total})`
    return labels[f]
  }

  async function load() {
    loading = true
    error = null
    try {
      const res = await admin.listDomains(filter, page, PAGE_SIZE)
      items = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
      total = Number(res.total ?? items.length)
      autoApprovedToday = Number(res.auto_approved_today ?? 0)
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function setRelevant(
    item: Record<string, unknown>,
    isRelevant: boolean,
    fullSite = true,
  ) {
    const domain = String(item.domain ?? '')
    if (!domain) return
    busy = new Set(busy).add(domain)
    try {
      await admin.setDomainRelevant({ domain, is_relevant: isRelevant, full_site: fullSite })
      onmessage?.(`${domain} → ${isRelevant ? 'relevant' : 'dead end'}`)
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      const next = new Set(busy)
      next.delete(domain)
      busy = next
    }
  }

  async function addDomain() {
    const domain = normalizeDomainInput(newDomain)
    if (!domain) return
    adding = true
    try {
      await admin.setDomainRelevant({ domain, is_relevant: true, full_site: addFullSite })
      newDomain = ''
      onmessage?.(`Approved ${domain}`)
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      adding = false
    }
  }

  function selectFilter(f: (typeof FILTERS)[number]) {
    filter = f
    page = 0
  }

  $effect(() => {
    filter
    page
    void load()
  })
</script>

<div class="admin-stack">
  <div class="admin-toolbar">
    <p class="admin-muted intro">
      Crawl frontier review — decide which discovered domains the crawler may explore.
    </p>
    <div class="toolbar-actions">
      {#if autoApprovedToday > 0}
        <span class="admin-chip auto-badge" title="Domains the frontier auto-approved today (UTC)">
          Auto-approved today: {autoApprovedToday}
        </span>
      {/if}
      <button class="btn" type="button" disabled={loading} onclick={() => load()}>Refresh</button>
    </div>
  </div>

  <section class="admin-panel stack">
    <form
      class="add-form"
      onsubmit={(e) => {
        e.preventDefault()
        if (!adding) void addDomain()
      }}
    >
      <div class="add-row">
        <label class="field grow">
          <span class="admin-muted">Domain</span>
          <input bind:value={newDomain} placeholder="example.com" required />
        </label>
        <button class="btn btn-primary compact" type="submit" disabled={adding}>
          {adding ? 'Adding…' : 'Add domain'}
        </button>
      </div>
      <label class="check-row">
        <input type="checkbox" bind:checked={addFullSite} />
        <span>Approve &amp; explore full site</span>
      </label>
    </form>
  </section>

  <div class="filter-chips">
    {#each FILTERS as f (f)}
      <button
        type="button"
        class="filter-chip"
        class:active={filter === f}
        onclick={() => selectFilter(f)}
      >
        {filterLabel(f)}
      </button>
    {/each}
  </div>

  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else if items.length === 0}
    <section class="admin-panel empty">
      <strong>No domains match this filter</strong>
      <p class="admin-muted">Try another status or add a domain manually.</p>
    </section>
  {:else}
    {#each items as item (String(item.domain))}
      {@const domain = String(item.domain ?? '')}
      {@const pending = item.frontier_status === 'pending'}
      {@const relevant = item.is_relevant === true}
      {@const score = Number(item.relevance_score ?? 0)}
      {@const contentRel = item.content_relevance != null ? Number(item.content_relevance) : null}
      {@const reasons = String(item.content_relevance_reasons ?? '')}
      {@const humanReasons = humanizeRelevanceReasons(reasons)}
      {@const pendingUrl = String(item.pending_url ?? '')}
      {@const category = String(item.category ?? item.category_admin ?? '')}
      {@const pagesCrawled = Number(item.pages_crawled ?? 0)}
      {@const lastCrawled = String(item.last_crawled_at ?? '').split('T')[0]}
      {@const suggestedFullSite = item.suggested_full_site as boolean | undefined}
      {@const sameDomainLinks = Number(item.same_domain_link_count ?? 0)}
      {@const isBusy = busy.has(domain)}
      <article class="admin-panel domain-card">
        <div class="domain-head">
          <span class="status-dot" style="background: {statusDotColor(item)}"></span>
          <a class="domain-link" href={domainHref(item)} target="_blank" rel="noopener noreferrer">
            {domain}
          </a>
          {#if contentRel != null}
            <span
              class="rel-chip"
              style="color: {relevanceColor(contentRel)}; border-color: color-mix(in srgb, {relevanceColor(contentRel)} 40%, transparent); background: color-mix(in srgb, {relevanceColor(contentRel)} 12%, transparent)"
              title={humanReasons || 'Content relevance score'}
            >
              rel {contentRel.toFixed(2)}
            </span>
          {/if}
          {#if item.possible_service === true}
            <span class="service-chip" title="Looks like an Algorand service">possible service</span>
          {/if}
          <span class="frontier-status admin-muted">{String(item.frontier_status ?? '')}</span>
        </div>

        {#if humanReasons}
          <p class="reasons admin-muted">{humanReasons}</p>
        {/if}

        {#if pending}
          {#if item.preview_title}
            <p class="preview-title">{String(item.preview_title)}</p>
          {/if}
          {#if item.preview_description}
            <p class="preview-desc admin-muted">{String(item.preview_description)}</p>
          {/if}
          {#if item.preview_keywords}
            <p class="preview-kw admin-muted">
              Keywords: {String(item.preview_keywords)}
            </p>
          {/if}
          {#if item.link_text}
            <p class="admin-muted small">Linked as: {String(item.link_text)}</p>
          {/if}
          <p class="admin-muted small meta-block">
            {#if score > 0}Predicted interest: {score.toFixed(1)}{/if}
            {#if pendingUrl}
              <br /><span class="mono">{pendingUrl}</span>
            {/if}
            {#if item.found_on}
              <br />Found on: {String(item.found_on)}
            {/if}
          </p>
        {:else}
          <p class="admin-muted small">
            Score {score.toFixed(1)}
            {#if category} · {category}{/if}
            · {pagesCrawled} page{pagesCrawled === 1 ? '' : 's'} crawled
            {#if lastCrawled} · crawled {lastCrawled}{/if}
          </p>
        {/if}

        <div class="card-actions">
          {#if pending}
            <button
              class="btn compact danger"
              type="button"
              disabled={isBusy}
              onclick={() => setRelevant(item, false)}
            >
              Dead end
            </button>
            <button
              class="btn compact"
              type="button"
              class:suggested={suggestedFullSite === false}
              title={suggestedFullSite === false
                ? `${sameDomainLinks} same-domain links found — single page may be enough`
                : ''}
              disabled={isBusy}
              onclick={() => setRelevant(item, true, false)}
            >
              Single page
            </button>
            <button
              class="btn btn-primary compact"
              type="button"
              class:suggested={suggestedFullSite === true}
              title={suggestedFullSite === true
                ? `${sameDomainLinks} same-domain links found — full site recommended`
                : ''}
              disabled={isBusy}
              onclick={() => setRelevant(item, true, true)}
            >
              {isBusy ? 'Saving…' : 'Approve site'}
            </button>
          {:else}
            <button
              class="btn compact"
              type="button"
              class:danger={relevant}
              class:btn-primary={!relevant}
              disabled={isBusy}
              onclick={() => setRelevant(item, !relevant, true)}
            >
              {isBusy ? 'Saving…' : relevant ? 'Mark dead end' : 'Revive'}
            </button>
          {/if}
        </div>
      </article>
    {/each}

    <nav class="pager" aria-label="Domain pagination">
      <button class="btn compact" type="button" disabled={page <= 0} onclick={() => (page -= 1)}>
        Previous
      </button>
      <span class="admin-muted">Page {displayPage} of {totalPages}</span>
      <button
        class="btn compact"
        type="button"
        disabled={page + 1 >= totalPages}
        onclick={() => (page += 1)}
      >
        Next
      </button>
    </nav>
  {/if}
</div>

<style>
  .intro {
    flex: 1;
    min-width: 200px;
  }

  .toolbar-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .auto-badge {
    text-transform: none;
    font-size: 12px;
  }

  .add-form {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .add-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: flex-end;
  }

  .grow {
    flex: 1;
    min-width: 180px;
  }

  .check-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.92rem;
    cursor: pointer;
  }

  .filter-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .filter-chip {
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    border-radius: 999px;
    padding: 6px 14px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }

  .filter-chip.active {
    background: var(--accent-soft);
    color: var(--primary);
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  }

  .empty {
    text-align: center;
    padding: 24px;
  }

  .domain-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .domain-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .status-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .domain-link {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-weight: 600;
    color: var(--primary);
    text-decoration: underline;
    text-decoration-color: color-mix(in srgb, var(--primary) 40%, transparent);
  }

  .rel-chip,
  .service-chip {
    display: inline-block;
    padding: 2px 6px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 700;
    border: 1px solid;
  }

  .service-chip {
    color: #8e24aa;
    border-color: color-mix(in srgb, #8e24aa 40%, transparent);
    background: color-mix(in srgb, #8e24aa 12%, transparent);
  }

  .frontier-status {
    margin-inline-start: auto;
    font-size: 11px;
    text-transform: uppercase;
    font-weight: 700;
  }

  .reasons {
    font-size: 11px;
    margin: 0;
  }

  .preview-title {
    margin: 0;
    font-weight: 600;
  }

  .preview-desc {
    margin: 0;
    line-height: 1.45;
    font-size: 0.92rem;
  }

  .preview-kw {
    margin: 0;
    font-style: italic;
    font-size: 11px;
  }

  .small {
    margin: 0;
    font-size: 11px;
    line-height: 1.5;
  }

  .meta-block .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    word-break: break-all;
  }

  .card-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 4px;
  }

  .compact {
    padding: 8px 14px;
    font-size: 13px;
  }

  .danger {
    color: var(--danger);
    border-color: color-mix(in srgb, var(--danger) 35%, var(--border));
  }

  .suggested {
    outline: 2px solid #00897b;
    outline-offset: 1px;
  }

  .pager {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 12px;
    padding-top: 4px;
  }
</style>
