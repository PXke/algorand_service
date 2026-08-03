<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let { admin }: { admin: AdminApi } = $props()

  type Group = { label: string; entries: Array<Record<string, unknown>> }

  let suggestions: Array<Record<string, unknown>> = $state([])
  let feedback: Array<Record<string, unknown>> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let section = $state<'gaps' | 'feedback'>('gaps')

  function newestFirst(entries: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
    return [...entries].sort((a, b) => timestamp(b) - timestamp(a))
  }

  function timestamp(item: Record<string, unknown>): number {
    const t = Date.parse(String(item.created_at ?? ''))
    return Number.isNaN(t) ? 0 : t
  }

  function groupSuggestions(items: Array<Record<string, unknown>>): Group[] {
    const byCap = new Map<string, Group>()
    for (const item of items) {
      const cap = String(item.capability ?? '').trim()
      if (!cap) continue
      const key = cap.toLowerCase()
      let group = byCap.get(key)
      if (!group) {
        group = { label: cap, entries: [] }
        byCap.set(key, group)
      }
      group.entries.push(item)
    }
    const groups = [...byCap.values()].map((g) => ({ ...g, entries: newestFirst(g.entries) }))
    return groups.sort((a, b) => timestamp(b.entries[0]) - timestamp(a.entries[0]))
  }

  function groupFeedback(items: Array<Record<string, unknown>>): Group[] {
    const byCat = new Map<string, Group>()
    for (const item of items) {
      const cat = String(item.category ?? 'other').trim().toLowerCase()
      let group = byCat.get(cat)
      if (!group) {
        group = { label: cat, entries: [] }
        byCat.set(cat, group)
      }
      group.entries.push(item)
    }
    return [...byCat.values()].sort((a, b) => b.entries.length - a.entries.length)
  }

  const suggestionGroups = $derived(groupSuggestions(suggestions))
  const feedbackGroups = $derived(groupFeedback(feedback))

  async function load() {
    loading = true
    error = null
    try {
      const [s, f] = await Promise.all([admin.listToolSuggestions(), admin.listComposeFeedback()])
      suggestions = Array.isArray(s.items) ? (s.items as Array<Record<string, unknown>>) : []
      feedback = Array.isArray(f.items) ? (f.items as Array<Record<string, unknown>>) : []
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  function formatDate(raw: unknown): string {
    const s = String(raw ?? '')
    return s.includes('T') ? s.split('T')[0] : s
  }

  function metaLine(parts: string[]): string {
    return parts.filter(Boolean).join(' · ')
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <p class="intro">
      Writer introspection while composing — tool gaps and pipeline friction the model reports
      back. Use this to prioritize prompt, data, and tool fixes. Hard tool errors still go to
      Bugsnag.
    </p>
    <button class="btn" type="button" onclick={() => load()}>Refresh</button>
  </div>

  <div class="segment" role="tablist">
    <button
      type="button"
      role="tab"
      class:active={section === 'gaps'}
      onclick={() => (section = 'gaps')}
    >
      Tool gaps ({suggestions.length})
    </button>
    <button
      type="button"
      role="tab"
      class:active={section === 'feedback'}
      onclick={() => (section = 'feedback')}
    >
      Pipeline feedback ({feedback.length})
    </button>
  </div>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if section === 'gaps'}
    {#if !suggestionGroups.length}
      <div class="empty panel">
        <p><strong>No tool suggestions yet</strong></p>
        <p class="subtle">
          When the writer wishes it had a tool it lacks, it records the gap here. Nothing yet —
          that is a good sign the toolset is covering stories.
        </p>
      </div>
    {:else}
      {#each suggestionGroups as g}
        {@const shown = g.entries.slice(0, 5)}
        <section class="panel group-card">
          <div class="group-head">
            <h3 class="mono">{g.label}</h3>
            <span class="count-badge">{g.entries.length}× requested</span>
          </div>
          {#each shown as e}
            <div class="entry">
              {#if e.reason}
                <p class="entry-body">{String(e.reason)}</p>
              {/if}
              <p class="entry-meta">
                {metaLine([formatDate(e.created_at), String(e.source_url ?? '')])}
              </p>
            </div>
          {/each}
          {#if g.entries.length > shown.length}
            <p class="more">+ {g.entries.length - shown.length} more</p>
          {/if}
        </section>
      {/each}
    {/if}
  {:else if !feedbackGroups.length}
    <div class="empty panel">
      <p><strong>No pipeline feedback yet</strong></p>
      <p class="subtle">
        When the writer hits prompt confusion, bad source data, or tool friction, it can report it
        via report_compose_issue. Nothing recorded yet.
      </p>
    </div>
  {:else}
    {#each feedbackGroups as g}
      {@const shown = g.entries.slice(0, 8)}
      <section class="panel group-card">
        <div class="group-head">
          <h3>{g.label.replaceAll('_', ' ')}</h3>
          <span class="subtle">{g.entries.length} report{g.entries.length === 1 ? '' : 's'}</span>
        </div>
        {#each shown as e}
          <div class="entry feedback-entry">
            {#if e.summary}
              <p class="entry-summary">{String(e.summary)}</p>
            {/if}
            {#if e.detail}
              <p class="entry-body">{String(e.detail)}</p>
            {/if}
            <p class="entry-meta">
              {metaLine([
                String(e.severity ?? ''),
                String(e.related_tool ?? ''),
                formatDate(e.created_at),
                String(e.source_url ?? ''),
              ])}
            </p>
          </div>
        {/each}
        {#if g.entries.length > shown.length}
          <p class="more">+ {g.entries.length - shown.length} more</p>
        {/if}
      </section>
    {/each}
  {/if}
</div>

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }
  .intro {
    margin: 0;
    flex: 1;
    font-size: 0.88rem;
    color: var(--muted);
    line-height: 1.45;
    max-width: 62ch;
  }
  .segment {
    display: flex;
    gap: 4px;
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--surface);
    width: fit-content;
  }
  .segment button {
    border: 0;
    background: transparent;
    color: var(--muted);
    font-size: 13px;
    font-weight: 600;
    padding: 8px 14px;
    border-radius: 8px;
  }
  .segment button.active {
    background: var(--panel);
    color: var(--primary);
    box-shadow: 0 1px 3px var(--card-shadow);
  }
  .group-card {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .group-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
  }
  .group-head h3 {
    margin: 0;
    font-size: 0.95rem;
    font-weight: 700;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .count-badge {
    font-size: 11px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--primary) 12%, var(--panel));
    color: var(--primary);
  }
  .entry {
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }
  .entry:last-of-type {
    border-bottom: 0;
    padding-bottom: 0;
  }
  .entry-summary {
    margin: 0 0 4px;
    font-weight: 600;
    font-size: 0.92rem;
  }
  .entry-body {
    margin: 0;
    font-size: 0.88rem;
    line-height: 1.45;
  }
  .entry-meta {
    margin: 4px 0 0;
    font-size: 0.78rem;
    color: var(--subtle);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .feedback-entry .entry-body {
    color: var(--muted);
  }
  .more {
    margin: 4px 0 0;
    font-size: 0.78rem;
    color: var(--subtle);
  }
  .empty {
    text-align: center;
    padding: 28px 20px;
  }
  .empty p {
    margin: 0 0 8px;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
