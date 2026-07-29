<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let health = $state<Record<string, unknown> | null>(null)
  let workers = $state<Array<Record<string, unknown>>>([])
  let workersError = $state<string | null>(null)
  let scrapers = $state<Array<Record<string, unknown>>>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let fetchedAt = $state<Date | null>(null)
  let running = $state<Set<string>>(new Set())
  let runningAll = $state(false)
  let backfilling = $state(false)
  let clearingDomains = $state(false)
  let resetting = $state(false)
  let confirmClear = $state(false)
  let confirmReset = $state(false)

  async function load() {
    loading = true
    error = null
    workersError = null
    try {
      const [h, c, s] = await Promise.all([
        admin.healthReady().catch(() => ({ status: 'unreachable' })),
        admin.celeryWorkers().catch((e) => {
          workersError = e instanceof Error ? e.message : String(e)
          return { workers: [] }
        }),
        admin.listScrapers(),
      ])
      health = h as Record<string, unknown>
      workers = Array.isArray(c.workers) ? (c.workers as Array<Record<string, unknown>>) : []
      scrapers = Array.isArray(s.items) ? (s.items as Array<Record<string, unknown>>) : []
      fetchedAt = new Date()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  const checks = $derived(
    Array.isArray(health?.checks) ? (health!.checks as Array<Record<string, unknown>>) : [],
  )

  const overallOk = $derived(health?.status === 'ok')

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

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else}
    {#if health?.status}
      <div class="banner" class:ok={overallOk}>
        {#if overallOk}
          ✓ All systems operational
        {:else}
          Status: {String(health.status)}
        {/if}
      </div>
    {/if}

    <section class="panel">
      <h3>Celery workers</h3>
      {#if workersError}
        <p class="warn-text">Worker ping failed: {workersError}</p>
      {:else if !workers.length}
        <p class="warn-text">
          {loading ? 'Pinging workers…' : 'No Celery workers answered the ping — the worker service may be down.'}
        </p>
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

    {#each checks as c}
      {@const name = String(c.name ?? '')}
      {@const ok = c.ok === true}
      {@const detail = String(c.detail ?? '')}
      {@const chips = name === 'celery_queues' ? parseQueueChips(detail) : null}
      <section class="panel check-card">
        <div class="check-head">
          <span class="dot" class:online={ok}></span>
          <h3>{checkLabel(name)}</h3>
        </div>
        {#if chips}
          <div class="chips">
            {#each chips as chip}
              <span class="chip" class:highlight={chip.depth > 0}>
                {chip.key} {chip.depth}
              </span>
            {/each}
          </div>
        {:else}
          <p class="subtle detail">{detail || (ok ? 'healthy' : 'failing')}</p>
        {/if}
      </section>
    {/each}

    {#if scrapers.length}
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
  {/if}
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
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
