<script lang="ts">
  /**
   * The expandable product/route/price list -- originally X402.svelte's
   * "pricing" panel, extracted so the Marketplace page (mechanic products:
   * catalog/directory/board/features/grading) and the Our Endpoints page
   * (every other product) render it identically instead of two copies of
   * the same markup and styles.
   */
  import { messages, t } from '../../lib/i18n'
  import type { X402Catalog } from '../../lib/api/x402'
  import { routePrice, productSummary, type X402CatalogProductGroup } from '../../lib/x402/catalog'

  let { products, catalog }: { products: X402CatalogProductGroup[]; catalog: X402Catalog } =
    $props()

  const totalRoutes = $derived(products.reduce((n, p) => n + p.routes.length, 0))
</script>

<p class="hit-count cat-totals">
  {t($messages, 'x402CatalogTotals', {
    products: products.length,
    routes: totalRoutes,
  })}<span class="sep" aria-hidden="true">·</span>{catalog.network_name}
</p>
<ul class="products">
  {#each products as product (product.key)}
    <li>
      <details class="product">
        <summary>
          <span class="p-title">{product.title}</span>
          <span class="p-meta">{productSummary(product.routes, $messages)}</span>
        </summary>
        <ul class="cat-routes">
          {#each product.routes as route (`${route.method} ${route.path}`)}
            <li class="cat-route">
              <div class="cat-route-head">
                <code>{route.method} {route.path}</code>
                <span class="price" class:free={!route.paid}>{routePrice(route, $messages)}</span>
              </div>
              {#if route.description}
                <p class="cat-desc">{route.description}</p>
              {/if}
            </li>
          {/each}
        </ul>
      </details>
    </li>
  {/each}
</ul>

<style>
  .hit-count {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .hit-count .sep {
    margin-inline: 6px;
    color: var(--subtle);
  }
  .cat-totals {
    margin-top: 10px;
  }
  .products {
    list-style: none;
    margin: 6px 0 0;
    padding: 0;
  }
  .products > li {
    border-bottom: 1px solid var(--border);
  }
  .product > summary {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 16px;
    padding: 8px 0;
    cursor: pointer;
    list-style: none;
  }
  .product > summary::-webkit-details-marker {
    display: none;
  }
  .product > summary::before {
    content: '+';
    font-family: var(--font-mono);
    font-weight: 600;
    color: var(--accent);
    margin-inline-end: 8px;
  }
  .product[open] > summary::before {
    content: '−';
  }
  .p-title {
    flex: 1;
    font-family: var(--font-serif);
    font-size: 0.95rem;
  }
  .p-meta {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    font-variant-numeric: tabular-nums;
    color: var(--muted);
    white-space: nowrap;
  }
  .cat-routes {
    list-style: none;
    margin: 0 0 10px;
    padding: 0 0 0 18px;
  }
  .cat-route {
    padding: 6px 0;
  }
  .cat-route-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    min-width: 0;
  }
  /* A long path breaks after a slash when it can (overflow-wrap only forces
     an arbitrary break when no opportunity exists), not "histor/y". */
  .cat-route-head code {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--on-surface);
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .cat-route .price {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--accent);
  }
  .cat-route .price.free {
    color: var(--muted);
    text-transform: uppercase;
    font-weight: 400;
  }
  .cat-desc {
    margin: 3px 0 0;
    font-family: var(--font-serif);
    font-size: 0.85rem;
    line-height: 1.45;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
</style>
