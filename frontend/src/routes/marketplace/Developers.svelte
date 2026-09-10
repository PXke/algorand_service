<script lang="ts">
  /**
   * Developers (x402.pxke.me/developers): quickstart, the discovery
   * documents, the Python client, the free settlements feed, and the
   * preview/promo bypasses. Everything here is generated from the same
   * route roster agents fetch.
   */
  import { navigate } from '../../lib/router'
  import { config } from '../../lib/config'
  import { messages, t, activeLocale } from '../../lib/i18n'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { productGroups, routePriceText } from '../../lib/x402/catalog'
  import { X402_PRODUCTS } from '../../lib/x402/products'
  import {
    x402Api,
    X402_API_ORIGIN,
    X402_CATALOG_URL,
    X402_WELLKNOWN_URL,
    X402_OPENAPI_URL,
    X402_SDK_URL,
    X402_PATHS,
    type X402SettlementsFeed,
  } from '../../lib/api/x402'
  import { x402ShortAddr, x402Stamp } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import PageMeta from '../../components/PageMeta.svelte'
  import X402PageHead from '../../components/x402/X402PageHead.svelte'
  import X402CodeTabs from '../../components/x402/X402CodeTabs.svelte'

  ensureX402Catalog()

  const catalog = $derived($x402CatalogState.catalog)
  const catalogLoading = $derived($x402CatalogState.loading)
  const groups = $derived(productGroups(catalog))
  const apiBase = $derived(config.apiBaseUrl.replace(/\/$/, '') || X402_API_ORIGIN)
  const sdkUrl = $derived(`${X402_API_ORIGIN}${X402_SDK_URL}`)

  const quickstart = $derived({
    curl: `# 1. Read the catalog: every route, its price, the assets and the address to pay
curl -s ${X402_CATALOG_URL}

# 2. Call a paid route with no payment: HTTP 402, the offer is in PAYMENT-REQUIRED
curl -si '${apiBase}${X402_PATHS.newsSearch}?q=algorand'

# 3. Sign an Algorand transfer for that offer, then repeat with PAYMENT-SIGNATURE
curl -s '${apiBase}${X402_PATHS.newsSearch}?q=algorand' -H "PAYMENT-SIGNATURE: $SIGNED"`,
    python: `# pip install ${sdkUrl}pxke_x402-<version>.tar.gz   (the current tarball is linked below)

from pxke_x402 import PxkeClient

client = PxkeClient(mnemonic=MNEMONIC)   # a funded mainnet wallet; omit for free reads
print(client.catalog()["routes"][0])
hits = client.search_news("algorand")     # 402, sign, retry: all inside this call
print(hits["settlement_tx_id"])`,
  })

  let feed = $state<X402SettlementsFeed | null>(null)
  let feedLoaded = $state(false)
  let feedFailed = $state(false)

  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        const res = await x402Api.settlements(10, { signal: ac.signal })
        if (ac.signal.aborted) return
        feed = res
        feedLoaded = true
      } catch {
        if (ac.signal.aborted) return
        feedFailed = true
        feedLoaded = true
      }
    })()
    return () => ac.abort()
  })

  function go(href: string) {
    return (e: MouseEvent) => {
      e.preventDefault()
      navigate(href)
    }
  }
</script>

<PageMeta title={t($messages, 'x402DevTitle')} description={t($messages, 'x402DevLead')} path="/developers" />

<div class="page x402 developers">
  <X402PageHead title={t($messages, 'x402DevTitle')} lead={t($messages, 'x402DevLead')} />

  <section class="x402-block" aria-labelledby="quickstart-heading">
    <h2 id="quickstart-heading">{t($messages, 'x402DevQuickstart')}</h2>
    <p>{t($messages, 'x402HowLead')}</p>
    <X402CodeTabs
      label={t($messages, 'x402DevQuickstart')}
      tabs={[
        { key: 'curl', label: t($messages, 'x402CallCurl'), code: quickstart.curl },
        { key: 'python', label: t($messages, 'x402CallPython'), code: quickstart.python },
      ]}
    />
  </section>

  <section class="x402-block" aria-labelledby="discovery-heading">
    <h2 id="discovery-heading">{t($messages, 'x402DevDiscovery')}</h2>
    <p>{t($messages, 'x402AgentsBody')}</p>
    <dl class="x402-facts">
      <dt>catalog</dt>
      <dd><a href={X402_CATALOG_URL} target="_blank" rel="noopener noreferrer">{X402_CATALOG_URL}</a></dd>
      <dt>.well-known</dt>
      <dd><a href={X402_WELLKNOWN_URL} target="_blank" rel="noopener noreferrer">{X402_WELLKNOWN_URL}</a></dd>
      <dt>OpenAPI 3.1</dt>
      <dd><a href={X402_OPENAPI_URL} target="_blank" rel="noopener noreferrer">{X402_OPENAPI_URL}</a></dd>
      {#if catalog}
        <dt>{t($messages, 'x402NetworkLabel')}</dt>
        <dd>{catalog.network_name} ({catalog.network})</dd>
        <dt>{t($messages, 'x402PayToLabel')}</dt>
        <dd>{catalog.pay_to}</dd>
        {#if catalog.facilitator_url}
          <dt>{t($messages, 'x402FacilitatorLabel')}</dt>
          <dd><a href={catalog.facilitator_url} target="_blank" rel="noopener noreferrer">{catalog.facilitator_url}</a></dd>
        {/if}
      {/if}
    </dl>
    {#if catalog?.facilitator_url}
      <p>{t($messages, 'x402DevFacilitatorBody')}</p>
    {/if}
  </section>

  <section class="x402-block" aria-labelledby="products-heading">
    <h2 id="products-heading">{t($messages, 'x402RoutesHeading')}</h2>
    {#if catalogLoading}
      <p class="x402-state" role="status">{t($messages, 'x402CatalogLoading')}</p>
    {:else if !catalog}
      <p class="x402-err" role="alert">{t($messages, 'x402CatalogUnavailable')}</p>
    {:else}
      <ul class="products">
        {#each groups as group (group.key)}
          {@const def = X402_PRODUCTS[group.key]}
          <li class="x402" data-product={group.key}>
            <a class="product" href={def.path} onclick={go(def.path)}>{t($messages, def.nameKey)}</a>
            <ul class="x402-routes compact">
              {#each group.routes as route (`${route.method} ${route.path}`)}
                <li class="x402-route">
                  <span class="x402-route-sig">
                    <span class="x402-route-method">{route.method}</span>
                    <span class="x402-route-path">{route.path}</span>
                  </span>
                  <span class="x402-route-price" class:free={!route.paid}>{routePriceText(route) ?? t($messages, 'x402Free')}</span>
                </li>
              {/each}
            </ul>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section class="x402-block" aria-labelledby="sdk-heading">
    <h2 id="sdk-heading">{t($messages, 'x402DevSdk')}</h2>
    <p>{t($messages, 'x402DevSdkBody')}</p>
    <dl class="x402-facts">
      <dt>tarballs</dt>
      <dd><a href={sdkUrl} target="_blank" rel="noopener noreferrer">{sdkUrl}</a></dd>
      <dt>mirror</dt>
      <dd><a href={X402_SDK_URL}>{X402_SDK_URL}</a></dd>
    </dl>
  </section>

  <section class="x402-block" aria-labelledby="bypass-heading">
    <h2 id="bypass-heading">{t($messages, 'x402DevPreviewPromo')}</h2>
    <p>{t($messages, 'x402DevPreviewPromoBody')}</p>
    <pre class="x402-curl">curl -s '{apiBase}{X402_PATHS.newsSearch}?q=algorand&preview=true'
curl -s '{apiBase}{X402_PATHS.newsSearch}?q=algorand&promo=CODE&promo_wallet=WALLET'</pre>
  </section>

  <section class="x402-block" aria-labelledby="settlements-heading">
    <h2 id="settlements-heading">{t($messages, 'x402DevSettlements')}</h2>
    <p>{t($messages, 'x402DevSettlementsBody')}</p>
    <p><code class="path">GET {X402_PATHS.settlements}</code></p>
    {#if !feedLoaded}
      <p class="x402-state" role="status">{t($messages, 'loading')}</p>
    {:else if feedFailed}
      <p class="x402-err" role="alert">{t($messages, 'errorGeneric')}</p>
    {:else if feed && feed.items.length}
      <ul class="settlements">
        {#each feed.items as s (s.tx_id)}
          <li>
            <span class="when">{x402Stamp(s.settled_at_epoch, $activeLocale)}</span>
            <span class="resource">{s.resource}</span>
            <span class="amount">{s.amount} {s.asset}</span>
            <span class="payer">{x402ShortAddr(s.payer)}</span>
          </li>
        {/each}
      </ul>
    {:else}
      <p class="x402-state">{t($messages, 'x402DevSettlementsEmpty')}</p>
      {#if feed?.verify_at && isHttp(feed.verify_at)}
        <p><a class="quiet" href={feed.verify_at} target="_blank" rel="noopener noreferrer">{t($messages, 'x402DevSettlementsVerify')}</a></p>
      {/if}
    {/if}
  </section>
</div>

<style>
  .developers {
    display: flex;
    flex-direction: column;
    gap: 26px;
  }
  .products {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 18px;
  }
  .product {
    display: inline-block;
    margin-bottom: 6px;
    padding-inline-start: 10px;
    border-inline-start: 4px solid var(--x402-accent);
    font-family: var(--font-display);
    font-size: 17px;
    font-weight: 700;
    color: var(--on-surface);
    text-decoration: none;
  }
  .product:hover {
    color: var(--x402-accent);
    text-decoration: none;
  }
  .compact .x402-route {
    padding: 7px 0;
  }
  .path {
    font-family: var(--font-mono);
    font-size: 12.5px;
    color: var(--muted);
  }
  .settlements {
    list-style: none;
    margin: 0;
    padding: 0;
    border-top: 1px solid var(--border);
    font-family: var(--font-mono);
    font-size: 12.5px;
  }
  .settlements li {
    display: grid;
    grid-template-columns: 11ch minmax(0, 1fr) auto 10ch;
    gap: 12px;
    padding: 8px 0;
    border-bottom: 1px solid var(--border);
    align-items: baseline;
  }
  .when,
  .payer {
    color: var(--subtle);
  }
  .amount {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .resource {
    overflow-wrap: anywhere;
  }
  @media (max-width: 519px) {
    .settlements li {
      grid-template-columns: 1fr auto;
    }
  }
  .quiet {
    color: var(--accent);
  }
</style>
