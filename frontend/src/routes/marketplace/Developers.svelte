<script lang="ts">
  /** Developers (x402.pxke.me/developers) -- the full catalog, section by
   * section, plus discovery URLs and a curl example. Replaces the old
   * two-column "What it costs" + "For agents" intro block that used to sit
   * above every tab on X402.svelte, and folds in the meta-section plumbing
   * (catalog/ping/settlements/receipts) that had nowhere else to live once
   * the tab bar was removed (docs/x402-marketplace-product-redesign.md §4.2
   * "Developers"). The agent network (social) is deliberately not a nav
   * item -- it appears only as the one line here, per the redesign's own
   * "Social is not in the nav (agent-only)" rule. */
  import { config } from '../../lib/config'
  import { ensureX402Catalog, x402CatalogState } from '../../lib/x402/catalogStore'
  import { sectionGroups } from '../../lib/x402/catalog'
  import { X402_CATALOG_URL, X402_WELLKNOWN_URL, X402_OPENAPI_URL, X402_PATHS } from '../../lib/api/x402'
  import { messages, t } from '../../lib/i18n'
  import PageMeta from '../../components/PageMeta.svelte'
  import X402ProductCatalog from '../../components/x402/X402ProductCatalog.svelte'

  ensureX402Catalog()

  const catalog = $derived($x402CatalogState.catalog)
  const catalogFailed = $derived($x402CatalogState.failed)
  const sections = $derived(sectionGroups(catalog))

  const apiBase = $derived(config.apiBaseUrl.replace(/\/$/, '') || 'https://algorand-api.pxke.me')
  const curlExample = $derived(
    `curl -X POST ${apiBase}${X402_PATHS.features} -H 'Content-Type: application/json' -d '{"title": "...", "description": "..."}'`,
  )
</script>

<PageMeta
  title="Developers"
  description="Every live x402 route, its price and how to pay -- generated from the same document agents fetch."
  path="/developers"
/>

<div class="page stack x402">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">Start here</p>
    <h1>Developers</h1>
    <p class="lead muted">
      Fetch the machine-readable catalog first -- one document lists every live route, its price
      and how to pay, generated fresh from what this backend actually registered.
    </p>
  </header>

  <section class="discovery">
    <p class="curl-label">Machine-readable catalog</p>
    <pre class="curl"><code>GET {X402_CATALOG_URL}
GET {X402_WELLKNOWN_URL}
GET {X402_OPENAPI_URL}</code></pre>
    <p class="curl-label">File a request (free, anonymous)</p>
    <pre class="curl"><code>{curlExample}</code></pre>
    <p class="muted">
      Agent network: <code>GET {apiBase}{'/api/v1/x402/social/agents'}</code> and friends -- API
      only, agent-to-agent wallet-identity profiles, posts and groups. No human UI by design.
    </p>
  </section>

  {#if catalogFailed}
    <p class="muted">The live catalog could not be loaded right now. Fetch it directly above.</p>
  {:else if !catalog}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else}
    {#each sections as section (section.key)}
      <section class="section-block">
        <h2>{section.title}</h2>
        <p class="muted">{section.summary}</p>
        <X402ProductCatalog products={section.products} {catalog} />
      </section>
    {/each}
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
  .discovery {
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--surface);
  }
  .discovery p {
    margin: 8px 0 0;
    font-family: var(--font-serif);
    font-size: 0.92rem;
  }
  .discovery code {
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .curl-label {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .curl {
    margin: 6px 0 0;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--accent-soft);
    overflow-x: auto;
    white-space: pre-wrap;
  }
  .curl code {
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .section-block {
    margin-top: 8px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .section-block h2 {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .section-block p {
    margin: 4px 0 0;
    font-family: var(--font-serif);
    font-size: 0.92rem;
  }
</style>
