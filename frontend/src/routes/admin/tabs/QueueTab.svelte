<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'
  import { navigate } from '../../../lib/router'

  let { admin }: { admin: AdminApi } = $props()

  const STATUS_FILTERS = ['all', 'pending', 'done', 'deferred', 'expired'] as const

  let queue: Array<Record<string, unknown>> = $state([])
  let backlog: Array<Record<string, unknown>> = $state([])
  let filter = $state<(typeof STATUS_FILTERS)[number]>('all')
  let loading = $state(true)
  let error = $state<string | null>(null)
  let expanded = $state<Set<string>>(new Set())
  let breakdowns = $state<Record<string, Record<string, unknown>>>({})
  let breakdownLoading = $state<Set<string>>(new Set())
  let bumpingId = $state<string | null>(null)
  let bumpError = $state<string | null>(null)
  let recomposingNowId = $state<string | null>(null)
  let recomposeNowError = $state<string | null>(null)
  let recomposedNowIds = $state<Set<string>>(new Set())
  let pickingId = $state<string | null>(null)
  let pickError = $state<string | null>(null)
  let deadEndingId = $state<string | null>(null)
  let deadEndError = $state<string | null>(null)
  let deadEndedDomains = $state<Record<string, string>>({})

  const filtered = $derived(
    filter === 'all' ? queue : queue.filter((x) => String(x.status ?? '') === filter),
  )

  // Same UTC "YYYY-MM-DD" convention the backend stamps burst_day with
  // (workers' publish_daily_guard._day_key) -- toISOString() is always UTC
  // regardless of the admin's local timezone, so this lines up exactly.
  const todayUtc = new Date().toISOString().slice(0, 10)
  const todaysBurst = $derived(queue.filter((x) => String(x.burst_day ?? '') === todayUtc))

  type BurstStage = { label: string; tone: 'pending' | 'review' | 'done' | 'skipped' }

  function burstStage(item: Record<string, unknown>): BurstStage {
    const status = String(item.status ?? '')
    const reason = String(item.last_reason ?? '')
    if (status === 'pending') {
      return { label: 'Selected — waiting for the off-peak compose run', tone: 'pending' }
    }
    if (reason === 'review') {
      return { label: 'Composed — awaiting your review', tone: 'review' }
    }
    if (reason.startsWith('duplicate')) {
      return { label: 'Skipped — a review for this source is already pending', tone: 'skipped' }
    }
    if (status === 'done') {
      return { label: `Composed — ${reason || 'resolved'}`, tone: 'done' }
    }
    return { label: reason || status || 'unresolved', tone: 'skipped' }
  }

  function goToReview() {
    navigate('/admin?tab=classifier', true, false)
  }

  function statusColor(status: string): string {
    switch (status) {
      case 'pending':
        return '#f59e0b'
      case 'done':
        return '#2e7d32'
      case 'expired':
      case 'deferred':
        return 'var(--muted)'
      default:
        return 'var(--primary)'
    }
  }

  function formatTs(raw: unknown): string {
    const s = String(raw ?? '').replace('T', ' ')
    return s.length > 16 ? s.slice(0, 16) : s
  }

  function rowMeta(item: Record<string, unknown>): string {
    return [
      String(item.publish_kind ?? ''),
      String(item.topic ?? ''),
      String(item.scrape_url ?? item.url ?? ''),
    ]
      .filter(Boolean)
      .join(' · ')
  }

  async function load() {
    loading = true
    error = null
    try {
      const [q, b] = await Promise.all([
        admin.listPublishQueue(),
        admin.listPendingFeedBacklog().catch(() => ({ items: [] })),
      ])
      queue = Array.isArray(q.items) ? (q.items as Array<Record<string, unknown>>) : []
      backlog = Array.isArray(b.items) ? (b.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function toggleExpand(queueId: string) {
    if (expanded.has(queueId)) {
      const nextExpanded = new Set(expanded)
      nextExpanded.delete(queueId)
      expanded = nextExpanded
      return
    }

    expanded = new Set(expanded).add(queueId)

    if (breakdowns[queueId] || breakdownLoading.has(queueId)) return

    breakdownLoading = new Set(breakdownLoading).add(queueId)
    try {
      const detail = await admin.publishQueueBreakdown(queueId)
      breakdowns = { ...breakdowns, [queueId]: detail as Record<string, unknown> }
    } catch {
      breakdowns = { ...breakdowns, [queueId]: {} }
    } finally {
      const nextLoading = new Set(breakdownLoading)
      nextLoading.delete(queueId)
      breakdownLoading = nextLoading
    }
  }

  async function composeNext(queueId: string) {
    bumpingId = queueId
    bumpError = null
    try {
      await admin.composeQueueItemNext(queueId)
      await load()
    } catch (e) {
      bumpError = e instanceof Error ? e.message : String(e)
    } finally {
      bumpingId = null
    }
  }

  async function recomposeNow(queueId: string) {
    if (
      !confirm(
        'Compose this row immediately, bypassing the standard pacing gate? ' +
          'This spends real Mistral usage and can take several minutes ' +
          '(longer for a special edition) — watch the Sessions tab for progress.',
      )
    ) {
      return
    }
    recomposingNowId = queueId
    recomposeNowError = null
    try {
      await admin.recomposeQueueItemNow(queueId)
      recomposedNowIds = new Set(recomposedNowIds).add(queueId)
    } catch (e) {
      recomposeNowError = e instanceof Error ? e.message : String(e)
    } finally {
      recomposingNowId = null
    }
  }

  async function pickForToday(queueId: string) {
    pickingId = queueId
    pickError = null
    try {
      await admin.pickQueueItemForToday(queueId)
      await load()
    } catch (e) {
      pickError = e instanceof Error ? e.message : String(e)
    } finally {
      pickingId = null
    }
  }

  async function deadEndDomain(queueId: string) {
    if (!confirm('Permanently reject this row\'s source domain? It will never be re-crawled or re-composed.')) return
    deadEndingId = queueId
    deadEndError = null
    try {
      const result = await admin.deadEndQueueItemDomain(queueId)
      deadEndedDomains = { ...deadEndedDomains, [queueId]: String(result.domain ?? '') }
    } catch (e) {
      deadEndError = e instanceof Error ? e.message : String(e)
    } finally {
      deadEndingId = null
    }
  }

  function signalsText(signals: unknown): string {
    if (!signals || typeof signals !== 'object' || Array.isArray(signals)) return ''
    return Object.entries(signals as Record<string, unknown>)
      .map(([k, v]) => `${k}=${v}`)
      .join('  ')
  }

  $effect(() => {
    void load()
  })
</script>

<div class="admin-stack">
  <div class="admin-toolbar">
    <p class="admin-muted intro">
      Publish-queue rows with the last drain/compose decision each one received — newest activity
      first.
    </p>
    <button class="btn compact" type="button" disabled={loading} onclick={() => load()}>
      Refresh
    </button>
  </div>

  <p class="admin-muted pipeline-legend">
    Pipeline: queued candidates below → today's picks (batch-composed off-peak) → your review on
    the <button type="button" class="link-inline" onclick={goToReview}>Classifier tab</button> →
    approved &amp; up next, at the bottom of this page.
  </p>

  <section class="admin-panel stack">
    <div class="section-head">
      <h3>Today's picks ({todaysBurst.length})</h3>
    </div>
    <p class="admin-muted small">
      The human pick plus the system's top discovery + scale candidates for {todayUtc}. Not yet
      approved — nothing here publishes until you review it.
    </p>
    {#if todaysBurst.length === 0}
      <p class="admin-muted small">
        Nothing selected for today yet — the daily burst selection hasn't run, or is disabled.
      </p>
    {:else}
      {#each todaysBurst as item (String(item.queue_id))}
        {@const stage = burstStage(item)}
        <div class="burst-row">
          <span class="burst-title">{String(item.display_name ?? item.service_id ?? '—')}</span>
          <span class="burst-stage" class:review={stage.tone === 'review'} class:skipped={stage.tone === 'skipped'}>
            {stage.label}
          </span>
          {#if stage.tone === 'review'}
            <button type="button" class="btn compact" onclick={goToReview}>Review now</button>
          {/if}
        </div>
      {/each}
    {/if}
  </section>

  {#if backlog.length}
    <section class="admin-panel stack">
      <div class="section-head">
        <h3>Approved — up next to publish ({backlog.length})</h3>
      </div>
      <p class="admin-muted small">
        Source of truth for publish order: released one at a time on the standard pacing clock.
      </p>
      {#each backlog as item (String(item.service_id ?? item.title))}
        <div class="backlog-row">
          <span class="backlog-title">
            {String(item.title ?? item.service_id ?? '—')}
          </span>
          {#if item.service_id && item.title}
            <span class="admin-muted small">{String(item.service_id)}</span>
          {/if}
          <span class="backlog-time admin-muted">{formatTs(item.approved_at)}</span>
        </div>
      {/each}
    </section>
  {/if}

  <div class="filter-chips">
    {#each STATUS_FILTERS as status (status)}
      <button
        type="button"
        class="filter-chip"
        class:active={filter === status}
        onclick={() => (filter = status)}
      >
        {status}
      </button>
    {/each}
  </div>

  {#if bumpError}
    <p class="admin-err">{bumpError}</p>
  {/if}
  {#if recomposeNowError}
    <p class="admin-err">{recomposeNowError}</p>
  {/if}
  {#if pickError}
    <p class="admin-err">{pickError}</p>
  {/if}
  {#if deadEndError}
    <p class="admin-err">{deadEndError}</p>
  {/if}

  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else if filtered.length === 0}
    <section class="admin-panel empty">
      <strong>Queue is empty</strong>
      <p class="admin-muted">No publish-queue rows match this filter.</p>
    </section>
  {:else}
    {#each filtered as item (String(item.queue_id))}
      {@const queueId = String(item.queue_id ?? '')}
      {@const status = String(item.status ?? '')}
      {@const isOpen = expanded.has(queueId)}
      {@const detail = breakdowns[queueId]}
      {@const isLoadingDetail = breakdownLoading.has(queueId)}
      <div class="admin-panel queue-row" class:open={isOpen}>
        <button
          type="button"
          class="row-toggle"
          onclick={() => toggleExpand(queueId)}
        >
          <div class="row-head">
            <span class="status-dot" style="background: {statusColor(status)}"></span>
            <span class="status-label">{status}</span>
            <strong class="display-name">
              {String(item.display_name ?? item.service_id ?? queueId)}
            </strong>
            <span class="priority admin-muted">prio {Number(item.priority ?? 0)}</span>
            {#if item.human_pick_day}
              <span class="human-pick-badge" title="Pinned as Lane 1 (human pick)">
                picked for {String(item.human_pick_day)}
              </span>
            {/if}
          </div>

          {#if item.last_reason}
            <p class="last-reason">last decision: {String(item.last_reason)}</p>
          {/if}

          {#if rowMeta(item)}
            <p class="admin-muted small meta">{rowMeta(item)}</p>
          {/if}

          {#if item.updated_at}
            <p class="admin-muted small">{formatTs(item.updated_at)}</p>
          {/if}
        </button>

        {#if status === 'pending' || item.scrape_url}
          <div class="row-actions">
            {#if status === 'pending'}
              <button
                class="btn compact"
                type="button"
                disabled={bumpingId === queueId}
                onclick={() => composeNext(queueId)}
              >
                {bumpingId === queueId ? 'Pinning…' : 'Compose next'}
              </button>
              <button
                class="btn compact"
                type="button"
                disabled={pickingId === queueId || Boolean(item.human_pick_day)}
                onclick={() => pickForToday(queueId)}
                title="Reserve one of today's 3 publish slots (Lane 1) for this row"
              >
                {#if pickingId === queueId}
                  Picking…
                {:else if item.human_pick_day}
                  Picked ✓
                {:else}
                  Pick for today
                {/if}
              </button>
              <button
                class="btn compact btn-danger"
                type="button"
                disabled={recomposingNowId === queueId || recomposedNowIds.has(queueId)}
                onclick={() => recomposeNow(queueId)}
              >
                {#if recomposingNowId === queueId}
                  Triggering…
                {:else if recomposedNowIds.has(queueId)}
                  Triggered ✓
                {:else}
                  Recompose now
                {/if}
              </button>
            {/if}
            {#if item.scrape_url}
              {#if deadEndedDomains[queueId]}
                <span class="admin-muted small">domain rejected: {deadEndedDomains[queueId]}</span>
              {:else}
                <button
                  class="btn compact btn-danger"
                  type="button"
                  disabled={deadEndingId === queueId}
                  onclick={() => deadEndDomain(queueId)}
                >
                  {deadEndingId === queueId ? 'Rejecting…' : 'Dead-end domain'}
                </button>
              {/if}
            {/if}
          </div>
        {/if}

        {#if isOpen}
          <div class="breakdown">
            {#if isLoadingDetail}
              <div class="breakdown-loading" aria-hidden="true"></div>
              <p class="admin-muted">Loading breakdown…</p>
            {:else if detail && Object.keys(detail).length}
              {#if detail.priority_breakdown}
                <div class="breakdown-block">
                  <strong>Priority breakdown</strong>
                  <pre class="mono">{String(detail.priority_breakdown)}</pre>
                </div>
              {/if}
              {#if detail.signals && signalsText(detail.signals)}
                <div class="breakdown-block">
                  <strong>Content signals</strong>
                  <pre class="mono">{signalsText(detail.signals)}</pre>
                </div>
              {/if}
              {#if detail.diff_preview}
                <div class="breakdown-block">
                  <strong>Diff preview</strong>
                  <pre class="mono diff">{String(detail.diff_preview)}</pre>
                </div>
              {/if}
            {:else}
              <p class="admin-muted">No breakdown available for this row.</p>
            {/if}
          </div>
        {/if}
      </div>
    {/each}
  {/if}
</div>

<style>
  .intro {
    flex: 1;
    min-width: 200px;
  }

  .compact {
    padding: 8px 14px;
    font-size: 13px;
  }

  .section-head h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 700;
  }

  .small {
    margin: 0;
    font-size: 0.92rem;
  }

  .pipeline-legend {
    margin: 0;
    font-size: 0.88rem;
  }

  .link-inline {
    background: none;
    border: 0;
    padding: 0;
    margin: 0;
    color: var(--primary);
    font: inherit;
    font-weight: 600;
    text-decoration: underline;
    cursor: pointer;
  }

  .burst-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
  }

  .burst-row:last-child {
    border-bottom: 0;
  }

  .burst-title {
    flex: 1;
    min-width: 160px;
    font-size: 0.92rem;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .burst-stage {
    font-size: 12px;
    font-weight: 600;
    padding: 3px 10px;
    border-radius: 999px;
    background: var(--accent-soft);
    color: var(--muted);
    white-space: nowrap;
  }

  .burst-stage.review {
    background: color-mix(in srgb, var(--primary) 18%, transparent);
    color: var(--primary);
  }

  .burst-stage.skipped {
    background: color-mix(in srgb, var(--danger) 14%, transparent);
    color: var(--danger);
  }

  .backlog-row {
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 4px 12px;
    padding: 4px 0;
    border-bottom: 1px solid var(--border);
    text-align: start;
  }

  .backlog-row:last-child {
    border-bottom: 0;
  }

  .backlog-title {
    grid-column: 1;
    font-size: 0.92rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .backlog-time {
    grid-column: 2;
    grid-row: 1 / span 2;
    font-size: 11px;
    white-space: nowrap;
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
    text-transform: capitalize;
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

  .queue-row {
    transition: border-color 0.15s ease;
  }

  .queue-row.open {
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  }

  .row-toggle {
    display: block;
    width: 100%;
    text-align: start;
    cursor: pointer;
    background: none;
    border: 0;
    padding: 0;
    color: inherit;
    font: inherit;
  }

  .row-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 8px;
  }

  .btn-danger {
    background: var(--danger);
    color: #fff;
    border-color: var(--danger);
  }

  .row-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  .status-label {
    font-size: 12px;
    font-weight: 700;
    text-transform: uppercase;
  }

  .display-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.95rem;
  }

  .priority {
    font-size: 11px;
    font-weight: 600;
  }

  .human-pick-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--primary) 16%, transparent);
    color: var(--primary);
    white-space: nowrap;
  }

  .last-reason {
    margin: 6px 0 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.92rem;
    color: var(--primary);
  }

  .meta {
    margin: 4px 0 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .breakdown {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .breakdown-loading {
    height: 2px;
    background: linear-gradient(90deg, var(--primary), transparent);
    animation: pulse 1.2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%,
    100% {
      opacity: 0.4;
    }
    50% {
      opacity: 1;
    }
  }

  .breakdown-block strong {
    display: block;
    font-size: 12px;
    margin-bottom: 4px;
  }

  .mono {
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .diff {
    padding: 10px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface);
    max-height: 360px;
    overflow: auto;
  }
</style>
