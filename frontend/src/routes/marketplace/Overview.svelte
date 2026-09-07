<script lang="ts">
  /**
   * Marketplace root (x402.pxke.me/). Replaces the old behaviour of aliasing
   * '/' straight to the directory tab -- see
   * docs/x402-marketplace-product-redesign.md §4.2 "Home": one sentence,
   * live numbers, the five catalog sections as real navigable groups (not a
   * flat 80-route list), one primary CTA. Everything here reads the shared
   * catalog v2 document (lib/x402/catalogStore.ts) fetched once for the
   * whole app -- no separate fetch, no hand-maintained product list.
   */
  import { navigate } from '../../lib/router'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { sectionGroups, catalogStats } from '../../lib/x402/catalog'
  import { messages, t } from '../../lib/i18n'
  import PageMeta from '../../components/PageMeta.svelte'

  ensureX402Catalog()

  const sections = $derived(sectionGroups($x402CatalogState.catalog))
  const stats = $derived(catalogStats($x402CatalogState.catalog))

  function go(href: string, e: MouseEvent) {
    e.preventDefault()
    navigate(href)
  }
</script>

<PageMeta
  title="PXke x402 marketplace"
  description="List, find and vet x402 endpoints on Algorand -- agents pay per call in USDC, EURQ or USDQ, all under one payTo."
  path="/"
/>

<div class="page stack x402-overview">
  <header>
    <span class="accent-slug"></span>
    <p class="kicker">x402 on Algorand mainnet</p>
    <h1>A marketplace agents can pay into, in one call</h1>
    <p class="lead muted">
      List an x402 endpoint for other agents to find, or search what is already listed. Every
      product below -- including PXke's own -- shares one catalog, one payTo, and one settlement
      ledger. Humans browse everything for free; agents pay per call.
    </p>
    <div class="cta-row">
      <a class="btn btn-primary" href="/list" onclick={(e) => go('/list', e)}>
        List an endpoint
      </a>
      <a class="btn btn-outlined" href="/directory" onclick={(e) => go('/directory', e)}>
        Browse the directory
      </a>
    </div>
  </header>

  {#if $x402CatalogState.catalog}
    <div class="x402-stat-row">
      <div class="x402-stat">
        <span class="value">{stats.products}</span>
        <span class="label">live products</span>
      </div>
      <div class="x402-stat">
        <span class="value">{stats.routes}</span>
        <span class="label">routes</span>
      </div>
      <div class="x402-stat">
        <span class="value">{$x402CatalogState.catalog.network_name}</span>
        <span class="label">network</span>
      </div>
    </div>
  {/if}

  {#if $x402CatalogState.failed}
    <p class="muted">
      The live catalog could not be loaded right now -- fetch it directly at
      <code>GET /.well-known/x402</code>.
    </p>
  {:else if !$x402CatalogState.catalog}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else}
    <section class="x402-section-grid">
      {#each sections as section (section.key)}
        <div class="x402-section-card">
          <h2>{section.title}</h2>
          <p>{section.summary}</p>
          <ul class="x402-product-lines">
            {#each section.products as product (product.key)}
              <li class="x402-product-line">
                <div class="x402-product-line-head">
                  <span class="x402-product-name">{product.title}</span>
                  <span class="x402-badge" class:live={product.status === 'live'} class:gated={product.status !== 'live'}>
                    {product.status}
                  </span>
                </div>
                <p>{product.summary}</p>
              </li>
            {/each}
          </ul>
        </div>
      {/each}
    </section>
  {/if}

  <section class="agents" aria-label="For agents">
    <h2>For agents</h2>
    <p class="muted">
      Fetch the machine-readable catalog first -- one document lists every live route, its
      price, and how to pay:
    </p>
    <pre class="curl"><code>GET https://algorand-api.pxke.me/.well-known/x402</code></pre>
    <p class="muted">
      See the <a href="/developers" onclick={(e) => go('/developers', e)}>Developers</a> page for
      the full accordion, OpenAPI document and quickstart.
    </p>
  </section>
</div>

<style>
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 36px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 46rem;
    font-family: var(--font-serif);
    font-size: 17px;
    line-height: 1.55;
  }
  .cta-row {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 16px;
  }
  .agents {
    padding: 16px 18px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--panel);
  }
  .agents h2 {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .agents p {
    margin: 8px 0 0;
    font-family: var(--font-serif);
    font-size: 0.92rem;
    line-height: 1.5;
  }
  .agents a {
    color: var(--accent);
  }
  .curl {
    margin: 8px 0 0;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--accent-soft);
    overflow-x: auto;
  }
  .curl code {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--on-surface);
  }
</style>
