<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  type SessionSummary = Record<string, unknown>
  type SessionDetail = Record<string, unknown>
  type Message = Record<string, unknown>

  const ACTIVE_STATUSES = new Set(['researching', 'writing'])
  const ACTIVE_POLL_MS = 8000
  const IDLE_POLL_MS = 30000

  let { admin }: { admin: AdminApi } = $props()

  let sessions = $state<SessionSummary[]>([])
  /* A growing window rather than appended pages: this tab polls every few
     seconds and replaces the list wholesale, so accumulated pages would be
     wiped by the next refresh. Re-requesting the same total keeps "load more"
     and the poll from fighting each other. */
  const PAGE = 20
  let pageLimit = $state(PAGE)
  let hasMore = $state(false)
  let loadingMore = $state(false)
  let loading = $state(true)
  let error = $state<string | null>(null)

  let expandedIds = $state<Set<string>>(new Set())
  let details = $state<Record<string, SessionDetail>>({})
  let detailLoading = $state<Set<string>>(new Set())
  let detailErrors = $state<Set<string>>(new Set())

  type ChatTurn = { role: 'user' | 'assistant'; content: string }
  let interrogateOpenIds = $state<Set<string>>(new Set())
  let interrogateHistory = $state<Record<string, ChatTurn[]>>({})
  let interrogateDraft = $state<Record<string, string>>({})
  let interrogateGroundTruth = $state<Record<string, boolean>>({})
  let interrogateLoading = $state<Set<string>>(new Set())
  let interrogateError = $state<Record<string, string>>({})

  let recomposingSessionIds = $state<Set<string>>(new Set())
  let recomposedSessionIds = $state<Set<string>>(new Set())
  let recomposeSessionError = $state<Record<string, string>>({})

  const hasActiveSession = $derived(
    sessions.some((s) => ACTIVE_STATUSES.has(String(s.status ?? ''))),
  )

  const pollInterval = $derived(hasActiveSession ? ACTIVE_POLL_MS : IDLE_POLL_MS)

  /* No client-side cap: the server decides how many rows come back (pageLimit),
     and a slice here silently discarded every extra page — "load more" fetched
     40 and then rendered the same 20. */
  const visibleSessions = $derived(sessions)

  function sessionId(s: SessionSummary): string {
    return String(s.session_id ?? '')
  }

  function formatDuration(ms: number): string {
    if (ms <= 0) return '0s'
    const sec = ms / 1000
    if (sec < 60) return `${sec.toFixed(1)}s`
    const min = Math.floor(sec / 60)
    const rem = Math.round(sec % 60)
    return rem > 0 ? `${min}m ${rem}s` : `${min}m`
  }

  function shortDate(iso: string): string {
    if (!iso) return '—'
    return iso.replace('T', ' ').split('.')[0] ?? iso
  }

  function statusChipClass(status: string): string {
    if (ACTIVE_STATUSES.has(status)) return 'admin-chip active'
    // "ok" itself is finalized after the fact into the real publish decision
    // (see finalize_compose_session_outcome) -- a plain "ok" surviving here
    // means the compose succeeded but no decision has landed yet (or ever
    // will, e.g. a manual/legacy session predating this).
    if (status === 'ok' || status === 'done' || status === 'published') {
      return 'admin-chip ok'
    }
    if (status === 'on_hold') return 'admin-chip hold'
    if (status === 'error' || status === 'failed' || status.startsWith('rejected')) {
      return 'admin-chip suspect'
    }
    return 'admin-chip'
  }

  function roleColor(role: string): string {
    switch (role) {
      case 'user':
        return 'var(--gain)'
      case 'assistant':
        return 'var(--primary)'
      case 'tool':
        return '#b7791f'
      case 'system':
        return 'var(--muted)'
      default:
        return 'var(--subtle)'
    }
  }

  function truncateJson(value: unknown, max = 120): string {
    const text =
      typeof value === 'string' ? value : value == null ? '' : JSON.stringify(value)
    if (text.length <= max) return text
    return `${text.slice(0, max)}…`
  }

  function toolCallLabel(tc: Record<string, unknown>): string {
    const name = String(tc.name ?? tc.method ?? tc.function ?? 'tool')
    const args = tc.arguments ?? tc.args ?? tc.input ?? {}
    return `→ ${name}(${truncateJson(args)})`
  }

  function messagesFor(detail: SessionDetail | undefined): Message[] {
    if (!detail) return []
    const raw = detail.messages
    if (!Array.isArray(raw)) return []
    return raw.filter((m): m is Message => m != null && typeof m === 'object')
  }

  function finalOutputFor(detail: SessionDetail | undefined): string {
    if (!detail) return ''
    const explicit = String(detail.final_output ?? '').trim()
    if (explicit) return explicit
    const msgs = messagesFor(detail)
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i]
      if (String(m.role ?? '') === 'assistant') {
        const content = String(m.content ?? '').trim()
        if (content) return content
      }
    }
    return ''
  }

  async function loadDetail(sessionId: string, createdAt: string, force = false) {
    if (!sessionId || !createdAt) return
    if (detailLoading.has(sessionId)) return
    if (!force && details[sessionId]) return

    detailLoading = new Set([...detailLoading, sessionId])
    detailErrors = new Set([...detailErrors].filter((id) => id !== sessionId))
    try {
      const detail = (await admin.getComposeSessionDetail(sessionId, createdAt)) as SessionDetail
      details = { ...details, [sessionId]: detail }
    } catch {
      detailErrors = new Set([...detailErrors, sessionId])
    } finally {
      const next = new Set(detailLoading)
      next.delete(sessionId)
      detailLoading = next
    }
  }

  function refreshExpandedActiveDetails(list: SessionSummary[]) {
    for (const s of list) {
      const id = sessionId(s)
      const createdAt = String(s.created_at ?? '')
      if (!id || !createdAt) continue
      if (!expandedIds.has(id)) continue
      if (!ACTIVE_STATUSES.has(String(s.status ?? ''))) continue
      void loadDetail(id, createdAt, true)
    }
  }

  async function load(showSpinner = true) {
    if (showSpinner) loading = true
    error = null
    try {
      const res = await admin.listComposeSessions({ limit: pageLimit })
      const items = Array.isArray(res.items) ? (res.items as SessionSummary[]) : []
      sessions = items
      hasMore = items.length >= pageLimit
      refreshExpandedActiveDetails(items)
    } catch (e) {
      if (showSpinner) error = e instanceof Error ? e.message : String(e)
    } finally {
      if (showSpinner) loading = false
    }
  }

  async function quietReload() {
    try {
      const res = await admin.listComposeSessions({ limit: pageLimit })
      const items = Array.isArray(res.items) ? (res.items as SessionSummary[]) : []
      sessions = items
      hasMore = items.length >= pageLimit
      refreshExpandedActiveDetails(items)
    } catch {
      // Silent — next poll retries; manual refresh surfaces errors.
    }
  }

  function toggleSession(s: SessionSummary) {
    const id = sessionId(s)
    const createdAt = String(s.created_at ?? '')
    if (!id) return

    if (expandedIds.has(id)) {
      const next = new Set(expandedIds)
      next.delete(id)
      expandedIds = next
      return
    }

    expandedIds = new Set([...expandedIds, id])
    void loadDetail(id, createdAt)
  }

  function isExpanded(s: SessionSummary): boolean {
    return expandedIds.has(sessionId(s))
  }

  function toggleInterrogate(id: string) {
    const next = new Set(interrogateOpenIds)
    if (next.has(id)) {
      next.delete(id)
    } else {
      next.add(id)
      if (!(id in interrogateGroundTruth)) {
        interrogateGroundTruth = { ...interrogateGroundTruth, [id]: true }
      }
    }
    interrogateOpenIds = next
  }

  async function askInterrogation(id: string) {
    const question = (interrogateDraft[id] ?? '').trim()
    if (!question || interrogateLoading.has(id)) return

    const priorHistory = interrogateHistory[id] ?? []
    interrogateHistory = { ...interrogateHistory, [id]: [...priorHistory, { role: 'user', content: question }] }
    interrogateDraft = { ...interrogateDraft, [id]: '' }
    interrogateLoading = new Set(interrogateLoading).add(id)
    interrogateError = { ...interrogateError, [id]: '' }

    try {
      const res = await admin.interrogateComposeSession(id, {
        question,
        history: priorHistory,
        ground_truth: interrogateGroundTruth[id] ?? true,
      })
      const answer = String(res.answer ?? '')
      const fallbackTurn: ChatTurn = { role: 'assistant', content: answer }
      const nextHistory: ChatTurn[] = Array.isArray(res.history)
        ? (res.history as ChatTurn[])
        : [...(interrogateHistory[id] ?? []), fallbackTurn]
      interrogateHistory = { ...interrogateHistory, [id]: nextHistory }
    } catch (e) {
      // Roll back the optimistic user turn so a retry doesn't duplicate it.
      interrogateHistory = { ...interrogateHistory, [id]: priorHistory }
      interrogateDraft = { ...interrogateDraft, [id]: question }
      interrogateError = {
        ...interrogateError,
        [id]: e instanceof Error ? e.message : String(e),
      }
    } finally {
      const next = new Set(interrogateLoading)
      next.delete(id)
      interrogateLoading = next
    }
  }

  async function recomposeSession(id: string, sourceUrl: string) {
    if (!sourceUrl) {
      recomposeSessionError = { ...recomposeSessionError, [id]: 'session has no source_url' }
      return
    }
    if (
      !confirm(
        'Recompose the live article for this source now? This spends real Mistral usage ' +
          'and can take several minutes (longer for a special edition).',
      )
    ) {
      return
    }
    recomposingSessionIds = new Set(recomposingSessionIds).add(id)
    recomposeSessionError = { ...recomposeSessionError, [id]: '' }
    try {
      await admin.recomposeSession(id, sourceUrl)
      recomposedSessionIds = new Set(recomposedSessionIds).add(id)
    } catch (e) {
      recomposeSessionError = {
        ...recomposeSessionError,
        [id]: e instanceof Error ? e.message : String(e),
      }
    } finally {
      const next = new Set(recomposingSessionIds)
      next.delete(id)
      recomposingSessionIds = next
    }
  }

  $effect(() => {
    admin
    void load()
  })

  $effect(() => {
    const interval = pollInterval
    let timer: ReturnType<typeof setTimeout> | undefined
    let cancelled = false

    function schedule() {
      timer = setTimeout(async () => {
        if (cancelled) return
        await quietReload()
        if (!cancelled) schedule()
      }, interval)
    }

    schedule()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  })
</script>

<div class="tab admin-stack">
  <div class="admin-toolbar">
    <p class="admin-muted intro">
      Recent article composes — expand one for its full transcript (prompts, tool calls, output).
      Newest first, showing {sessions.length}.
    </p>
    <button class="btn" type="button" disabled={loading} onclick={() => load()}>Refresh</button>
  </div>

  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else if visibleSessions.length === 0}
    <div class="admin-panel empty">
      <h3>No compose sessions yet</h3>
      <p class="admin-muted">
        Once the writer composes an article, its full transcript shows up here. Nothing composed
        recently.
      </p>
    </div>
  {:else}
    {#each visibleSessions as s (sessionId(s))}
      {@const id = sessionId(s)}
      {@const createdAt = String(s.created_at ?? '')}
      {@const source = String(s.source_url ?? '').trim()}
      {@const status = String(s.status ?? '')}
      {@const model = String(s.model ?? '')}
      {@const rounds = Number(s.rounds ?? 0)}
      {@const toolCalls = Number(s.tool_calls ?? 0)}
      {@const durationMs = Number(s.duration_ms ?? 0)}
      {@const totalTokens = Number(s.total_tokens ?? 0)}
      {@const expanded = isExpanded(s)}
      {@const detail = details[id]}
      {@const detailBusy = detailLoading.has(id)}
      {@const detailFailed = detailErrors.has(id)}

      <section class="admin-panel session-card">
        <button class="session-head" type="button" onclick={() => toggleSession(s)}>
          <div class="session-copy">
            <strong class="session-source">{source || '(no source)'}</strong>
            <p class="admin-muted session-meta">
              {shortDate(createdAt)}
              {#if model} · {model}{/if}
              · {rounds}r · {toolCalls} tools · {formatDuration(durationMs)}
              {#if totalTokens > 0} · {totalTokens}tok{/if}
            </p>
          </div>
          <span class={statusChipClass(status)}>{status || 'unknown'}</span>
        </button>

        {#if expanded}
          <div class="session-detail">
            {#if detailBusy && !detail}
              <p class="admin-muted">Loading transcript…</p>
            {:else if detailFailed}
              <p class="admin-err">Failed to load transcript — try re-expanding.</p>
            {:else if detail}
              {#each messagesFor(detail) as msg, i}
                {@const role = String(msg.role ?? 'unknown')}
                {@const name = String(msg.name ?? '').trim()}
                {@const content = String(msg.content ?? '').trim()}
                {@const msgToolCalls = Array.isArray(msg.tool_calls)
                  ? (msg.tool_calls as Record<string, unknown>[])
                  : []}
                <article class="msg">
                  <p class="msg-role" style:color={roleColor(role)}>
                    {(name ? `${role} · ${name}` : role).toUpperCase()}
                  </p>
                  {#each msgToolCalls as tc}
                    {#if tc && typeof tc === 'object'}
                      <pre class="msg-mono tool">{toolCallLabel(tc as Record<string, unknown>)}</pre>
                    {/if}
                  {/each}
                  {#if content}
                    <pre class="msg-mono">{content}</pre>
                  {/if}
                </article>
              {/each}

              {#if finalOutputFor(detail)}
                <section class="final-output">
                  <p class="final-label">Final output</p>
                  <pre class="msg-mono accent">{finalOutputFor(detail)}</pre>
                </section>
              {/if}

              {@const interrogateOpen = interrogateOpenIds.has(id)}
              {@const chatHistory = interrogateHistory[id] ?? []}
              {@const asking = interrogateLoading.has(id)}
              {@const askError = interrogateError[id]}
              {@const recomposing = recomposingSessionIds.has(id)}
              {@const recomposed = recomposedSessionIds.has(id)}
              {@const recomposeErr = recomposeSessionError[id]}
              <section class="interrogate">
                <div class="session-actions">
                  <button
                    class="btn compact"
                    type="button"
                    onclick={() => toggleInterrogate(id)}
                  >
                    {interrogateOpen ? 'Hide interrogation' : 'Interrogate this session'}
                  </button>
                  <button
                    class="btn compact btn-danger"
                    type="button"
                    disabled={recomposing || recomposed || !source}
                    title={source ? '' : 'no source_url on this session'}
                    onclick={() => recomposeSession(id, source)}
                  >
                    {#if recomposing}
                      Triggering…
                    {:else if recomposed}
                      Triggered ✓
                    {:else}
                      Recompose this
                    {/if}
                  </button>
                </div>
                {#if recomposeErr}
                  <p class="admin-err">{recomposeErr}</p>
                {/if}

                {#if interrogateOpen}
                  <div class="interrogate-panel">
                    <p class="admin-muted small">
                      Revives this session's transcript into the same model that composed it and
                      asks it your question. Its answers are leads to verify against the trace
                      and live sources — an LLM cannot truly introspect its past reasoning, and
                      under pressure it rationalises or capitulates rather than telling you it
                      doesn't know.
                    </p>
                    <label class="ground-truth-toggle">
                      <input
                        type="checkbox"
                        checked={interrogateGroundTruth[id] ?? true}
                        onchange={(e) =>
                          (interrogateGroundTruth = {
                            ...interrogateGroundTruth,
                            [id]: (e.target as HTMLInputElement).checked,
                          })}
                      />
                      Confront with live ground truth (DNS on linked domains + its own transcript's
                      fetch failures)
                    </label>

                    {#if chatHistory.length > 0}
                      <div class="interrogate-history">
                        {#each chatHistory as turn}
                          <p class="chat-turn" class:assistant={turn.role === 'assistant'}>
                            <span class="chat-role">{turn.role === 'user' ? 'you' : 'writer'}</span>
                            {turn.content}
                          </p>
                        {/each}
                      </div>
                    {/if}

                    {#if askError}
                      <p class="admin-err">{askError}</p>
                    {/if}

                    <div class="interrogate-input">
                      <textarea
                        rows="2"
                        placeholder="e.g. where did the '1,000 issuers' figure come from?"
                        value={interrogateDraft[id] ?? ''}
                        oninput={(e) =>
                          (interrogateDraft = {
                            ...interrogateDraft,
                            [id]: (e.target as HTMLTextAreaElement).value,
                          })}
                        onkeydown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault()
                            void askInterrogation(id)
                          }
                        }}
                      ></textarea>
                      <button
                        class="btn compact"
                        type="button"
                        disabled={asking || !(interrogateDraft[id] ?? '').trim()}
                        onclick={() => askInterrogation(id)}
                      >
                        {asking ? 'Asking…' : 'Ask'}
                      </button>
                    </div>
                  </div>
                {/if}
              </section>
            {/if}
          </div>
        {/if}
      </section>
    {/each}

    {#if hasMore}
      <button
        class="load-more"
        type="button"
        disabled={loadingMore}
        onclick={async () => {
          loadingMore = true
          pageLimit += PAGE
          try {
            await load(false)
          } finally {
            loadingMore = false
          }
        }}
      >
        {loadingMore ? 'Loading…' : `Load ${PAGE} more`}
      </button>
    {/if}
  {/if}
</div>

<style>
  .load-more {
    display: block;
    width: 100%;
    margin-top: 12px;
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--panel);
    color: var(--on-surface);
    font-size: 0.9rem;
    font-weight: 600;
  }
  .load-more:hover:not(:disabled) {
    background: var(--accent-soft);
  }
  .load-more:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .tab {
    gap: 14px;
  }

  .intro {
    flex: 1;
    min-width: 220px;
    margin: 0;
  }

  .empty h3 {
    margin: 0 0 6px;
  }

  .session-card {
    padding: 0;
    overflow: hidden;
  }

  .session-head {
    width: 100%;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    padding: 12px 14px;
    border: 0;
    background: transparent;
    color: inherit;
    text-align: start;
    cursor: pointer;
    font: inherit;
  }

  .session-head:hover {
    background: color-mix(in srgb, var(--on-surface) 3%, transparent);
  }

  .session-copy {
    min-width: 0;
    flex: 1;
  }

  .session-source {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.92rem;
  }

  .session-meta {
    margin: 4px 0 0;
    font-size: 11px;
  }

  .admin-chip.active {
    background: color-mix(in srgb, var(--primary) 16%, transparent);
  }

  .admin-chip.ok {
    background: color-mix(in srgb, var(--gain) 12%, transparent);
    color: var(--gain);
    border-color: color-mix(in srgb, var(--gain) 45%, transparent);
  }

  .admin-chip.hold {
    background: color-mix(in srgb, #b7791f 12%, transparent);
    color: #b7791f;
    border-color: color-mix(in srgb, #b7791f 45%, transparent);
  }

  .session-detail {
    border-top: 1px solid var(--border);
    padding: 0 14px 14px;
  }

  .msg {
    margin-top: 10px;
  }

  .msg-role {
    margin: 0 0 3px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
  }

  .msg-mono {
    margin: 0 0 4px;
    padding: 8px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--on-surface) 4%, var(--panel));
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-x: auto;
  }

  .msg-mono.tool {
    color: var(--primary);
  }

  .msg-mono.accent {
    color: var(--primary);
  }

  .final-output {
    margin-top: 10px;
  }

  .final-label {
    margin: 0 0 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: var(--primary);
  }

  .interrogate {
    margin-top: 12px;
    border-top: 1px solid var(--border);
    padding-top: 12px;
  }

  .session-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .interrogate-panel {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .ground-truth-toggle {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    font-size: 11px;
    color: var(--subtle);
  }

  .ground-truth-toggle input {
    margin-top: 2px;
  }

  .interrogate-history {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 320px;
    overflow-y: auto;
  }

  .chat-turn {
    margin: 0;
    padding: 8px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--on-surface) 4%, var(--panel));
    font-size: 12px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .chat-turn.assistant {
    background: color-mix(in srgb, var(--primary) 8%, var(--panel));
  }

  .chat-role {
    margin-right: 6px;
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--primary);
  }

  .interrogate-input {
    display: flex;
    gap: 8px;
    align-items: flex-end;
  }

  .interrogate-input textarea {
    flex: 1;
    resize: vertical;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--panel);
    color: var(--on-surface);
    padding: 8px;
    font: inherit;
    font-size: 12px;
  }
</style>
