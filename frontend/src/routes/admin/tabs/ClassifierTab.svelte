<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  type ReviewItem = Record<string, unknown>
  type Quality = 'high' | 'medium' | 'low' | 'spam'
  type Finding = Record<string, unknown>

  const QUALITIES: Quality[] = ['high', 'medium', 'low', 'spam']

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  let reviews = $state<ReviewItem[]>([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let composing = $state(false)
  let recomposingId = $state<string | null>(null)
  let pending = $state(false)
  let trainingMode = $state(false)

  let quality = $state<Quality>('medium')
  let sourceRelevant = $state(true)
  let editedScores = $state<Record<string, number>>({})

  let investigationOpen = $state(false)
  let investigationLoading = $state(false)
  let investigationFindings = $state<Finding[] | null>(null)

  const current = $derived(reviews[0] ?? null)
  const waitingCount = $derived(Math.max(0, reviews.length - 1))

  const subscores = $derived.by(() => {
    const detail = current?.grade_detail
    if (!detail || typeof detail !== 'object') return {} as Record<string, number>
    const d = detail as Record<string, unknown>
    const raw = (d.subscores ?? d.scores) as Record<string, unknown> | undefined
    if (!raw || typeof raw !== 'object') return {} as Record<string, number>
    const out: Record<string, number> = {}
    for (const [k, v] of Object.entries(raw)) {
      if (typeof v === 'number') out[k] = v
    }
    return out
  })

  const issues = $derived.by(() => {
    const detail = current?.grade_detail
    if (!detail || typeof detail !== 'object') return [] as string[]
    const d = detail as Record<string, unknown>
    if (!Array.isArray(d.issues)) return [] as string[]
    return d.issues.map((i) => String(i))
  })

  const predictedCategories = $derived.by(() => {
    if (!current) return [] as string[]
    const raw = current.categories
    if (Array.isArray(raw) && raw.length > 0) {
      return raw.map((c) => String(c).toLowerCase())
    }
    const pred = String(current.predicted_category ?? current.category ?? 'generic').toLowerCase()
    return [pred]
  })

  function itemKey(item: ReviewItem): string {
    return String(item.review_id ?? item.url ?? '')
  }

  function autoScore100(dim: string): number {
    const raw = subscores[dim] ?? 0
    // Subscores are 0–1; scores may already be 0–100.
    const scaled = raw <= 1 ? raw * 100 : raw
    return Math.round(Math.min(100, Math.max(0, scaled)))
  }

  function scoreValue(dim: string): number {
    return editedScores[dim] ?? autoScore100(dim)
  }

  function setScore(dim: string, value: number) {
    const clamped = Math.min(100, Math.max(0, Math.round(value)))
    editedScores = { ...editedScores, [dim]: clamped }
  }

  function buildCorrectedScores(): Record<string, number> | undefined {
    const corrected: Record<string, number> = {}
    for (const dim of Object.keys(subscores)) {
      const currentVal = scoreValue(dim)
      const auto = autoScore100(dim)
      if (Math.abs(currentVal - auto) >= 5) corrected[dim] = currentVal / 10
    }
    return Object.keys(corrected).length > 0 ? corrected : undefined
  }

  function resetItemState() {
    quality = 'medium'
    sourceRelevant = true
    editedScores = {}
    investigationOpen = false
    investigationFindings = null
    investigationLoading = false
  }

  async function load() {
    loading = true
    error = null
    try {
      const res = await admin.listClassifierReviews()
      const items = Array.isArray(res.items) ? (res.items as ReviewItem[]) : []
      items.sort(
        (a, b) =>
          (Number(b.storage_score) || 0) - (Number(a.storage_score) || 0),
      )
      reviews = items
      resetItemState()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function composeNext() {
    composing = true
    try {
      const result = (await admin.composeNextReview()) as Record<string, unknown>
      const triggered = result.triggered === true
      const message = triggered
        ? 'Pulling the top topic — it will appear shortly'
        : String(result.message ?? 'Skipped — nothing to pull right now')
      onmessage?.(message)
      if (!triggered) return
      await new Promise((r) => setTimeout(r, 6000))
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      composing = false
    }
  }

  async function clearQueue() {
    if (reviews.length === 0) return
    const confirmed = confirm(
      `Clear review queue?\n\nDiscards all ${reviews.length} pending items without recording feedback. Labels you already submitted are kept.`,
    )
    if (!confirmed) return
    loading = true
    try {
      await admin.clearClassifierReviews()
      onmessage?.('Review queue cleared')
      await load()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
      loading = false
    }
  }

  async function recompose() {
    if (!current) return
    const reviewId = String(current.review_id ?? '')
    if (!reviewId) return
    recomposingId = reviewId
    try {
      await admin.recomposeReview({ review_id: reviewId })
      onmessage?.(
        'Recomposing — writer loop takes a few minutes; fresh draft replaces this item when ready',
      )
      await new Promise((r) => setTimeout(r, 2000))
      await load()
      for (let i = 0; i < 20; i++) {
        const hasNew = reviews.some((it) => String(it.review_id ?? '') !== reviewId)
        if (hasNew) break
        await new Promise((r) => setTimeout(r, 15000))
        await load()
      }
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      recomposingId = null
    }
  }

  async function decide(approved: boolean) {
    if (!current || pending) return
    pending = true
    error = null
    try {
      const cats = [...predictedCategories].sort()
      const corrected = buildCorrectedScores()
      await admin.submitClassifierFeedback({
        url: String(current.url ?? ''),
        text_sample: String(current.page_text_preview ?? ''),
        category: cats[0] ?? 'generic',
        categories: cats,
        predicted_category: String(
          current.predicted_category ?? current.category ?? 'generic',
        ).toLowerCase(),
        quality,
        source_relevant: sourceRelevant,
        predicted_publish: false,
        approved,
        training_only: trainingMode,
        review_id: current.review_id,
        article_id: current.article_id,
        ...(corrected ? { corrected_scores: corrected } : {}),
      })
      onmessage?.(approved ? 'Approved' : 'Rejected')
      reviews = reviews.filter((r) => itemKey(r) !== itemKey(current))
      resetItemState()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      pending = false
    }
  }

  async function loadInvestigation() {
    if (!current) return
    const url = String(current.url ?? '')
    if (!url) return
    investigationLoading = true
    try {
      const res = await admin.investigationFindings(url)
      investigationFindings = Array.isArray(res.items)
        ? (res.items as Finding[])
        : []
    } catch {
      investigationFindings = []
    } finally {
      investigationLoading = false
    }
  }

  function toggleInvestigation() {
    investigationOpen = !investigationOpen
    if (investigationOpen && investigationFindings === null && !investigationLoading) {
      void loadInvestigation()
    }
  }

  function summarizeResult(result: unknown): string {
    if (!result || typeof result !== 'object') return String(result ?? '')
    const r = result as Record<string, unknown>
    if (r.error != null) return `error: ${String(r.error)}`
    const parts: string[] = []
    for (const [k, v] of Object.entries(r)) {
      if (v == null || v === '' || v === false) continue
      if (Array.isArray(v) && v.length === 0) continue
      const vs =
        typeof v === 'object'
          ? `${Array.isArray(v) ? v.length : ''} ${Array.isArray(v) ? 'items' : 'object'}`
          : String(v)
      parts.push(`${k}: ${vs}`)
    }
    return parts.slice(0, 6).join(' · ')
  }

  function gradeColor(grade: number): string {
    if (grade >= 7) return 'var(--gain)'
    if (grade >= 5) return '#b7791f'
    return 'var(--danger)'
  }

  function tagsFor(item: ReviewItem): string[] {
    const raw = item.tags
    if (!Array.isArray(raw)) return []
    return raw.map((t) => String(t))
  }

  $effect(() => {
    admin
    void load()
  })
</script>

<div class="tab admin-stack">
  <label class="training-toggle field">
    <span>
      <strong>Training mode</strong>
      <span class="admin-muted">Label only — don't publish accepted articles</span>
    </span>
    <input type="checkbox" bind:checked={trainingMode} />
  </label>

  <div class="admin-toolbar">
    <p class="admin-muted intro">
      {#if reviews.length === 0}
        Article proposals land here before publishing.
      {:else}
        Article proposal 1 of {reviews.length} — sorted by interest score.
      {/if}
    </p>
    <div class="toolbar-actions">
      <button
        class="btn"
        type="button"
        disabled={loading || composing}
        onclick={() => composeNext()}
      >
        {composing ? 'Pulling…' : 'Pull top topic'}
      </button>
      {#if reviews.length > 0}
        <button class="btn btn-danger" type="button" disabled={loading} onclick={() => clearQueue()}>
          Clear queue
        </button>
      {/if}
      <button class="btn" type="button" disabled={loading} onclick={() => load()}>Refresh</button>
    </div>
  </div>

  {#if loading}
    <p class="admin-muted">Loading…</p>
  {:else if error}
    <p class="admin-err">{error}</p>
  {:else if !current}
    <div class="admin-panel empty">
      <h3>Review queue is clear</h3>
      <p class="admin-muted">Nothing is pending classifier review right now.</p>
    </div>
  {:else}
    <article class="admin-panel review-card">
      <h3 class="review-title">
        {String(current.page_title ?? current.article_title ?? current.url ?? '')}
      </h3>
      <a class="review-url" href={String(current.url)} target="_blank" rel="noopener noreferrer">
        {String(current.url ?? '')}
      </a>

      {#if typeof current.grade === 'number'}
        <section class="grade-block">
          <div class="grade-head">
            <span class="admin-chip grade-chip" style:color={gradeColor(current.grade)} style:border-color={gradeColor(current.grade)}>
              Grade {Number(current.grade).toFixed(1)}/10
            </span>
            <span class="admin-muted grade-hint">Adjust a score if the auto-grade is wrong</span>
          </div>
          {#each Object.keys(subscores) as dim}
            {@const auto = autoScore100(dim)}
            {@const val = scoreValue(dim)}
            {@const edited = editedScores[dim] != null && Math.abs(val - auto) >= 5}
            <div class="score-row">
              <label class="score-label" for="score-{dim}">{dim}</label>
              <input
                id="score-{dim}"
                class="score-range"
                type="range"
                min="0"
                max="100"
                step="1"
                value={val}
                disabled={pending}
                oninput={(e) => setScore(dim, Number(e.currentTarget.value))}
              />
              <input
                class="score-input"
                type="number"
                min="0"
                max="100"
                step="1"
                value={val}
                disabled={pending}
                oninput={(e) => setScore(dim, Number(e.currentTarget.value))}
              />
              {#if edited}
                <span class="admin-chip">edited</span>
              {/if}
            </div>
          {/each}
          {#if issues.length > 0}
            <ul class="issues">
              {#each issues as issue}
                <li>{issue}</li>
              {/each}
            </ul>
          {/if}
        </section>
      {/if}

      {#if String(current.hold_reason ?? '').trim()}
        <div class="admin-alert warn hold-banner">
          <strong
            >Held by {String(current.diverted_by ?? 'gate').trim() || 'gate'}</strong
          >
          <p>{String(current.hold_reason)}</p>
        </div>
      {/if}

      {#if String(current.article_title ?? '').trim()}
        <section class="article-preview">
          <p class="preview-label">Composed article (held — approve to publish)</p>
          <h4>{String(current.article_title)}</h4>
          {#if String(current.article_summary ?? '').trim()}
            <p class="admin-muted">{String(current.article_summary)}</p>
          {/if}
          {#if tagsFor(current).length > 0}
            <div class="tag-row">
              {#each tagsFor(current) as tag}
                <span class="admin-chip">#{tag}</span>
              {/each}
            </div>
          {/if}
          {#if String(current.article_body ?? '').trim()}
            <pre class="text-preview article-body">{String(current.article_body)}</pre>
          {/if}
        </section>
      {/if}

      {#if String(current.page_text_preview ?? '').trim()}
        <p class="preview-label source-label">Crawled source page (context, not the article)</p>
        <pre class="text-preview">{String(current.page_text_preview)}</pre>
      {/if}

      <div class="meta-row">
        <span class="admin-chip">
          Predicted: {predictedCategories.join(', ')}
          {#if current.storage_score != null}
            · score {String(current.storage_score)}
          {/if}
          {#if typeof current.confidence === 'number'}
            · confidence {Math.round(Number(current.confidence) * 100)}%
          {/if}
        </span>
      </div>

      <div class="source-toggle panel">
        <div>
          <strong>Source worth watching?</strong>
          <p class="admin-muted">
            Off = mark this domain a dead end. Rejecting a weak article alone keeps a good source
            alive.
          </p>
        </div>
        <input type="checkbox" bind:checked={sourceRelevant} disabled={pending} />
      </div>

      <section class="quality-block">
        <strong>Article quality</strong>
        <div class="quality-chips">
          {#each QUALITIES as level}
            <button
              type="button"
              class="admin-chip quality-chip"
              class:active={quality === level}
              disabled={pending}
              onclick={() => (quality = level)}
            >
              {level}
            </button>
          {/each}
        </div>
      </section>

      <details class="investigation" open={investigationOpen}>
        <summary onclick={(e) => { e.preventDefault(); toggleInvestigation() }}>
          Investigation evidence
          <span class="admin-muted">Tools the agent called to verify this story</span>
        </summary>
        {#if investigationLoading}
          <p class="admin-muted">Loading findings…</p>
        {:else if (investigationFindings ?? []).length === 0}
          <p class="admin-muted">No tool calls recorded for this article.</p>
        {:else}
          {#each investigationFindings ?? [] as finding}
            <div class="finding">
              <strong>{String(finding.tool ?? 'tool')}</strong>
              <p class="admin-muted">{summarizeResult(finding.result)}</p>
            </div>
          {/each}
        {/if}
      </details>

      <div class="decision-row">
        {#if current.review_id}
          <button
            class="btn"
            type="button"
            disabled={pending || recomposingId != null}
            onclick={() => recompose()}
          >
            {recomposingId === String(current.review_id) ? 'Recomposing…' : 'Recompose'}
          </button>
        {/if}
        <div class="decision-actions">
          <button class="btn btn-danger" type="button" disabled={pending} onclick={() => decide(false)}>
            Reject
          </button>
          <button class="btn btn-primary" type="button" disabled={pending} onclick={() => decide(true)}>
            {pending ? 'Saving…' : 'Approve'}
          </button>
        </div>
      </div>
    </article>

    {#if waitingCount > 0}
      <p class="admin-muted waiting">{waitingCount} more waiting after this one</p>
    {/if}
  {/if}
</div>

<style>
  .tab {
    gap: 14px;
  }

  .training-toggle {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin: 0;
  }

  .training-toggle span {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .intro {
    flex: 1;
    min-width: 200px;
    margin: 0;
  }

  .toolbar-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }

  .empty h3 {
    margin: 0 0 6px;
  }

  .review-card {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .review-title {
    margin: 0;
    font-size: 1.05rem;
    line-height: 1.35;
  }

  .review-url {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11px;
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .grade-block {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .grade-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }

  .grade-chip {
    font-size: 12px;
  }

  .grade-hint {
    font-size: 11px;
    font-style: italic;
    margin: 0;
  }

  .score-row {
    display: grid;
    grid-template-columns: 88px 1fr 52px auto;
    gap: 8px;
    align-items: center;
  }

  .score-label {
    font-size: 12px;
    color: var(--muted);
    text-transform: capitalize;
  }

  .score-range {
    width: 100%;
  }

  .score-input {
    width: 52px;
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 4px 6px;
    font-size: 12px;
    background: var(--panel);
    color: var(--on-surface);
  }

  .issues {
    margin: 4px 0 0;
    padding-left: 18px;
    color: var(--danger);
    font-size: 12px;
  }

  .hold-banner p {
    margin: 4px 0 0;
    font-weight: 400;
  }

  .article-preview {
    padding: 12px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--primary) 6%, var(--panel));
    border: 1px solid var(--border);
  }

  .preview-label {
    margin: 0 0 6px;
    font-size: 11px;
    font-weight: 700;
    color: var(--primary);
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }

  .source-label {
    color: var(--muted);
  }

  .article-body {
    max-height: 400px;
  }

  .article-preview h4 {
    margin: 0 0 4px;
    font-size: 0.95rem;
  }

  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
  }

  .text-preview {
    margin: 0;
    white-space: pre-wrap;
    max-height: 220px;
    overflow: auto;
    font-size: 12px;
    line-height: 1.5;
    color: var(--muted);
    padding: 10px;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--surface);
  }

  .meta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .source-toggle {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    margin: 0;
  }

  .source-toggle p {
    margin: 2px 0 0;
    font-size: 12px;
  }

  .quality-block {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .quality-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .quality-chip {
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--surface);
    padding: 6px 12px;
    text-transform: capitalize;
  }

  .quality-chip.active {
    background: color-mix(in srgb, var(--primary) 14%, var(--panel));
    border-color: color-mix(in srgb, var(--primary) 45%, var(--border));
    color: var(--primary);
  }

  .investigation {
    border-top: 1px solid var(--border);
    padding-top: 8px;
  }

  .investigation summary {
    cursor: pointer;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-weight: 600;
    font-size: 0.92rem;
  }

  .investigation summary::-webkit-details-marker {
    display: none;
  }

  .finding {
    margin-top: 8px;
    padding: 10px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--primary) 5%, var(--panel));
    border: 1px solid var(--border);
  }

  .finding p {
    margin: 4px 0 0;
    font-size: 12px;
    line-height: 1.4;
  }

  .decision-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding-top: 4px;
  }

  .decision-actions {
    display: flex;
    gap: 8px;
    margin-left: auto;
  }

  .waiting {
    text-align: center;
    font-size: 12px;
  }

  .btn-danger {
    color: var(--danger);
    border-color: color-mix(in srgb, var(--danger) 35%, var(--border));
  }
</style>
