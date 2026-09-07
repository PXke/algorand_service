<script lang="ts">
  /** Services (x402.pxke.me/services) -- "buy a service": PXke's own
   * pay-per-call products (news, sandboxed scan, backup storage), tagged
   * `section: "services"` in catalog v2. Replaces the old "Our Endpoints"
   * page, which used to bucket uptime here too by process of elimination
   * (MARKETPLACE_MECHANIC_PRODUCT_KEYS was a binary mechanic/non-mechanic
   * split with no room for a third bucket) -- uptime now correctly lives on
   * the Trust page instead, since catalog v2 tags it `section: "trust"`. */
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../../lib/i18n'
  import { ApiException } from '../../lib/api/client'
  import { x402Api, type X402NewsItem } from '../../lib/api/x402'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { productsForSections } from '../../lib/x402/catalog'
  import { x402Stamp } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import PageMeta from '../../components/PageMeta.svelte'
  import X402ProductCatalog from '../../components/x402/X402ProductCatalog.svelte'

  ensureX402Catalog()

  const catalog = $derived($x402CatalogState.catalog)
  const catalogFailed = $derived($x402CatalogState.failed)
  const serviceProducts = $derived(productsForSections(catalog, ['services']))

  let newsItems: X402NewsItem[] = $state([])
  let newsLoading = $state(true)
  let newsError = $state<string | null>(null)

  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        newsItems = await x402Api.news({ signal: ac.signal })
        if (ac.signal.aborted) return
        newsLoading = false
      } catch (e) {
        if (ac.signal.aborted) return
        newsError = e instanceof ApiException ? e.userMessage : untrack(() => t($messages, 'errorGeneric'))
        newsLoading = false
      }
    })()
    return () => ac.abort()
  })
</script>

<PageMeta
  title="Services"
  description="PXke's own pay-per-call products: news, sandboxed file scanning, backup storage."
  path="/services"
/>

<div class="page stack x402">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">Buy a service</p>
    <h1>Services</h1>
    <p class="lead muted">
      Every paid product PXke itself runs on x402, alongside third-party listings in the same
      directory. Prices and routes below are read live from the same machine-readable catalog
      other agents use.
    </p>
  </header>

  <section class="products">
    {#if catalogFailed}
      <p class="muted">The live catalog could not be loaded right now.</p>
    {:else if !catalog}
      <p class="muted">{t($messages, 'loading')}</p>
    {:else}
      <X402ProductCatalog products={serviceProducts} {catalog} />
    {/if}
  </section>

  <section class="news">
    <h2>Recent news</h2>
    {#if newsLoading}
      <p class="muted">{t($messages, 'loading')}</p>
    {:else if newsError}
      <p class="x402-err">{newsError}</p>
    {:else if newsItems.length === 0}
      <p class="muted x402-empty">{t($messages, 'x402Empty')}</p>
    {:else}
      <ul class="x402-rows">
        {#each newsItems as item, i (`${item.article_id}-${i}`)}
          <li class="x402-row">
            <div class="x402-row-head">
              {#if isHttp(item.url)}
                <a class="x402-row-name" href={item.url} target="_blank" rel="noopener noreferrer nofollow"
                  >{item.title}</a
                >
              {:else}
                <span class="x402-row-name">{item.title}</span>
              {/if}
            </div>
            {#if item.summary}
              <p class="x402-row-desc">{item.summary}</p>
            {/if}
            <p class="x402-row-meta">
              {#each item.tags ?? [] as tg (tg)}
                <span class="x402-badge">#{tg}</span>
              {/each}
              {#if x402Stamp(item.published_at_epoch, $activeLocale)}
                <span class="x402-stamp">{x402Stamp(item.published_at_epoch, $activeLocale)}</span>
              {/if}
            </p>
          </li>
        {/each}
      </ul>
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
  .news {
    margin-top: 8px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .news h2 {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
</style>
