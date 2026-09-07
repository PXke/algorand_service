<script lang="ts">
  /** Trust (x402.pxke.me/trust) -- "check before you pay": paid endpoint
   * grading, plus the on-demand uptime check and KYA identity lookup
   * products, grouped here because catalog v2 now tags all three
   * `section: "trust"` (docs/x402-marketplace-product-redesign.md §3.2).
   * Replaces the old "grades" tab, which only ever showed grading. */
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../../lib/i18n'
  import { navigate } from '../../lib/router'
  import { ApiException } from '../../lib/api/client'
  import { x402Api, type X402GradedEndpoint } from '../../lib/api/x402'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { productsForSections } from '../../lib/x402/catalog'
  import { x402Stamp } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import PageMeta from '../../components/PageMeta.svelte'
  import X402ProductCatalog from '../../components/x402/X402ProductCatalog.svelte'

  ensureX402Catalog()

  let graded: X402GradedEndpoint[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  const catalog = $derived($x402CatalogState.catalog)
  const catalogFailed = $derived($x402CatalogState.failed)
  const trustProducts = $derived(productsForSections(catalog, ['trust']))

  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        graded = await x402Api.grades({ signal: ac.signal })
        if (ac.signal.aborted) return
        loading = false
      } catch (e) {
        if (ac.signal.aborted) return
        error = e instanceof ApiException ? e.userMessage : untrack(() => t($messages, 'errorGeneric'))
        loading = false
      }
    })()
    return () => ac.abort()
  })

  function openListing(url: string, e: MouseEvent) {
    e.preventDefault()
    navigate(`/listing?url=${encodeURIComponent(url)}`)
  }
</script>

<PageMeta
  title="Trust"
  description="Everything that answers 'should I pay this endpoint': paid grading, scheduled and on-demand reachability, agent identity."
  path="/trust"
/>

<div class="page stack x402">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">Check before you pay</p>
    <h1>Trust</h1>
    <p class="lead muted">
      A grade is a paid opinion from a wallet that actually paid the endpoint once, on-chain
      verified. A probe is a free, scheduled measurement the directory already runs. Every
      listing's own page shows both -- this page is the index.
    </p>
  </header>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="x402-err">{error}</p>
  {:else if graded.length === 0}
    <p class="muted x402-empty">{t($messages, 'x402Empty')}</p>
  {:else}
    <p class="x402-hit-count">{graded.length}<span class="sep" aria-hidden="true">·</span>graded</p>
    <ul class="x402-rows">
      {#each graded as item, i (`${item.url}-${i}`)}
        <li class="x402-row">
          <div class="x402-row-head">
            <a
              class="x402-row-name"
              href={`/listing?url=${encodeURIComponent(item.url)}`}
              onclick={(e) => openListing(item.url, e)}
            >
              {item.url}
            </a>
          </div>
          <p class="x402-row-meta">
            {#if x402Stamp(item.last_graded_at_epoch, $activeLocale)}
              <span class="x402-stamp">Last graded {x402Stamp(item.last_graded_at_epoch, $activeLocale)}</span>
            {/if}
            <span class="x402-stamp">{t($messages, 'x402ScorePaidHint')}</span>
            {#if isHttp(item.url)}
              <a class="detail-link" href={`/listing?url=${encodeURIComponent(item.url)}`} onclick={(e) => openListing(item.url, e)}>
                View listing
              </a>
            {/if}
          </p>
        </li>
      {/each}
    </ul>
  {/if}

  <section class="products">
    <h2>Trust products</h2>
    {#if catalogFailed}
      <p class="muted">The live catalog could not be loaded right now.</p>
    {:else if !catalog}
      <p class="muted">{t($messages, 'loading')}</p>
    {:else}
      <X402ProductCatalog products={trustProducts} {catalog} />
    {/if}
  </section>
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
  .detail-link {
    color: var(--accent);
  }
  .products {
    margin-top: 8px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .products h2 {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
</style>
