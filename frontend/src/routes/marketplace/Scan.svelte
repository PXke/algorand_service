<script lang="ts">
  /** /scan: the sandboxed URL scan, plus a pay-from-your-wallet panel. */
  import { x402CatalogState } from '../../lib/x402/catalogStore'
  import { productGroup, findRoute, routePriceText } from '../../lib/x402/catalog'
  import { X402_PATHS } from '../../lib/api/x402'
  import X402ProductPage from '../../components/x402/X402ProductPage.svelte'
  import X402ScanTryPanel from '../../components/x402/X402ScanTryPanel.svelte'

  const group = $derived(productGroup($x402CatalogState.catalog, 'scan'))
  const lead = $derived(findRoute(group.routes, X402_PATHS.scanUrl, 'POST'))
  const price = $derived(lead ? routePriceText(lead) : null)
  const live = $derived(group.meta?.status === 'live')
</script>

<X402ProductPage product="scan">
  {#snippet after()}
    {#if live}
      <X402ScanTryPanel {price} />
    {/if}
  {/snippet}
</X402ProductPage>
