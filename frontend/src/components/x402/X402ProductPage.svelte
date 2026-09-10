<script lang="ts">
  /**
   * One layout for the three product pages (/scan, /storage, /news): a hero
   * in the product's accent, the route table read live from the catalog,
   * a tabbed "call it" panel, a realistic example response, and the limits.
   * Renders a skeleton while the catalog loads and a plain error line with
   * the machine-readable URL if it fails -- never a blank page.
   */
  import type { Snippet } from 'svelte'
  import { config } from '../../lib/config'
  import { messages, t } from '../../lib/i18n'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { productGroup, routePrice, cheapestPriceText, findRoute, type X402ProductKey } from '../../lib/x402/catalog'
  import { productDef } from '../../lib/x402/products'
  import { x402Json } from '../../lib/x402/format'
  import { X402_CATALOG_URL, X402_API_ORIGIN } from '../../lib/api/x402'
  import PageMeta from '../PageMeta.svelte'
  import X402CodeTabs from './X402CodeTabs.svelte'

  let {
    product,
    before,
    after,
  }: { product: X402ProductKey; before?: Snippet; after?: Snippet } = $props()

  ensureX402Catalog()

  const def = $derived(productDef(product))
  const catalog = $derived($x402CatalogState.catalog)
  const loading = $derived($x402CatalogState.loading)
  const failed = $derived($x402CatalogState.failed)
  const group = $derived(productGroup(catalog, product))
  const status = $derived(group.meta?.status ?? null)
  const fromPrice = $derived(cheapestPriceText(group.routes))
  const lead = $derived(findRoute(group.routes, def.leadPath, def.leadMethod))

  const apiBase = $derived(config.apiBaseUrl.replace(/\/$/, '') || X402_API_ORIGIN)
  const snippets = $derived(def.snippets(apiBase))
  const exampleText = $derived(x402Json(def.example))

  const name = $derived(t($messages, def.nameKey))
  const pitch = $derived(t($messages, def.pitchKey))
</script>

<PageMeta title={name} description={pitch} path={def.path} />

<div class="page x402 product" data-product={product}>
  <header class="hero">
    <h1>{name}</h1>
    <p class="pitch">{pitch}</p>
    <p class="who">{t($messages, def.whoKey)}</p>
    <div class="facts-row">
      {#if loading}
        <span class="x402-skeleton short"></span>
      {:else if status}
        <span class="x402-status" class:live={status === 'live'}>
          {status === 'live' ? t($messages, 'x402StatusLive') : t($messages, 'x402StatusGated')}
        </span>
      {/if}
      {#if fromPrice}
        <span class="from">{t($messages, 'x402FromPrice', { price: fromPrice })}</span>
      {:else if !loading && !failed && group.routes.length}
        <span class="from">{t($messages, 'x402Free')}</span>
      {/if}
      {#if lead?.supports_preview}
        <span class="flag">{t($messages, 'x402PreviewBadge')}</span>
      {/if}
    </div>
  </header>

  {#if before}
    {@render before()}
  {/if}

  <section class="x402-block" aria-labelledby="routes-heading">
    <h2 id="routes-heading">{t($messages, 'x402RoutesHeading')}</h2>
    {#if loading}
      <ul class="x402-routes" aria-busy="true">
        {#each [0, 1, 2] as i (i)}
          <li class="x402-route">
            <span class="x402-skeleton"></span>
            <span class="x402-skeleton short"></span>
          </li>
        {/each}
      </ul>
      <p class="x402-state" role="status">{t($messages, 'x402CatalogLoading')}</p>
    {:else if failed || group.routes.length === 0}
      <p class="x402-err" role="alert">{t($messages, 'x402CatalogUnavailable')}</p>
      <pre class="x402-curl">GET {X402_CATALOG_URL}</pre>
    {:else}
      <ul class="x402-routes">
        {#each group.routes as route (`${route.method} ${route.path}`)}
          <li class="x402-route">
            <span class="x402-route-sig">
              <span class="x402-route-method">{route.method}</span>
              <span class="x402-route-path">{route.path}</span>
            </span>
            <span class="x402-route-price" class:free={!route.paid}>{routePrice(route, $messages)}</span>
            {#if route.description}
              <p class="x402-route-desc">{route.description}</p>
            {/if}
            {#if route.supports_preview || route.supports_promo}
              <span class="x402-route-flags">
                {#if route.supports_preview}<span>{t($messages, 'x402PreviewBadge')}</span>{/if}
                {#if route.supports_promo}<span>{t($messages, 'x402PromoBadge')}</span>{/if}
              </span>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="x402-block" aria-labelledby="call-heading">
    <h2 id="call-heading">{t($messages, 'x402CallHeading')}</h2>
    <p>{t($messages, 'x402CallPythonHint')}</p>
    <X402CodeTabs
      label={t($messages, 'x402CallHeading')}
      tabs={[
        { key: 'curl', label: t($messages, 'x402CallCurl'), code: snippets.curl },
        { key: 'python', label: t($messages, 'x402CallPython'), code: snippets.python },
      ]}
    />
    {#if lead?.supports_preview}
      <p>{t($messages, 'x402PreviewNote')}</p>
    {/if}
  </section>

  <section class="x402-block" aria-labelledby="returns-heading">
    <h2 id="returns-heading">{t($messages, 'x402ReturnsHeading')}</h2>
    <p>{t($messages, 'x402ReturnsBody', { method: def.leadMethod, path: def.leadPath })}</p>
    <pre class="x402-curl example">{exampleText}</pre>
  </section>

  <section class="x402-block" aria-labelledby="limits-heading">
    <h2 id="limits-heading">{t($messages, 'x402LimitsHeading')}</h2>
    <ul class="limits">
      {#each def.limitKeys as key (key)}
        <li>{t($messages, key)}</li>
      {/each}
    </ul>
  </section>

  {#if after}
    {@render after()}
  {/if}
</div>

<style>
  .product {
    display: flex;
    flex-direction: column;
    gap: 28px;
  }
  .hero {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding-top: 18px;
    border-top: 6px solid var(--x402-accent);
  }
  .hero h1 {
    margin: 0;
    font-family: var(--font-display);
    font-size: clamp(40px, 7vw, 64px);
    line-height: 0.98;
    letter-spacing: -1.5px;
    font-weight: 700;
  }
  .pitch {
    margin: 4px 0 0;
    font-family: var(--font-serif);
    font-size: clamp(18px, 2.4vw, 21px);
    line-height: 1.45;
    max-width: 30em;
    color: var(--on-surface);
  }
  .who {
    margin: 0;
    font-size: 15px;
    line-height: 1.5;
    color: var(--muted);
    max-width: 44em;
  }
  .facts-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 22px;
    margin-top: 6px;
    min-height: 20px;
  }
  .from {
    font-family: var(--font-mono);
    font-size: 14px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--on-surface);
  }
  .flag {
    font-size: 13px;
    color: var(--subtle);
  }
  .example {
    max-height: 26rem;
    overflow: auto;
    white-space: pre;
  }
  .limits {
    margin: 0;
    padding-inline-start: 1.1em;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: 66ch;
    font-family: var(--font-serif);
    font-size: 1rem;
    line-height: 1.55;
    color: var(--body);
  }
  .limits li::marker {
    color: var(--x402-accent);
  }
</style>
