<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  const GATEKEEPER_ERROR_TYPES = [
    'numeric_drift',
    'unsupported_elaboration',
    'entity_swap',
    'cross_contamination',
    'relational_hallucination',
    'temporal_collapse',
    'hype',
    'speculative_tone',
    'clickbait',
  ] as const

  let anchorsData = $state<Record<string, unknown>>({ count: 0, target: 40, items: [] })
  let reportWrapper = $state<Record<string, unknown> | null>(null)
  let loading = $state(true)
  let running = $state(false)
  let error = $state<string | null>(null)

  let articleId = $state('')
  let factualityFail = $state(false)
  let toneFail = $state(false)
  let selectedTypes = $state<Set<string>>(new Set())

  const anchorCount = $derived(Number(anchorsData.count ?? 0))
  const anchorTarget = $derived(Number(anchorsData.target ?? 40))
  const anchorItems = $derived(
    Array.isArray(anchorsData.items)
      ? (anchorsData.items as Array<Record<string, unknown>>)
      : [],
  )
  const progressPct = $derived(
    anchorTarget === 0 ? 0 : Math.min(100, (anchorCount / anchorTarget) * 100),
  )

  const innerReport = $derived.by(() => {
    if (!reportWrapper || reportWrapper.report == null) return null
    const report = (reportWrapper.report ?? {}) as Record<string, unknown>
    const perTypeRaw = (report.per_type ?? {}) as Record<string, unknown>
    const perType = Object.entries(perTypeRaw).map(([key, val]) => {
      const m = (val ?? {}) as Record<string, unknown>
      return {
        key,
        precision: Number(m.precision ?? 0),
        recall: Number(m.recall ?? 0),
        support: Number(m.support ?? 0),
        trusted: m.trusted === true,
      }
    })
    return {
      computedAt: String(reportWrapper.computed_at ?? '—'),
      gated: report.gated === true,
      nAnchors: Number(report.n_anchors ?? 0),
      factualityAgreement: Number(report.factuality_agreement ?? 0),
      toneAgreement: Number(report.tone_agreement ?? 0),
      trustedTypes: Array.isArray(report.trusted_types)
        ? (report.trusted_types as string[])
        : [],
      perType,
    }
  })

  function errorTypeLabel(value: string): string {
    return value
      .split('_')
      .map((w) => (w ? w[0]!.toUpperCase() + w.slice(1) : w))
      .join(' ')
  }

  function toggleType(type: string) {
    const next = new Set(selectedTypes)
    if (next.has(type)) next.delete(type)
    else next.add(type)
    selectedTypes = next
  }

  async function load() {
    loading = true
    error = null
    try {
      const [anchors, report] = await Promise.all([
        admin.listGatekeeperAnchors(),
        admin.getGatekeeperValidationReport().catch(() => null),
      ])
      anchorsData = anchors as Record<string, unknown>
      reportWrapper =
        report && (report as Record<string, unknown>).report != null
          ? (report as Record<string, unknown>)
          : null
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function runValidation() {
    running = true
    error = null
    try {
      await admin.runGatekeeperValidation()
      onmessage?.('Validation queued — refreshing report shortly…')
      await new Promise((r) => setTimeout(r, 8000))
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      running = false
    }
  }

  async function addAnchor() {
    const id = articleId.trim()
    if (!id) return
    try {
      await admin.addGatekeeperAnchor({
        article_id: id,
        factuality_fail: factualityFail,
        tone_fail: toneFail,
        error_types: [...selectedTypes],
      })
      articleId = ''
      factualityFail = false
      toneFail = false
      selectedTypes = new Set()
      onmessage?.('Anchor added')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    }
  }

  function anchorLabel(a: Record<string, unknown>): string {
    const flags: string[] = []
    if (a.factuality_fail === true) flags.push('factuality')
    if (a.tone_fail === true) flags.push('tone')
    return flags.length ? flags.join(' + ') : 'clean'
  }

  $effect(() => {
    void load()
  })
</script>

<div class="admin-stack">
  {#if error}
    <p class="admin-err">{error}</p>
  {/if}

  {#if loading}
    <div class="progress-line" aria-hidden="true"></div>
  {/if}

  <section class="admin-panel stack">
    <div class="section-head">
      <h3>Validation anchors</h3>
      <strong>{anchorCount} / {anchorTarget}</strong>
    </div>
    <div class="progress-track" role="progressbar" aria-valuenow={anchorCount} aria-valuemin={0} aria-valuemax={anchorTarget}>
      <div class="progress-fill" style="width: {progressPct}%"></div>
    </div>
    <p class="admin-muted">
      Tagged anchors are immutable ground truth used only to validate the LLM annotator — never to
      train. Aim for a diverse set (clean, hallucinated, hyped). Reach ~{anchorTarget}, then run
      validation.
    </p>
  </section>

  <section class="admin-panel stack">
    <div class="section-head">
      <h3>Annotator validation</h3>
      <button class="btn btn-primary compact" type="button" disabled={running} onclick={() => runValidation()}>
        {running ? 'Running…' : 'Run validation'}
      </button>
    </div>

    {#if innerReport}
      <p class="admin-muted small">
        Computed: {innerReport.computedAt} · anchors: {innerReport.nAnchors}
      </p>
      {#if innerReport.gated}
        <p class="admin-alert warn">
          Too few anchors for a reliable report (need ≥20). Trust nothing yet.
        </p>
      {/if}
      <p>
        Fail-flag agreement — factuality {(innerReport.factualityAgreement * 100).toFixed(0)}%, tone
        {(innerReport.toneAgreement * 100).toFixed(0)}%
      </p>
      <p>
        <strong>Trusted error types:</strong>
        {innerReport.trustedTypes.length ? innerReport.trustedTypes.join(', ') : 'none'}
      </p>
      {#if innerReport.perType.length}
        <table class="type-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>P</th>
              <th>R</th>
              <th>n</th>
            </tr>
          </thead>
          <tbody>
            {#each innerReport.perType as row (row.key)}
              <tr>
                <td>
                  <span class="trust-icon" class:trusted={row.trusted} aria-hidden="true">
                    {row.trusted ? '✓' : '○'}
                  </span>
                  {errorTypeLabel(row.key)}
                </td>
                <td>{row.precision.toFixed(2)}</td>
                <td>{row.recall.toFixed(2)}</td>
                <td>{row.support}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    {:else}
      <p class="admin-muted">Not run yet. Tag anchors, then press Run validation.</p>
    {/if}
  </section>

  <section class="admin-panel stack">
    <h3>Tag a published article as anchor</h3>
    <form
      class="stack"
      onsubmit={(e) => {
        e.preventDefault()
        void addAnchor()
      }}
    >
      <label class="field">
        <span>Article ID</span>
        <input bind:value={articleId} placeholder="article UUID" required />
      </label>
      <div class="flag-row">
        <label class="check-row">
          <input type="checkbox" bind:checked={factualityFail} />
          <span>Factuality fail</span>
        </label>
        <label class="check-row">
          <input type="checkbox" bind:checked={toneFail} />
          <span>Tone fail</span>
        </label>
      </div>
      <div class="type-chips">
        {#each GATEKEEPER_ERROR_TYPES as t (t)}
          <button
            type="button"
            class="filter-chip"
            class:active={selectedTypes.has(t)}
            onclick={() => toggleType(t)}
          >
            {errorTypeLabel(t)}
          </button>
        {/each}
      </div>
      <div class="form-actions">
        <button class="btn btn-primary compact" type="submit">Add anchor</button>
      </div>
    </form>
  </section>

  <section class="admin-panel stack">
    <h3>Tagged anchors ({anchorItems.length})</h3>
    {#if anchorItems.length === 0}
      <p class="admin-muted">No anchors yet.</p>
    {:else}
      {#each anchorItems as a (String(a.article_id ?? a.url))}
        {@const types = Array.isArray(a.error_types) ? (a.error_types as string[]) : []}
        {@const label = anchorLabel(a)}
        <div class="anchor-row">
          <div class="anchor-main">
            <span class="anchor-id">{String(a.article_id ?? '—')}</span>
            {#if a.url}
              <span class="anchor-url admin-muted">{String(a.url)}</span>
            {/if}
          </div>
          <span class="anchor-meta">
            {#if a.factuality_fail === true}
              <span class="admin-chip suspect">factuality</span>
            {/if}
            {#if a.tone_fail === true}
              <span class="admin-chip suspect">tone</span>
            {/if}
            {#if types.length}
              {#each types as t (t)}
                <span class="admin-chip">{errorTypeLabel(t)}</span>
              {/each}
            {:else if a.factuality_fail !== true && a.tone_fail !== true}
              <span class="admin-chip">{label}</span>
            {/if}
          </span>
        </div>
      {/each}
    {/if}
  </section>
</div>

<style>
  h3 {
    margin: 0;
    font-size: 1rem;
    font-weight: 700;
  }

  .section-head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: center;
    gap: 10px;
  }

  .progress-line {
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

  .progress-track {
    height: 8px;
    border-radius: 6px;
    background: var(--surface);
    border: 1px solid var(--border);
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: var(--primary);
    border-radius: 6px;
    transition: width 0.2s ease;
  }

  .small {
    margin: 0;
    font-size: 0.92rem;
  }

  .type-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.92rem;
  }

  .type-table th,
  .type-table td {
    padding: 4px 8px;
    text-align: start;
    border-bottom: 1px solid var(--border);
  }

  .type-table th {
    font-size: 11px;
    text-transform: uppercase;
    color: var(--muted);
  }

  .trust-icon {
    display: inline-block;
    width: 16px;
    color: var(--muted);
  }

  .trust-icon.trusted {
    color: var(--gain, #2e7d32);
  }

  .flag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
  }

  .check-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.92rem;
    cursor: pointer;
  }

  .type-chips,
  .form-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .form-actions {
    justify-content: flex-end;
  }

  .filter-chip {
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .filter-chip.active {
    background: var(--accent-soft);
    color: var(--primary);
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
  }

  .compact {
    padding: 8px 14px;
    font-size: 13px;
  }

  .anchor-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: flex-start;
    justify-content: space-between;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
  }

  .anchor-row:last-child {
    border-bottom: 0;
  }

  .anchor-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .anchor-id {
    font-size: 0.92rem;
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .anchor-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
    justify-content: flex-end;
  }

  .anchor-url {
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
