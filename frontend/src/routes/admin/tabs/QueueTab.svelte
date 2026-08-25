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
  }

  function tomorrowIso(): string {
    const d = new Date()
    d.setDate(d.getDate() + 1)
    return d.toISOString().slice(0, 10)
  }

  // Ranked pending artifacts (the queue's primary view).
  let day = $state(tomorrowIso())
  let items: PreviewItem[] = $state([])
  let humanPicked = $state(false)
  let platformSlotsFilled = $state(0)
  let platformSlotsAvailable = $state(0)
  let loading = $state(true)
  let error = $state<string | null>(null)
  let pinningId = $state<string | null>(null)
  let pinError = $state<string | null>(null)

  // Approved & awaiting paced release — still a live, distinct concept (not
  // part of the old lane/status system), kept as secondary context.
  let backlog: Array<Record<string, unknown>> = $state([])

  // Legacy publish-queue rows — read-only history, collapsed by default.
  // The old all/pending/done/deferred/expired lane system is retired; this
  // is kept only as a reference trail during the cutover to the artifact
  // pipeline, not as an actionable view.
  let historyOpen = $state(false)
  let historyLoaded = $state(false)
  let historyLoading = $state(false)
  let historyError = $state<string | null>(null)
  let history: Array<Record<string, unknown>> = $state([])

  async function load() {
    loading = true
    error = null
    try {
      const [res, b] = await Promise.all([
        admin.artifactsToComposePreview(day) as Promise<Record<string, unknown>>,
        admin.listPendingFeedBacklog().catch(() => ({ items: [] })),
      ])
      items = Array.isArray(res.items) ? (res.items as PreviewItem[]) : []
      humanPicked = Boolean(res.human_picked)
      platformSlotsFilled = Number(res.platform_slots_filled ?? 0)
      platformSlotsAvailable = Number(res.platform_slots_available ?? 0)
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

  async function loadHistory() {
    if (historyLoaded || historyLoading) return
    historyLoading = true
    historyError = null
    try {
      const res = await admin.listPublishQueue()
      history = Array.isArray(res.items) ? (res.items as Array<Record<string, unknown>>) : []
      historyLoaded = true
    } catch (e) {
      historyError = e instanceof Error ? e.message : String(e)
    } finally {
      historyLoading = false
    }
  }

  function formatTs(raw: unknown): string {
    if (!raw) return '—'
    const s = String(raw).replace('T', ' ')
    return s.length > 16 ? s.slice(0, 16) : s
  }

  function laneLabel(lane: PreviewItem['selected_lane']): string {
    if (lane === 'human') return 'human pick'
    if (lane === 'platform') return 'platform pick'
    return ''
  }

  function historyMeta(item: Record<string, unknown>): string {
    return [
      String(item.publish_kind ?? ''),
      String(item.topic ?? ''),
      String(item.scrape_url ?? item.url ?? ''),
    ]
      .filter(Boolean)
      .join(' · ')
  }

  $effect(() => {
    void day
    void load()
  })

  $effect(() => {
    if (historyOpen) void loadHistory()
  })
</script>

<div class="admin-stack">
  <div class="admin-toolbar">
    <div>
      <h2>Queue</h2>
      <p class="admin-muted intro">
        Pending artifacts ranked by priority — what's coming up to compose next. Pin one as
        tomorrow's human pick, or let the top platform-ranked artifacts fill the remaining slots.
        Once an artifact is composed it becomes an article, visible in the
        <a href="/admin?tab=articles">Articles</a> tab — it no longer lingers here.
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
      Showing what would currently be selected for this day —
      {humanPicked ? 'a human pick is pinned' : 'no human pick is pinned yet'}, plus
      {platformSlotsFilled} of {platformSlotsAvailable} platform slot(s) filled by top-priority
      pending artifacts.
    </p>
    <p class="admin-muted small">
      "Pin for tomorrow" always pins the real tomorrow's human slot, regardless of which day you're
      viewing above.
    </p>
  </div>

  {#if pinError}
    <p class="admin-err">{pinError}</p>
  {/if}

  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else if items.length === 0}
    <section class="admin-panel empty">
      <strong>No pending artifacts</strong>
      <p class="admin-muted">Nothing is waiting in the artifact pool right now.</p>
    </section>
  {:else}
    {#each items as item (item.artifact_id)}
      <div class="admin-panel artifact-row" class:selected={Boolean(item.selected_lane)}>
        <div class="row-head">
          {#if item.selected_lane}
            <span class="lane-badge lane-{item.selected_lane}">{laneLabel(item.selected_lane)}</span>
          {/if}
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
            <span>ecosystem listed</span><span>{item.priority_breakdown.ecosystem_listed.toFixed(2)}</span>
          </div>
        </div>
        <div class="row-actions">
          <button
            class="btn compact"
            type="button"
            disabled={pinningId === item.artifact_id || item.is_pinned_for_day}
            onclick={() => pinForTomorrow(item.artifact_id)}
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
    {/each}
  {/if}

  {#if backlog.length}
    <section class="admin-panel stack">
      <div class="section-head">
        <h3>Approved — awaiting paced release ({backlog.length})</h3>
      </div>
      <p class="admin-muted small">
        Already-composed articles waiting their turn on the standard release pace — distinct from
        the pending-artifact ranking above.
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

  <details class="admin-panel history" bind:open={historyOpen}>
    <summary>Legacy queue history (reference only, being retired)</summary>
    <p class="admin-muted small history-note">
      Read-only rows from the old publish-queue lane system, kept for reference while the artifact
      pipeline takes over. No actions here — composing now happens through the ranked list above.
    </p>
    {#if historyLoading}
      <p class="admin-muted">Loading…</p>
    {:else if historyError}
      <p class="admin-err">{historyError}</p>
    {:else if historyLoaded && history.length === 0}
      <p class="admin-muted">No legacy queue rows.</p>
    {:else if historyLoaded}
      {#each history as item (String(item.queue_id))}
        <div class="history-row">
          <div class="history-head">
            <span class="history-status">{String(item.status ?? '')}</span>
            <strong class="display-name">
              {String(item.display_name ?? item.service_id ?? item.queue_id)}
            </strong>
            <span class="admin-muted small">prio {Number(item.priority ?? 0)}</span>
          </div>
          {#if item.last_reason}
            <p class="admin-muted small history-reason">{String(item.last_reason)}</p>
          {/if}
          {#if historyMeta(item)}
            <p class="admin-muted small meta">{historyMeta(item)}</p>
          {/if}
          <p class="admin-muted small">{formatTs(item.updated_at)}</p>
        </div>
      {/each}
    {/if}
  </details>
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
  .artifact-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .artifact-row.selected {
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  }
  .row-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
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
  }
  .lane-human {
    background: color-mix(in srgb, var(--primary) 14%, var(--panel));
    color: var(--primary);
  }
  .lane-platform {
    background: color-mix(in srgb, var(--gain) 12%, var(--panel));
    color: var(--gain);
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

  .history {
    padding: 14px 18px;
  }

  .history summary {
    cursor: pointer;
    font-weight: 700;
    font-size: 0.92rem;
    color: var(--muted);
  }

  .history-note {
    margin: 8px 0 12px;
  }

  .history-row {
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
  }

  .history-row:last-child {
    border-bottom: 0;
  }

  .history-head {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .history-status {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    color: var(--subtle);
  }

  .history-reason {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
</style>
