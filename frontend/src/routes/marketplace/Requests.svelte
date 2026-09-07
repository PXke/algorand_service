<script lang="ts">
  /** Feature-request board (x402.pxke.me/requests). Free to file and browse,
   * paid to vote/claim/complete/read demand -- filing is the one write in
   * this whole marketplace that can complete in-browser today with no
   * wallet at all, so the redesign makes it the visible success path
   * (docs/x402-marketplace-product-redesign.md §4.2) instead of a curl
   * block like Board's. */
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../../lib/i18n'
  import { ApiException } from '../../lib/api/client'
  import { x402Api, type X402FeatureRequest } from '../../lib/api/x402'
  import { x402Stamp, x402ShortAddr } from '../../lib/x402/format'
  import PageMeta from '../../components/PageMeta.svelte'

  let requests: X402FeatureRequest[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  let title = $state('')
  let description = $state('')
  let filing = $state(false)
  let fileError = $state<string | null>(null)
  let filed = $state(false)

  async function load(signal?: AbortSignal) {
    try {
      requests = await x402Api.features(signal ? { signal } : undefined)
      loading = false
    } catch (e) {
      if (signal?.aborted) return
      error = e instanceof ApiException ? e.userMessage : untrack(() => t($messages, 'errorGeneric'))
      loading = false
    }
  }

  $effect(() => {
    const ac = new AbortController()
    void load(ac.signal)
    return () => ac.abort()
  })

  async function submitRequest(e: SubmitEvent) {
    e.preventDefault()
    if (!title.trim()) {
      fileError = 'Title is required.'
      return
    }
    filing = true
    fileError = null
    try {
      await x402Api.fileFeatureRequest(title.trim(), description.trim())
      filed = true
      title = ''
      description = ''
      void load()
    } catch (e) {
      fileError = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
    } finally {
      filing = false
    }
  }
</script>

<PageMeta
  title="Requests"
  description="File a feature request for free, then pay to vote it up, claim it, or read the paid-demand ranking."
  path="/requests"
/>

<div class="page stack x402">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">Get found</p>
    <h1>Requests</h1>
    <p class="lead muted">
      File a feature request for free, then pay to vote it up, claim it, or mark it complete.
    </p>
  </header>

  <form class="file-form" onsubmit={submitRequest}>
    <div class="field">
      <label for="req-title">Title</label>
      <input id="req-title" type="text" bind:value={title} maxlength="200" required disabled={filing} />
    </div>
    <div class="field">
      <label for="req-desc">Description (optional)</label>
      <textarea id="req-desc" rows="3" bind:value={description} maxlength="2000" disabled={filing}></textarea>
    </div>
    {#if fileError}
      <p class="x402-err">{fileError}</p>
    {/if}
    {#if filed}
      <p class="filed-ok">Filed. Thanks -- it now appears below.</p>
    {/if}
    <button class="btn btn-primary" type="submit" disabled={filing}>
      {filing ? 'Filing…' : 'File a request (free)'}
    </button>
  </form>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="x402-err">{error}</p>
  {:else if requests.length === 0}
    <p class="muted x402-empty">{t($messages, 'x402Empty')}</p>
  {:else}
    <p class="x402-hit-count">{requests.length}<span class="sep" aria-hidden="true">·</span>filed</p>
    <ul class="x402-rows">
      {#each requests as item, i (item.request_id ?? `${item.title}-${i}`)}
        <li class="x402-row">
          <div class="x402-row-head">
            <span class="x402-row-name">{item.title}</span>
            {#if typeof item.vote_total === 'number'}
              <span class="x402-row-price">{item.vote_total} votes</span>
            {/if}
          </div>
          {#if item.description}
            <p class="x402-row-desc">{item.description}</p>
          {/if}
          <p class="x402-row-meta">
            {#if x402Stamp(item.created_at_epoch, $activeLocale)}
              <span class="x402-stamp">Filed {x402Stamp(item.created_at_epoch, $activeLocale)}</span>
            {/if}
            {#if typeof item.claims_count === 'number' && item.claims_count > 0}
              <span class="x402-badge">{item.claims_count} builders claimed</span>
            {/if}
            {#if item.latest_claimer}
              <span class="x402-stamp">Building {x402ShortAddr(item.latest_claimer)}</span>
            {/if}
          </p>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 46rem;
    font-family: var(--font-serif);
    font-size: 17px;
    line-height: 1.55;
  }
  .file-form {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--panel);
  }
  .field label {
    display: block;
    margin-bottom: 4px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .field input,
  .field textarea {
    width: 100%;
    box-sizing: border-box;
  }
  .filed-ok {
    margin: 0;
    color: var(--gain);
    font-family: var(--font-serif);
    font-size: 0.9rem;
  }
</style>
