<script lang="ts">
  /**
   * Listing detail page (x402.pxke.me/listing?url=...) -- the page the
   * redesign explicitly calls out: "a listing detail page that finally
   * shows the probe/grade data the backend already has"
   * (docs/x402-marketplace-product-redesign.md §4.2). Every call here is a
   * free read that already existed (`GET /listings?url=`,
   * `GET /directory/probe/history?url=`, `GET /grades/summary?url=`) -- no
   * new backend work, just a frontend that finally links to them (the
   * 2026-09-07 UX audit's §2.3 "lists with no depth" finding).
   */
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../../lib/i18n'
  import { navigate } from '../../lib/router'
  import { ApiException } from '../../lib/api/client'
  import { x402Api, type X402ListingDetail, type X402ProbeHistory, type X402GradeSummary } from '../../lib/api/x402'
  import { x402Stamp, x402ShortAddr, x402HostOf } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import PageMeta from '../../components/PageMeta.svelte'

  let { url }: { url: string } = $props()

  let detail: X402ListingDetail | null = $state(null)
  let probeHistory: X402ProbeHistory | null = $state(null)
  let gradeSummary: X402GradeSummary | null = $state(null)
  let loading = $state(true)
  let notFound = $state(false)
  let error = $state<string | null>(null)

  $effect(() => {
    const target = url
    const ac = new AbortController()
    loading = true
    notFound = false
    error = null
    void (async () => {
      try {
        const [d, ph, gs] = await Promise.all([
          x402Api.listingDetail(target, { signal: ac.signal }),
          x402Api.probeHistory(target, 20, { signal: ac.signal }),
          x402Api.gradeSummary(target, { signal: ac.signal }),
        ])
        if (ac.signal.aborted) return
        if (!d) {
          notFound = true
          loading = false
          return
        }
        detail = d
        probeHistory = ph
        gradeSummary = gs
        loading = false
      } catch (e) {
        if (ac.signal.aborted) return
        error = e instanceof ApiException ? e.userMessage : untrack(() => t($messages, 'errorGeneric'))
        loading = false
      }
    })()
    return () => ac.abort()
  })

  function back(e: MouseEvent) {
    e.preventDefault()
    navigate('/directory')
  }
</script>

<PageMeta
  title={x402HostOf(url)}
  description={detail?.listing.description || `x402 listing detail: ${url}`}
  path={`/listing?url=${encodeURIComponent(url)}`}
  noindex
/>

<div class="page stack x402">
  <p class="back"><a href="/directory" onclick={back}>← Back to directory</a></p>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="x402-err">{error}</p>
  {:else if notFound}
    <header class="page-head">
      <span class="accent-slug"></span>
      <h1>Not listed</h1>
      <p class="lead muted">No live listing for that url. It may have expired or never existed.</p>
    </header>
  {:else if detail}
    {@const listing = detail.listing}
    <header class="page-head">
      <span class="accent-slug"></span>
      <p class="kicker">Listing</p>
      <h1>{x402HostOf(listing.url)}</h1>
      {#if isHttp(listing.url)}
        <p class="url">
          <a href={listing.url} target="_blank" rel="noopener noreferrer nofollow">{listing.url} ↗</a>
        </p>
      {/if}
      {#if listing.description}
        <p class="lead muted">{listing.description}</p>
      {/if}
    </header>

    <div class="x402-stat-row">
      <div class="x402-stat">
        <span class="value">{listing.price}</span>
        <span class="label">price</span>
      </div>
      {#if detail.probe}
        <div class="x402-stat">
          <span class="value">{detail.probe.reachable ? 'up' : 'down'}</span>
          <span class="label">latest probe</span>
        </div>
        <div class="x402-stat">
          <span class="value">{detail.probe.latency_ms} ms</span>
          <span class="label">latency</span>
        </div>
      {/if}
      {#if gradeSummary}
        <div class="x402-stat">
          <span class="value">{gradeSummary.count}</span>
          <span class="label">grades</span>
        </div>
      {/if}
    </div>

    <section class="block">
      <h2>Details</h2>
      <p class="x402-row-meta">
        {#if listing.verified_wallet}
          <span class="x402-badge verified">{t($messages, 'x402Verified')}</span>
        {/if}
        {#if listing.category}
          <span class="x402-badge">{listing.category}</span>
        {/if}
        {#each listing.assets ?? [] as asset (asset)}
          <span class="x402-badge">{asset}</span>
        {/each}
        {#each listing.tags ?? [] as tg (tg)}
          <span class="x402-stamp">#{tg}</span>
        {/each}
      </p>
      <p class="x402-row-meta">
        {#if listing.payer}
          <span class="x402-stamp">Owner {x402ShortAddr(listing.payer)}</span>
        {/if}
        {#if x402Stamp(listing.term_end_epoch, $activeLocale)}
          <span class="x402-stamp">Listed until {x402Stamp(listing.term_end_epoch, $activeLocale)}</span>
        {/if}
        {#if x402Stamp(listing.created_at_epoch, $activeLocale)}
          <span class="x402-stamp">Listed since {x402Stamp(listing.created_at_epoch, $activeLocale)}</span>
        {/if}
        {#if listing.reimburses}
          <span class="x402-badge">Refunds on failure</span>
        {/if}
      </p>
      {#if listing.contact}
        <p class="muted">Contact: {listing.contact}</p>
      {/if}
    </section>

    <section class="block">
      <h2>Reliability (free, measured)</h2>
      {#if !probeHistory || probeHistory.history.length === 0}
        <p class="muted">Not probed yet.</p>
      {:else}
        <ul class="x402-rows">
          {#each probeHistory.history as p, i (`${p.probed_at_epoch}-${i}`)}
            <li class="x402-row probe-row">
              <span class="x402-badge" class:live={p.reachable} class:gated={!p.reachable}>
                {p.reachable ? 'up' : 'down'}
              </span>
              <span class="x402-stamp">{p.http_status || '—'}</span>
              <span class="x402-stamp">{p.latency_ms} ms</span>
              <span class="x402-stamp">{p.served_valid_402 ? 'valid 402' : 'no 402'}</span>
              <span class="x402-stamp">{x402Stamp(p.probed_at_epoch, $activeLocale)}</span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>

    <section class="block">
      <h2>Grades (trust layer)</h2>
      {#if !gradeSummary}
        <p class="muted">Nobody has graded this endpoint yet.</p>
      {:else}
        <p class="muted">
          {gradeSummary.count} grade{gradeSummary.count === 1 ? '' : 's'}
          {#if x402Stamp(gradeSummary.last_graded_at_epoch, $activeLocale)}
            · last graded {x402Stamp(gradeSummary.last_graded_at_epoch, $activeLocale)}
          {/if}
        </p>
        <p class="muted">{t($messages, 'x402ScorePaidHint')}: <code>GET /api/v1/x402/grades/score?url=</code></p>
      {/if}
    </section>
  {/if}
</div>

<style>
  .back {
    margin: 0;
  }
  .back a {
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    text-decoration: none;
  }
  .back a:hover {
    color: var(--accent);
  }
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(26px, 4vw, 32px);
    overflow-wrap: anywhere;
  }
  .lead {
    margin: 8px 0 0;
    max-width: 46rem;
    font-family: var(--font-serif);
    font-size: 16px;
    line-height: 1.55;
  }
  .url {
    margin: 6px 0 0;
  }
  .url a {
    font-family: var(--font-mono);
    font-size: 12.5px;
    color: var(--accent);
    overflow-wrap: anywhere;
  }
  .block {
    padding-top: 16px;
    border-top: 1px solid var(--border);
  }
  .block h2 {
    margin: 0 0 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .probe-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
  }
  code {
    font-family: var(--font-mono);
    font-size: 12px;
  }
</style>
