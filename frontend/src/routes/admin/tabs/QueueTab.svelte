<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  type PreviewItem = {
    artifact_id: string
    service_id: string | null
    url: string | null
    channel: string
    title: string
    created_at: string | null
    event_date: string | null
    priority: number
    priority_breakdown: { word_count: number; timeliness: number; ecosystem_listed: number }
    human_pick_day: string | null
    is_pinned_for_day: boolean
    selected_lane: 'human' | 'platform' | null
    pool: 'new_service' | 'update'
  }

  // A row from the REAL, persisted `to_compose` table (list_to_compose_for_day)
  // — what the daily selection beat actually locked in, not a forecast.
  type SelectedItem = {
    slot: number
    artifact_id: string
    lane: 'human' | 'platform'
    service_id: string | null
    picked_at: string | null
  }

  // Full detail for one artifact (admin.getArtifactContent) — the raw
  // text that would actually get fed to the writer/composer, fetched only
  // when a row is expanded, never as part of the list/preview poll above.
  type ArtifactDetail = {
    artifact_id: string
    title: string
    content: string
    metadata: Record<string, unknown>
    service_id: string | null
    url: string | null
    channel: string
    status: string
  }

  function tomorrowIso(): string {
    const d = new Date()
    d.setDate(d.getDate() + 1)
    return d.toISOString().slice(0, 10)
  }

  let day = $state(tomorrowIso())

  // Section 1: the real, persisted selection for `day` — empty until the
  // 00:05 UTC daily beat has run for it.
  let selected: SelectedItem[] = $state([])

  // Section 2: ranked pending artifacts — the candidate pool a selection run
  // would draw from (a live forecast, not the real selection above).
  let items: PreviewItem[] = $state([])
  let humanPicked = $state(false)
  let platformSlotsFilled = $state(0)
  let platformSlotsAvailable = $state(0)

  // Platform-forecast picks pulled out of the ranked pool so they're
  // immediately visible on their own, rather than needing to be spotted
  // inline while scanning the (now potentially very long) full list.
  let platformPicks: PreviewItem[] = $derived(
    items.filter((item) => item.selected_lane === 'platform')
  )
  let otherItems: PreviewItem[] = $derived(
    items.filter((item) => item.selected_lane !== 'platform')
  )
  let pinningId = $state<string | null>(null)
  let pinError = $state<string | null>(null)

  // "Redo today's picks" — clears the real, persisted to_compose selection
  // for `day` and immediately re-runs selection, reverting any artifact it
  // had selected back to pending first (unless it's already composed/
  // discarded, in which case it's left alone and reported back).
  let resetting = $state(false)
  let resetError = $state<string | null>(null)

  // Row expand-in-place: which artifact's content panel is currently open
  // (at most one at a time), a per-id cache of already-fetched detail so
  // re-expanding a row doesn't refetch, and the in-flight/error state for
  // whichever fetch is currently running.
  let expandedId = $state<string | null>(null)
  let detailCache: Record<string, ArtifactDetail> = $state({})
  let detailLoadingId = $state<string | null>(null)
  let detailError = $state<string | null>(null)

  async function toggleExpand(artifactId: string) {
    if (expandedId === artifactId) {
      expandedId = null
      return
    }
    expandedId = artifactId
    detailError = null
    if (detailCache[artifactId]) return
    detailLoadingId = artifactId
    try {
      const res = (await admin.getArtifactContent(artifactId)) as ArtifactDetail
      detailCache = { ...detailCache, [artifactId]: res }
    } catch (e) {
      detailError = e instanceof Error ? e.message : String(e)
    } finally {
      detailLoadingId = null
    }
  }

  function onRowKeydown(e: KeyboardEvent, artifactId: string) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      void toggleExpand(artifactId)
    }
  }

  // Section 3: approved & awaiting paced release — still a live, distinct
  // concept (not part of the old lane/status system).
  let backlog: Array<Record<string, unknown>> = $state([])

  let loading = $state(true)
  let error = $state<string | null>(null)

  async function load() {
    loading = true
    error = null
    try {
      const [res, sel, b] = await Promise.all([
        admin.artifactsToComposePreview(day) as Promise<Record<string, unknown>>,
        admin
          .artifactsToComposeSelected(day)
          .catch(() => ({ items: [] })) as Promise<Record<string, unknown>>,
        admin.listPendingFeedBacklog().catch(() => ({ items: [] })),
      ])
      items = Array.isArray(res.items) ? (res.items as PreviewItem[]) : []
      humanPicked = Boolean(res.human_picked)
      platformSlotsFilled = Number(res.platform_slots_filled ?? 0)
      platformSlotsAvailable = Number(res.platform_slots_available ?? 0)
      selected = Array.isArray(sel.items) ? (sel.items as SelectedItem[]) : []
      backlog = Array.isArray(b.items) ? (b.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function pinForTomorrow(artifactId: string) {
    pinningId = artifactId
    pinError = null
    try {
      await admin.pinArtifactForTomorrow(artifactId)
      onmessage?.('Pinned as tomorrow’s human pick')
      await load()
    } catch (e) {
      pinError = e instanceof Error ? e.message : String(e)
    } finally {
      pinningId = null
    }
  }

  async function redoPicks() {
    if (
      !confirm(
        `Redo the picks for ${day}? This clears the currently locked-in selection and picks again ` +
          'from the full pool. Any pick already turned into a composed article stays as-is — only ' +
          'still-pending selections are undone.',
      )
    ) {
      return
    }
    resetting = true
    resetError = null
    try {
      const res = (await admin.resetToComposeForDay(day)) as Record<string, unknown>
      const reset = (res.reset ?? {}) as Record<string, unknown>
      const skipped = Array.isArray(reset.skipped) ? reset.skipped : []
      onmessage?.(
        skipped.length
          ? `Picks redone for ${day} — ${skipped.length} already-composed/discarded pick(s) left as-is`
          : `Picks redone for ${day}`,
      )
      await load()
    } catch (e) {
      resetError = e instanceof Error ? e.message : String(e)
    } finally {
      resetting = false
    }
  }

  function formatTs(raw: unknown): string {
    if (!raw) return '—'
    const s = String(raw).replace('T', ' ')
    return s.length > 16 ? s.slice(0, 16) : s
  }

  function laneLabel(lane: 'human' | 'platform' | null): string {
    if (lane === 'human') return 'human pick'
    if (lane === 'platform') return 'platform pick'
    return ''
  }

  // new_service: this artifact's service has never had a published article
  // before. update: the service is already covered and this is a fresh
  // diff/post/video against it. See to_compose_selection.py's guaranteed
  // new-service-vs-update pool split for why this distinction exists.
  function poolLabel(pool: 'new_service' | 'update'): string {
    return pool === 'new_service' ? 'new service' : 'update / diff'
  }

  $effect(() => {
    void day
    void load()
  })
</script>

<div class="admin-stack">
  <div class="admin-toolbar">
    <div>
      <h2>Queue</h2>
      <p class="admin-muted intro">
        Three views of the editorial-room artifact pipeline: what's actually been
        <strong>selected</strong> for the compose day below, the full candidate pool
        <strong>ranked by priority</strong>, and what's already composed and
        <strong>awaiting paced release</strong>. Once an artifact is composed it becomes an
        article, visible in the <a href="/admin?tab=articles">Articles</a> tab — it no longer
        lingers here.
      </p>
    </div>
    <button class="btn compact" type="button" disabled={loading} onclick={() => load()}>
      Refresh
    </button>
  </div>

  <div class="admin-panel toolbar-row">
    <label class="field">
      <span>Day</span>
      <input type="date" bind:value={day} class="admin-select" />
    </label>
    <p class="admin-muted small">
      "Pin for tomorrow" always pins the real tomorrow's human slot, regardless of which day you're
      viewing above.
    </p>
  </div>

  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else}
    {#snippet artifactDetail(artifactId: string)}
      {#if expandedId === artifactId}
        <div class="artifact-detail" onclick={(e) => e.stopPropagation()} onkeydown={(e) => e.stopPropagation()} role="presentation">
          {#if detailLoadingId === artifactId}
            <p class="admin-muted small">Loading content…</p>
          {:else if detailError}
            <p class="admin-err small">{detailError}</p>
          {:else if detailCache[artifactId]}
            {@const d = detailCache[artifactId]}
            <p class="admin-muted small meta">
              {[d.channel, d.service_id, d.status].filter(Boolean).join(' · ')}
            </p>
            {#if d.url}
              <p class="small detail-url">
                <a href={d.url} target="_blank" rel="noopener noreferrer">{d.url}</a>
              </p>
            {/if}
            <pre class="artifact-content">{d.content || '(no content)'}</pre>
          {/if}
        </div>
      {/if}
    {/snippet}

    <!-- Section 1: what has actually been selected (real to_compose rows). -->
    <section class="admin-panel stack">
      <div class="section-head row">
        <h3>Selected for {day} ({selected.length})</h3>
        <button class="btn compact" type="button" disabled={resetting || loading} onclick={() => redoPicks()}>
          {resetting ? 'Redoing…' : "Redo today's picks"}
        </button>
      </div>
      <p class="admin-muted small">
        The real, persisted lineup — what the daily selection beat (00:05 UTC) actually locked in
        for this day. This is not a forecast; it only changes when that beat runs.
      </p>
      {#if resetError}
        <p class="admin-err">{resetError}</p>
      {/if}
      {#if selected.length === 0}
        <p class="admin-muted empty-note">
          Nothing locked in yet — the daily selection runs at 00:05 UTC and will pick from the
          ranked list below.
        </p>
      {:else}
        {#each selected as sel (sel.artifact_id)}
          <div
            class="selected-row"
            class:expanded={expandedId === sel.artifact_id}
            role="button"
            tabindex="0"
            aria-expanded={expandedId === sel.artifact_id}
            onclick={() => toggleExpand(sel.artifact_id)}
            onkeydown={(e) => onRowKeydown(e, sel.artifact_id)}
          >
            <div class="selected-row-head">
              <span class="expand-caret" aria-hidden="true"
                >{expandedId === sel.artifact_id ? '▾' : '▸'}</span
              >
              <span class="lane-badge lane-{sel.lane}">{laneLabel(sel.lane)}</span>
              <span class="selected-service">{sel.service_id || sel.artifact_id}</span>
              <span class="admin-muted small">slot {sel.slot}</span>
              <span class="admin-muted small selected-time">{formatTs(sel.picked_at)}</span>
            </div>
            {@render artifactDetail(sel.artifact_id)}
          </div>
        {/each}
      {/if}
    </section>

    {#snippet artifactRow(item: PreviewItem)}
      <div
        class="admin-panel artifact-row"
        class:selected={Boolean(item.selected_lane)}
        class:expanded={expandedId === item.artifact_id}
        role="button"
        tabindex="0"
        aria-expanded={expandedId === item.artifact_id}
        onclick={() => toggleExpand(item.artifact_id)}
        onkeydown={(e) => onRowKeydown(e, item.artifact_id)}
      >
        <div class="row-head">
          <span class="expand-caret" aria-hidden="true">{expandedId === item.artifact_id ? '▾' : '▸'}</span>
          {#if item.selected_lane}
            <span class="lane-badge lane-{item.selected_lane}">{laneLabel(item.selected_lane)}</span>
          {/if}
          <span class="pool-badge pool-{item.pool}">{poolLabel(item.pool)}</span>
          <strong class="display-name">{item.title || item.service_id || item.artifact_id}</strong>
          <span class="priority admin-muted">priority {item.priority.toFixed(2)}</span>
          {#if item.is_pinned_for_day}
            <span class="pinned-badge" title="Pinned as the human pick for {day}">
              pinned for {day}
            </span>
          {/if}
        </div>
        <p class="admin-muted small meta">
          {[item.channel, item.service_id, item.url].filter(Boolean).join(' · ')}
        </p>
        <p class="admin-muted small">
          created {formatTs(item.created_at)}
          {#if item.event_date} · event {formatTs(item.event_date)}{/if}
        </p>
        <div class="breakdown-block">
          <strong>Priority breakdown</strong>
          <div class="breakdown-grid mono">
            <span>word count</span><span>{item.priority_breakdown.word_count.toFixed(2)}</span>
            <span>timeliness</span><span>{item.priority_breakdown.timeliness.toFixed(2)}</span>
            <span>ecosystem listed</span><span
              >{item.priority_breakdown.ecosystem_listed.toFixed(2)}</span
            >
          </div>
        </div>
        {@render artifactDetail(item.artifact_id)}
        <div class="row-actions">
          <button
            class="btn compact"
            type="button"
            disabled={pinningId === item.artifact_id || item.is_pinned_for_day}
            onclick={(e) => {
              e.stopPropagation()
              pinForTomorrow(item.artifact_id)
            }}
          >
            {#if pinningId === item.artifact_id}
              Pinning…
            {:else if item.is_pinned_for_day}
              Pinned ✓
            {:else}
              Pin for tomorrow
            {/if}
          </button>
        </div>
      </div>
    {/snippet}

    {#if pinError}
      <p class="admin-err">{pinError}</p>
    {/if}

    <!-- Section 2a: platform picks pulled out on their own, isolated from
         the full ranked pool below -- these are what would currently fill
         the non-human platform slot(s) for {day}. -->
    <section class="stack">
      <div class="section-head">
        <h3>Platform pick{platformPicks.length === 1 ? '' : 's'} for {day} ({platformPicks.length})</h3>
      </div>
      <p class="admin-muted small">
        Forecast only, recomputed live — the top-priority artifact(s) that would currently fill
        {platformSlotsAvailable} platform slot(s) ({platformSlotsFilled} filled) if selection ran right
        now. {humanPicked ? 'A human pick is currently pinned.' : 'No human pick is pinned yet.'}
      </p>
      {#if platformPicks.length === 0}
        <div class="admin-panel empty">
          <strong>No platform pick yet</strong>
          <p class="admin-muted">Nothing in the pool currently ranks high enough to fill a slot.</p>
        </div>
      {:else}
        {#each platformPicks as item (item.artifact_id)}
          {@render artifactRow(item)}
        {/each}
      {/if}
    </section>

    <!-- Section 2b: the full ranked candidate pool a selection run draws from. -->
    <section class="stack">
      <div class="section-head">
        <h3>Pending, ranked by priority</h3>
      </div>
      <p class="admin-muted small">
        Every other candidate in the pool, same forecast ranking as above — not what's actually
        been locked in at the top of this page. Pin one as tomorrow's human pick, or let the
        top-ranked artifacts fill the remaining platform slots on their own.
      </p>

      {#if otherItems.length === 0}
        <div class="admin-panel empty">
          <strong>No other pending artifacts</strong>
          <p class="admin-muted">Nothing else is waiting in the artifact pool right now.</p>
        </div>
      {:else}
        {#each otherItems as item (item.artifact_id)}
          {@render artifactRow(item)}
        {/each}
      {/if}
    </section>

    <!-- Section 3: already composed, waiting on the release pace. -->
    {#if backlog.length}
      <section class="admin-panel stack">
        <div class="section-head">
          <h3>Approved — awaiting paced release ({backlog.length})</h3>
        </div>
        <p class="admin-muted small">
          Already-composed articles waiting their turn on the standard release pace — distinct from
          both the real selection and the ranked candidate pool above.
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
  {/if}
</div>

<style>
  h2 {
    margin: 0;
    font-family: var(--font-display);
    font-size: 1.35rem;
    font-weight: 700;
  }
  .intro {
    margin: 6px 0 0;
    max-width: 72ch;
  }
  .toolbar-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .field {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.9rem;
    font-weight: 600;
  }
  .field input {
    width: auto;
  }
  .compact {
    padding: 8px 14px;
    font-size: 13px;
  }
  .small {
    margin: 0;
    font-size: 0.88rem;
  }
  .stack {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .artifact-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .artifact-row.selected {
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  }
  .artifact-row {
    cursor: pointer;
  }
  .artifact-row.expanded {
    border-color: color-mix(in srgb, var(--primary) 45%, var(--border));
  }
  .row-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .expand-caret {
    font-size: 0.8rem;
    color: var(--muted);
    width: 0.9em;
    flex: 0 0 auto;
  }
  .artifact-detail {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--surface);
    /* Prevent the row's own click-to-toggle from swallowing text selection
       clicks inside the expanded panel (e.g. selecting/copying content). */
    cursor: text;
  }
  .detail-url a {
    word-break: break-all;
  }
  .artifact-content {
    margin: 0;
    max-height: 420px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-word;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.82rem;
    line-height: 1.45;
    padding: 10px;
    border-radius: 6px;
    background: var(--panel);
    border: 1px solid var(--border);
  }
  .display-name {
    flex: 1;
    min-width: 160px;
  }
  .priority {
    font-size: 0.85rem;
    font-weight: 600;
  }
  .meta {
    word-break: break-word;
  }
  .lane-badge {
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.2px;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .lane-human {
    background: color-mix(in srgb, var(--primary) 14%, var(--panel));
    color: var(--primary);
  }
  .lane-platform {
    background: color-mix(in srgb, var(--gain) 12%, var(--panel));
    color: var(--gain);
  }
  .pool-badge {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2px;
    padding: 2px 8px;
    border-radius: 999px;
    white-space: nowrap;
    border: 1px solid var(--border);
  }
  .pool-new_service {
    color: var(--primary);
    border-color: color-mix(in srgb, var(--primary) 40%, transparent);
  }
  .pool-update {
    color: var(--text-muted, var(--admin-muted, currentColor));
  }
  .pinned-badge {
    font-size: 11px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: var(--accent-soft);
    color: var(--primary);
  }
  .breakdown-block {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 8px 10px;
    border-radius: 8px;
    background: var(--surface);
  }
  .breakdown-block strong {
    font-size: 0.78rem;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: var(--subtle);
  }
  .breakdown-grid {
    display: grid;
    grid-template-columns: auto auto;
    gap: 2px 12px;
    font-size: 0.85rem;
    justify-content: start;
  }
  .breakdown-grid span:nth-child(odd) {
    color: var(--muted);
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .row-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  .empty {
    text-align: center;
    padding: 24px;
  }

  .section-head h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 700;
  }

  .section-head.row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }

  .empty-note {
    margin: 0;
    padding: 10px 12px;
    border-radius: 8px;
    background: var(--surface);
  }

  .selected-row {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    background: var(--surface);
    cursor: pointer;
  }

  .selected-row.expanded {
    outline: 1px solid color-mix(in srgb, var(--primary) 45%, var(--border));
  }

  .selected-row-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .selected-row .artifact-detail {
    background: var(--panel);
  }

  .selected-service {
    flex: 1;
    min-width: 140px;
    font-weight: 600;
  }

  .selected-time {
    white-space: nowrap;
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
</style>
