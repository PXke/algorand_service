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

  let day = $state(tomorrowIso())
  let items: PreviewItem[] = $state([])
  let humanPicked = $state(false)
  let platformSlotsFilled = $state(0)
  let platformSlotsAvailable = $state(0)
  let loading = $state(true)
  let error = $state<string | null>(null)
  let pinningId = $state<string | null>(null)
  let pinError = $state<string | null>(null)

  async function load() {
    loading = true
    error = null
    try {
      const res = (await admin.artifactsToComposePreview(day)) as Record<string, unknown>
      items = Array.isArray(res.items) ? (res.items as PreviewItem[]) : []
      humanPicked = Boolean(res.human_picked)
      platformSlotsFilled = Number(res.platform_slots_filled ?? 0)
      platformSlotsAvailable = Number(res.platform_slots_available ?? 0)
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

  function formatTs(raw: string | null): string {
    if (!raw) return '—'
    return raw.replace('T', ' ').slice(0, 16)
  }

  function laneLabel(lane: PreviewItem['selected_lane']): string {
    if (lane === 'human') return 'human pick'
    if (lane === 'platform') return 'platform pick'
    return ''
  }

  $effect(() => {
    void day
    void load()
  })
</script>

<div class="admin-stack">
  <div class="admin-toolbar">
    <div>
      <h2>To-Compose Preview <span class="shadow-badge">Preview / shadow feature</span></h2>
      <p class="admin-muted intro">
        Read-mostly preview of the new editorial-room artifact system — a separate, not-yet-live
        selection pipeline running alongside today's actual Queue tab. Nothing here affects real
        publishing: the ranked list and "pin" action only touch the new shadow
        <code>artifacts</code> / <code>to_compose</code> tables, which the live compose pipeline
        does not read yet.
      </p>
    </div>
    <button class="btn compact" type="button" disabled={loading} onclick={() => load()}>
      Refresh
    </button>
  </div>

  <div class="admin-panel toolbar-row">
    <label class="field">
      <span>Preview day</span>
      <input type="date" bind:value={day} class="admin-select" />
    </label>
    <p class="admin-muted small">
      Defaults to tomorrow. The list below shows what the shadow selection would currently pick
      for this day — {humanPicked ? 'a human pick is pinned' : 'no human pick is pinned yet'},
      plus {platformSlotsFilled} of {platformSlotsAvailable} platform slot(s) filled by top-priority
      pending artifacts.
    </p>
    <p class="admin-muted small">
      The "Pin for tomorrow" button below always pins the real tomorrow's human slot, regardless
      of which day you're previewing above.
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
      <p class="admin-muted">Nothing is waiting in the shadow artifact pool right now.</p>
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
</div>

<style>
  h2 {
    margin: 0;
    font-family: var(--font-display);
    font-size: 1.35rem;
    font-weight: 700;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .shadow-badge {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    padding: 3px 8px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--warn, orange) 16%, var(--panel));
    color: var(--warn, #b35c00);
    border: 1px solid color-mix(in srgb, var(--warn, orange) 40%, var(--border));
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
</style>
