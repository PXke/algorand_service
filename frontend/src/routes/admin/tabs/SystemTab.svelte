<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  type CheckStatus = 'loading' | 'ok' | 'error'
  type CheckState = { status: CheckStatus; ok: boolean; detail: string }

  // Same names/order as backend/app/core/health.py's CHECKS registry — each
  // is fetched from its own endpoint so a slow one (Typesense, the Conduit
  // chain-index query) can't hold up the others.
  const CHECK_NAMES = ['redis', 'cassandra', 'typesense', 'conduit_index', 'celery_queues']

  function pendingCheck(): CheckState {
    return { status: 'loading', ok: false, detail: '' }
  }

  let checks = $state<Record<string, CheckState>>(
    Object.fromEntries(CHECK_NAMES.map((n) => [n, pendingCheck()])),
  )
  let workers = $state<Array<Record<string, unknown>>>([])
  let workersLoading = $state(true)
  let workersError = $state<string | null>(null)
  let scrapers = $state<Array<Record<string, unknown>>>([])
  let scrapersLoading = $state(true)
  let scrapersError = $state<string | null>(null)
  let fetchedAt = $state<Date | null>(null)
  let running = $state<Set<string>>(new Set())
  let runningAll = $state(false)
  let backfilling = $state(false)
  let clearingDomains = $state(false)
  let resetting = $state(false)
  let confirmClear = $state(false)
  let confirmReset = $state(false)

  function loadCheck(name: string): Promise<void> {
    checks[name] = pendingCheck()
    return admin
      .healthCheck(name)
      .then((r) => {
        checks[name] = { status: 'ok', ok: r.ok === true, detail: String(r.detail ?? '') }
      })
      .catch((e) => {
        checks[name] = {
          status: 'error',
          ok: false,
          detail: e instanceof Error ? e.message : String(e),
        }
      })
  }

  function loadWorkers(): Promise<void> {
    workersLoading = true
    workersError = null
    return admin
      .celeryWorkers()
      .then((c) => {
        workers = Array.isArray(c.workers) ? (c.workers as Array<Record<string, unknown>>) : []
      })
      .catch((e) => {
        workersError = e instanceof Error ? e.message : String(e)
      })
      .finally(() => {
        workersLoading = false
      })
  }

  function loadScrapers(): Promise<void> {
    scrapersLoading = true
    scrapersError = null
    return admin
      .listScrapers()
      .then((s) => {
        scrapers = Array.isArray(s.items) ? (s.items as Array<Record<string, unknown>>) : []
      })
      .catch((e) => {
        scrapersError = e instanceof Error ? e.message : String(e)
      })
      .finally(() => {
        scrapersLoading = false
      })
  }

  async function load() {
    // Fire every section's fetch at once; each updates its own state as
    // soon as it resolves instead of waiting on the others. `fetchedAt`
    // just timestamps the toolbar once the whole batch has settled — it
    // doesn't gate any section's render.
    const tasks = [...CHECK_NAMES.map((name) => loadCheck(name)), loadWorkers(), loadScrapers()]
    await Promise.allSettled(tasks)
    fetchedAt = new Date()
  }

  const essentialChecksDone = $derived(
    checks.redis?.status !== 'loading' && checks.cassandra?.status !== 'loading',
  )
  const overallOk = $derived((checks.redis?.ok ?? false) && (checks.cassandra?.ok ?? false))

  function checkLabel(name: string): string {
    if (name === 'celery_queues') return 'Celery queues'
    if (name === 'conduit_index') return 'Conduit chain index'
    return name ? name[0].toUpperCase() + name.slice(1) : name
  }

  function parseQueueChips(detail: string): Array<{ key: string; depth: number }> | null {
    if (!detail.startsWith('total=')) return null
    const parts = detail.replaceAll(',', '').split(' ').filter((p) => p.includes('='))
    return parts.map((p) => {
      const kv = p.split('=')
      return { key: kv[0], depth: Number(kv[1] ?? 0) || 0 }
    })
  }

  async function run(action: string, label?: string) {
    running = new Set([...running, action])
    try {
      const res = await admin.runScraper(action)
      const taskId = String(res.task_id ?? '')
      onmessage?.(
        `Queued "${label ?? action}"${taskId ? ` (task ${taskId.slice(0, 8)}…)` : ''}`,
      )
    } catch (e) {
      onmessage?.(`Failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      const next = new Set(running)
      next.delete(action)
      running = next
    }
  }

  async function runAll() {
    if (!scrapers.length) return
    runningAll = true
    running = new Set(scrapers.map((s) => String(s.action)))
    let queued = 0
    let failure: string | null = null
    for (const s of scrapers) {
      try {
        await admin.runScraper(String(s.action))
        queued++
      } catch (e) {
        failure = e instanceof Error ? e.message : String(e)
      }
    }
    running = new Set()
    runningAll = false
    onmessage?.(
      failure ? `Queued ${queued} tasks; last failure: ${failure}` : `Queued all ${queued} tasks`,
    )
  }

  async function backfill() {
    backfilling = true
    try {
      await admin.backfillArticleTranslations(500)
      onmessage?.(
        'Translation backfill queued (up to 500 articles) — missing locales will fill in over the next few minutes.',
      )
    } catch (e) {
      onmessage?.(`Backfill failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      backfilling = false
    }
  }

  async function clearDomains() {
    clearingDomains = true
    try {
      await admin.clearDomains()
      confirmClear = false
      onmessage?.('Domain frontier cleared')
    } catch (e) {
      onmessage?.(`Clear failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      clearingDomains = false
    }
  }

  async function resetPipeline() {
    resetting = true
    try {
      const res = await admin.resetPipeline()
      confirmReset = false
      const tables = Array.isArray(res.tables) ? res.tables.length : 0
      onmessage?.(`Pipeline reset — cleared ${tables} tables`)
    } catch (e) {
      onmessage?.(`Reset failed: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      resetting = false
    }
  }

  function formatFetched(d: Date): string {
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <p class="intro">
      Live backend readiness — the same checks the deploy pipeline gates on.
    </p>
    <div class="toolbar-right">
      {#if fetchedAt}
        <span class="fetched">as of {formatFetched(fetchedAt)}</span>
      {/if}
      <button class="btn" type="button" onclick={() => load()}>Refresh</button>
    </div>
  </div>

  {#if essentialChecksDone}
    <div class="banner" class:ok={overallOk}>
      {#if overallOk}
        ✓ All systems operational
      {:else}
        Status: degraded
      {/if}
    </div>
  {/if}

  <section class="panel">
    <h3>Celery workers</h3>
    {#if workersError}
      <p class="warn-text">Worker ping failed: {workersError}</p>
    {:else if workersLoading}
      <p class="muted">Pinging workers…</p>
    {:else if !workers.length}
      <p class="warn-text">No Celery workers answered the ping — the worker service may be down.</p>
    {:else}
      {#each workers as w}
        <div class="worker-row">
          <span class="dot" class:online={w.online === true}></span>
          <span class="mono name">{String(w.name ?? '')}</span>
          <span class="subtle">
            {w.online ? `${Number(w.active_tasks ?? 0)} active` : 'offline'}
          </span>
        </div>
      {/each}
    {/if}
  </section>

  {#each CHECK_NAMES as name}
    {@const c = checks[name]}
    {@const chips = c.status === 'ok' && name === 'celery_queues' ? parseQueueChips(c.detail) : null}
    <section class="panel check-card">
      <div class="check-head">
        <span class="dot" class:online={c.status === 'ok' && c.ok} class:pending={c.status === 'loading'}></span>
        <h3>{checkLabel(name)}</h3>
      </div>
      {#if c.status === 'loading'}
        <p class="subtle detail">Checking…</p>
      {:else if chips}
        <div class="chips">
          {#each chips as chip}
            <span class="chip" class:highlight={chip.depth > 0}>
              {chip.key} {chip.depth}
            </span>
          {/each}
        </div>
      {:else}
        <p class="subtle detail">{c.detail || (c.ok ? 'healthy' : 'failing')}</p>
      {/if}
    </section>
  {/each}

  {#if scrapersError}
    <section class="panel">
      <h3>Run tasks now</h3>
      <p class="warn-text">Failed to load scraper actions: {scrapersError}</p>
    </section>
  {:else if scrapersLoading}
    <section class="panel">
      <h3>Run tasks now</h3>
      <p class="muted">Loading…</p>
    </section>
  {:else if scrapers.length}
    <section class="panel stack">
      <div class="section-head">
        <div>
          <h3>Run tasks now</h3>
          <p class="subtle">Queue a worker task immediately instead of waiting for its schedule.</p>
        </div>
        <button class="btn btn-primary" type="button" disabled={runningAll || running.size > 0} onclick={() => runAll()}>
          {runningAll ? 'Queuing…' : 'Run all'}
        </button>
      </div>
      <div class="scraper-grid">
        {#each scrapers as s}
          {@const action = String(s.action ?? '')}
          {@const label = String(s.label ?? action)}
          {@const desc = String(s.description ?? '')}
          {@const busy = running.has(action)}
          <div class="scraper-row" title={desc || undefined}>
            <div>
              <strong>{label}</strong>
              {#if desc}
                <p class="subtle desc">{desc}</p>
              {/if}
            </div>
            <button class="btn btn-sm" type="button" disabled={busy} onclick={() => run(action, label)}>
              {busy ? '…' : 'Run'}
            </button>
          </div>
        {/each}
      </div>
    </section>
  {/if}

  <section class="panel stack">
    <div class="section-head">
      <div>
        <h3>Content localization</h3>
        <p class="subtle">
          Queue missing article translations (فارسی, پښتو, العربية, Русский, …) for stories already on
          the feed.
        </p>
      </div>
      <button class="btn" type="button" disabled={backfilling} onclick={() => backfill()}>
        {backfilling ? 'Queuing…' : 'Backfill translations'}
      </button>
    </div>
  </section>

  <section class="panel stack danger-zone">
    <div class="section-head">
      <div>
        <h3>Beta tools</h3>
        <p class="subtle">Truncate article, publish, crawl, and search state. Irreversible.</p>
      </div>
      <div class="danger-actions">
        <button class="btn danger-outline" type="button" onclick={() => (confirmClear = true)}>
          Clear domains
        </button>
        <button class="btn danger-outline" type="button" onclick={() => (confirmReset = true)}>
          Reset all
        </button>
      </div>
    </div>
  </section>
</div>

{#if confirmClear}
  <div class="overlay" role="dialog" aria-modal="true">
    <div class="dialog panel stack">
      <h3>Clear explored domains?</h3>
      <p>
        Forgets the whole crawl frontier: explored, pending and dead-end domains. The crawler
        re-discovers (and re-holds) them from scratch. The platform blocklist is unaffected.
      </p>
      <div class="form-actions">
        <button class="btn" type="button" onclick={() => (confirmClear = false)}>Cancel</button>
        <button class="btn btn-danger" type="button" disabled={clearingDomains} onclick={() => clearDomains()}>
          {clearingDomains ? 'Clearing…' : 'Clear domains'}
        </button>
      </div>
    </div>
  </div>
{/if}

{#if confirmReset}
  <div class="overlay" role="dialog" aria-modal="true">
    <div class="dialog panel stack">
      <h3>Reset pipeline?</h3>
      <p>
        This wipes all articles, publish queues, crawl queues, and search indexes so the pipeline
        can start fresh.
      </p>
      <p class="subtle">Sources, classifier feedback, and pending reviews are kept.</p>
      <div class="form-actions">
        <button class="btn" type="button" onclick={() => (confirmReset = false)}>Cancel</button>
        <button class="btn btn-danger" type="button" disabled={resetting} onclick={() => resetPipeline()}>
          {resetting ? 'Resetting…' : 'Reset all'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
  }
  .toolbar-right {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }
  .intro {
    margin: 0;
    flex: 1;
    font-size: 0.88rem;
    color: var(--muted);
    line-height: 1.45;
    max-width: 50ch;
  }
  .fetched {
    font-size: 0.78rem;
    color: var(--subtle);
  }
  h3 {
    margin: 0 0 6px;
    font-size: 1rem;
  }
  .banner {
    padding: 12px 14px;
    border-radius: 10px;
    font-weight: 700;
    font-size: 0.92rem;
    border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--border));
    background: color-mix(in srgb, var(--danger) 8%, var(--panel));
    color: var(--danger);
  }
  .banner.ok {
    border-color: color-mix(in srgb, var(--gain) 35%, var(--border));
    background: color-mix(in srgb, var(--gain) 10%, var(--panel));
    color: var(--gain);
  }
  .worker-row {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 0;
    font-size: 0.88rem;
  }
  .worker-row .name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--danger);
    flex-shrink: 0;
  }
  .dot.online {
    background: var(--gain);
  }
  .dot.pending {
    background: var(--subtle);
  }
  .check-card .check-head {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
  }
  .check-card h3 {
    margin: 0;
  }
  .detail {
    margin: 0;
    font-size: 0.88rem;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .chip {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px;
    padding: 4px 8px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
  }
  .chip.highlight {
    background: var(--accent-soft);
    color: var(--primary);
    font-weight: 700;
  }
  .section-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    flex-wrap: wrap;
  }
  .section-head h3 {
    margin: 0;
  }
  .section-head .subtle {
    margin: 2px 0 0;
    font-size: 0.85rem;
  }
  .scraper-grid {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .scraper-row {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 0;
    border-top: 1px solid var(--border);
  }
  .scraper-row:first-child {
    border-top: 0;
    padding-top: 0;
  }
  .desc {
    margin: 4px 0 0;
    font-size: 0.82rem;
    max-width: 48ch;
  }
  .btn-sm {
    padding: 6px 12px;
    font-size: 12.5px;
    flex-shrink: 0;
  }
  .danger-zone {
    border-color: color-mix(in srgb, var(--danger) 30%, var(--border));
  }
  .danger-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .danger-outline {
    color: var(--danger);
    border-color: color-mix(in srgb, var(--danger) 40%, var(--border));
  }
  .btn-danger {
    background: var(--danger);
    color: #fff;
    border-color: var(--danger);
  }
  .warn-text {
    margin: 0;
    font-size: 0.88rem;
    color: var(--danger);
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .overlay {
    position: fixed;
    inset: 0;
    background: color-mix(in srgb, var(--on-surface) 45%, transparent);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    z-index: 200;
  }
  .dialog {
    width: min(480px, 100%);
  }
  .dialog p {
    margin: 0 0 8px;
    line-height: 1.45;
  }
  .form-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 8px;
  }
</style>
