<script lang="ts">
  import type { AdminApi } from '../../../lib/api/admin'

  let {
    admin,
    onmessage = undefined,
  }: {
    admin: AdminApi
    onmessage?: (msg: string) => void
  } = $props()

  const STAT_LABELS: Record<string, string> = {
    total_labeled: 'Total labelled decisions',
    approved: 'Approved',
    rejected: 'Rejected',
    graded_trainable: 'Trainable (with grade dims)',
    graded_approved: 'Graded accept',
    graded_rejected: 'Graded reject',
    graded_accept: 'Graded accept',
    graded_reject: 'Graded reject',
    min_samples: 'Min samples to train',
  }

  const SKIP_KEYS = new Set(['ready_to_train'])

  let stats = $state<Record<string, unknown> | null>(null)
  let loading = $state(true)
  let error = $state<string | null>(null)
  let retrainMsg = $state<string | null>(null)
  let retraining = $state(false)

  const extraRows = $derived.by(() => {
    if (!stats) return []
    const known = new Set([...Object.keys(STAT_LABELS), ...SKIP_KEYS])
    return Object.entries(stats)
      .filter(([k, v]) => !known.has(k) && typeof v !== 'object')
      .map(([key, value]) => ({
        key,
        label: key.replaceAll('_', ' '),
        value: String(value),
      }))
  })

  const ready = $derived(stats?.ready_to_train === true)
  const graded = $derived(Number(stats?.graded_trainable ?? 0))
  const minSamples = $derived(Number(stats?.min_samples ?? 40))
  const gApproved = $derived(Number(stats?.graded_approved ?? stats?.graded_accept ?? 0))
  const gRejected = $derived(Number(stats?.graded_rejected ?? stats?.graded_reject ?? 0))
  const approved = $derived(Number(stats?.approved ?? 0))
  const rejected = $derived(Number(stats?.rejected ?? 0))

  async function load() {
    loading = true
    error = null
    try {
      stats = await admin.getTrainingStats()
    } catch (e) {
      error = e instanceof Error ? e.message : String(e)
    } finally {
      loading = false
    }
  }

  async function retrain() {
    retraining = true
    retrainMsg = 'Queuing retrain…'
    try {
      await admin.triggerRetrain()
      retrainMsg = 'Retrain queued — refresh in a minute to see results.'
      onmessage?.('Retrain queued')
      await load()
    } catch (e) {
      retrainMsg = `Retrain failed: ${e instanceof Error ? e.message : String(e)}`
    } finally {
      retraining = false
    }
  }

  $effect(() => {
    void load()
  })
</script>

<div class="tab stack">
  <div class="toolbar">
    <div>
      <h2>Training</h2>
      <p class="intro">
        Every accept/reject on the Review tab is a labelled example. The learned grader trains on
        rows that captured grade dimensions. Use "Training mode" on the Review tab to label without
        publishing.
      </p>
    </div>
    <button class="btn" type="button" onclick={() => load()}>Refresh</button>
  </div>

  {#if loading}
    <p class="muted">Loading…</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if stats}
    <section class="panel stats-section">
      <div class="stat-row">
        <span>Total labelled decisions</span>
        <strong>{String(stats.total_labeled ?? 0)}</strong>
      </div>
      <div class="stat-row">
        <span>Accept / reject balance</span>
        <strong>{approved} accept · {rejected} reject</strong>
      </div>
      <hr />
      <div class="stat-row">
        <span>Trainable (with grade dims)</span>
        <strong>{graded}</strong>
      </div>
      <div class="stat-row indent">
        <span>└ accept / reject</span>
        <strong>{gApproved} / {gRejected}</strong>
      </div>
      <div class="stat-row">
        <span>Min samples to train</span>
        <strong>{minSamples}</strong>
      </div>

      {#if extraRows.length}
        <hr />
        {#each extraRows as row}
          <div class="stat-row">
            <span>{row.label}</span>
            <strong>{row.value}</strong>
          </div>
        {/each}
      {/if}
    </section>

    <div class="readiness panel" class:ready>
      {#if ready}
        ✓ Ready to train the learned grader ({graded} balanced samples).
      {:else}
        Collecting… need {minSamples} balanced graded samples (have {graded}). Until then the
        grader uses heuristic weights.
      {/if}
    </div>

    <div class="retrain-row">
      <button class="btn btn-primary" type="button" disabled={loading || retraining} onclick={() => retrain()}>
        {retraining ? 'Queuing…' : 'Retrain now'}
      </button>
      {#if retrainMsg}
        <p class="retrain-msg">{retrainMsg}</p>
      {/if}
    </div>
  {/if}
</div>

<style>
  .toolbar {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
  }
  h2 {
    margin: 0;
    font-size: 1.25rem;
  }
  .intro {
    margin: 4px 0 0;
    font-size: 0.88rem;
    color: var(--muted);
    max-width: 58ch;
    line-height: 1.45;
  }
  .stats-section {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 16px;
    padding: 6px 0;
    font-size: 0.92rem;
  }
  .stat-row.indent span {
    padding-left: 8px;
    color: var(--muted);
  }
  hr {
    border: 0;
    border-top: 1px solid var(--border);
    margin: 10px 0;
  }
  .readiness {
    padding: 12px 14px;
    border-radius: 10px;
    font-size: 0.92rem;
    line-height: 1.45;
    border: 1px solid color-mix(in srgb, #c2410c 30%, var(--border));
    background: color-mix(in srgb, #c2410c 8%, var(--panel));
  }
  .readiness.ready {
    border-color: color-mix(in srgb, var(--gain) 35%, var(--border));
    background: color-mix(in srgb, var(--gain) 10%, var(--panel));
  }
  .retrain-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  .retrain-msg {
    margin: 0;
    flex: 1;
    min-width: 200px;
    font-size: 0.85rem;
    color: var(--muted);
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
