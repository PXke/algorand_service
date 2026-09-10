<script lang="ts">
  /**
   * Storefront home (x402.pxke.me/): the three products as a rate card,
   * how a paid call works, and the one URL an agent should fetch first.
   * Prices, status, payTo, facilitator and assets are all read live from
   * the catalog.
   */
  import { navigate } from '../../lib/router'
  import { messages, t } from '../../lib/i18n'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { productGroups, cheapestPriceText } from '../../lib/x402/catalog'
  import { X402_PRODUCTS } from '../../lib/x402/products'
  import { X402_CATALOG_URL } from '../../lib/api/x402'
  import PageMeta from '../../components/PageMeta.svelte'

  ensureX402Catalog()

  const catalog = $derived($x402CatalogState.catalog)
  const loading = $derived($x402CatalogState.loading)
  const failed = $derived($x402CatalogState.failed)
  const groups = $derived(productGroups(catalog))
  const assets = $derived((catalog?.assets ?? []).map((a) => a.symbol).join(', '))

  function go(href: string) {
    return (e: MouseEvent) => {
      e.preventDefault()
      navigate(href)
    }
  }
</script>

<PageMeta title={t($messages, 'x402HomeTitle')} description={t($messages, 'x402HomeLead')} path="/" />

<div class="page x402 home">
  <header class="hero">
    <h1>{t($messages, 'x402HomeTitle')}</h1>
    <p class="lead">{t($messages, 'x402HomeLead')}</p>
  </header>

  <ol class="card" aria-label={t($messages, 'x402HomeTitle')}>
    {#each groups as group (group.key)}
      {@const def = X402_PRODUCTS[group.key]}
      {@const price = cheapestPriceText(group.routes)}
      <li class="x402" data-product={group.key}>
        <a class="row" href={def.path} onclick={go(def.path)}>
          <span class="name">{t($messages, def.nameKey)}</span>
          <span class="price">
            {#if loading}
              <span class="x402-skeleton short"></span>
            {:else if price}
              {t($messages, 'x402FromPrice', { price })}
            {:else if group.routes.length}
              {t($messages, 'x402Free')}
            {/if}
          </span>
          <span class="pitch">{t($messages, def.pitchKey)}</span>
          <span class="meta">
            {#if group.meta}
              <span class="x402-status" class:live={group.meta.status === 'live'}>
                {group.meta.status === 'live' ? t($messages, 'x402StatusLive') : t($messages, 'x402StatusGated')}
              </span>
              <span class="entry">{group.meta.entry}</span>
            {:else if !loading}
              <span class="entry">{def.leadMethod} {def.leadPath}</span>
            {/if}
          </span>
        </a>
      </li>
    {/each}
  </ol>
  {#if failed}
    <p class="x402-err" role="alert">{t($messages, 'x402CatalogUnavailable')}</p>
  {/if}

  <section class="x402-block" aria-labelledby="how-heading">
    <h2 id="how-heading">{t($messages, 'x402HowHeading')}</h2>
    <p>{t($messages, 'x402HowLead')}</p>
    <ol class="wire">
      <li>
        <code>HTTP 402</code>
        <p>{t($messages, 'x402HowStep1')}</p>
      </li>
      <li>
        <code>sign</code>
        <p>{t($messages, 'x402HowStep2')}</p>
      </li>
      <li>
        <code>HTTP 200</code>
        <p>{t($messages, 'x402HowStep3')}</p>
      </li>
    </ol>
    {#if catalog}
      <dl class="x402-facts">
        <dt>{t($messages, 'x402NetworkLabel')}</dt>
        <dd>{catalog.network_name}</dd>
        {#if assets}
          <dt>{t($messages, 'x402AssetsLabel')}</dt>
          <dd>{assets}</dd>
        {/if}
        <dt>{t($messages, 'x402PayToLabel')}</dt>
        <dd>{catalog.pay_to}</dd>
        {#if catalog.facilitator_url}
          <dt>{t($messages, 'x402FacilitatorLabel')}</dt>
          <dd><a href={catalog.facilitator_url} target="_blank" rel="noopener noreferrer">{catalog.facilitator_url}</a></dd>
        {/if}
      </dl>
    {:else if loading}
      <p class="x402-state" role="status">{t($messages, 'x402CatalogLoading')}</p>
    {/if}
  </section>

  <section class="x402-block" aria-labelledby="agents-heading">
    <h2 id="agents-heading">{t($messages, 'x402AgentsHeading')}</h2>
    <p>{t($messages, 'x402AgentsBody')}</p>
    <pre class="x402-curl">curl -s {X402_CATALOG_URL}</pre>
    <p><a class="quiet" href="/developers" onclick={go('/developers')}>{t($messages, 'x402DevTitle')}</a></p>
  </section>
</div>

<style>
  .home {
    display: flex;
    flex-direction: column;
    gap: 32px;
  }
  .hero {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding-top: 10px;
  }
  .hero h1 {
    margin: 0;
    font-family: var(--font-display);
    font-size: clamp(34px, 6vw, 56px);
    line-height: 1.02;
    letter-spacing: -1.2px;
    font-weight: 700;
    max-width: 16em;
  }
  .lead {
    margin: 0;
    font-family: var(--font-serif);
    font-size: clamp(17px, 2.2vw, 20px);
    line-height: 1.5;
    color: var(--body);
    max-width: 34em;
  }

  /* The rate card: three rows, each with a thick rule in its product's
     accent and the price set in mono on the right. */
  .card {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      'name price'
      'pitch pitch'
      'meta meta';
    gap: 6px 20px;
    padding: 16px 0 16px 18px;
    border-inline-start: 6px solid var(--x402-accent);
    color: inherit;
    text-decoration: none;
    min-width: 0;
  }
  .row:hover,
  .row:focus-visible {
    background: var(--panel);
    text-decoration: none;
    outline: none;
  }
  .row:focus-visible {
    box-shadow: inset 0 0 0 2px var(--x402-accent);
  }
  .name {
    grid-area: name;
    font-family: var(--font-display);
    font-size: clamp(24px, 3.4vw, 32px);
    line-height: 1.05;
    font-weight: 700;
    letter-spacing: -0.6px;
  }
  .row:hover .name {
    color: var(--x402-accent);
  }
  .price {
    grid-area: price;
    align-self: start;
    min-width: 6ch;
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    text-align: end;
    padding-top: 6px;
  }
  .price .x402-skeleton {
    width: 6ch;
  }
  .pitch {
    grid-area: pitch;
    font-family: var(--font-serif);
    font-size: 1.05rem;
    line-height: 1.5;
    color: var(--body);
    max-width: 40em;
  }
  .meta {
    grid-area: meta;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 18px;
    margin-top: 2px;
    min-height: 20px;
  }
  .entry {
    font-family: var(--font-mono);
    font-size: 12.5px;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  @media (max-width: 519px) {
    .row {
      grid-template-columns: 1fr;
      grid-template-areas:
        'name'
        'price'
        'pitch'
        'meta';
      padding-inline-start: 14px;
    }
    .price {
      text-align: start;
      padding-top: 0;
    }
  }

  /* The wire: three steps, each labelled by what is on the wire at that moment. */
  .wire {
    list-style: none;
    margin: 4px 0 8px;
    padding: 0;
    counter-reset: step;
    display: flex;
    flex-direction: column;
    max-width: 62ch;
  }
  .wire li {
    counter-increment: step;
    display: grid;
    grid-template-columns: 2ch 9ch minmax(0, 1fr);
    gap: 6px 14px;
    align-items: baseline;
    padding: 10px 0;
    border-bottom: 1px solid var(--border);
  }
  .wire li::before {
    content: counter(step);
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--subtle);
  }
  .wire code {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--accent);
    white-space: nowrap;
  }
  .wire p {
    margin: 0;
    font-size: 15px;
    line-height: 1.5;
    color: var(--body);
  }
  @media (max-width: 519px) {
    .wire li {
      grid-template-columns: 2ch minmax(0, 1fr);
    }
    .wire p {
      grid-column: 2;
    }
  }
  .quiet {
    color: var(--accent);
    font-size: 15px;
  }
</style>
