import { t } from '../i18n'
import { X402_PATHS, type X402Catalog, type X402CatalogRoute } from '../api/x402'

// Shared between the Marketplace page (routes/X402.svelte) and the Our
// Endpoints page (routes/X402Endpoints.svelte) -- both group the same live
// catalog document by `product`, they just keep opposite halves of it. One
// grouping/pricing implementation, not two copies that can drift.

export type X402CatalogProductGroup = {
  key: string
  title: string
  routes: X402CatalogRoute[]
}

// Product keys that are marketplace *mechanics* -- discovery, vetting and
// demand-signalling for the ecosystem of OTHER agents' listed endpoints --
// rather than a PXke product you call directly to get work done. These stay
// on the Marketplace page. Everything else in the live catalog is a PXke
// product and belongs on the Our Endpoints page. Kept as an exclusion list
// (not a hand-maintained inclusion list) so a newly shipped product still
// needs no frontend edit to land on the right page.
const MARKETPLACE_MECHANIC_PRODUCT_KEYS = new Set([
  'catalog',
  'directory',
  'board',
  'features',
  'grading',
])

function groupByProduct(catalog: X402Catalog): Map<string, X402CatalogRoute[]> {
  const byKey = new Map<string, X402CatalogRoute[]>()
  for (const route of catalog.routes ?? []) {
    const group = byKey.get(route.product)
    if (group) group.push(route)
    else byKey.set(route.product, [route])
  }
  return byKey
}

function titleFor(catalog: X402Catalog, key: string): string {
  return catalog.products?.find((p) => p.key === key)?.title ?? key
}

/** Every marketplace-mechanic product+its routes (catalog/directory/board/features/grading) -- what the Marketplace page's pricing panel shows. */
export function marketplaceMechanicProducts(catalog: X402Catalog | null): X402CatalogProductGroup[] {
  if (!catalog) return []
  const byKey = groupByProduct(catalog)
  return [...byKey.entries()]
    .filter(([key]) => MARKETPLACE_MECHANIC_PRODUCT_KEYS.has(key))
    .map(([key, routes]) => ({ key, title: titleFor(catalog, key), routes }))
}

/**
 * Every PXke direct-utility product route -- what the Our Endpoints page
 * shows. This is everything NOT a marketplace mechanic, plus the `ping`
 * payment-test route on its own: `ping` technically lives inside the
 * "catalog" product bucket alongside two marketplace-plumbing routes (the
 * catalog document itself, and the settlements feed) that stay on the
 * Marketplace page, but it is itself a direct-utility call (prove your x402
 * client works before spending real money on a real product), not a
 * discovery/vetting mechanic, so it is pulled out rather than dropped with
 * the rest of that bucket.
 */
export function ourEndpointProducts(
  catalog: X402Catalog | null,
  msgs: Record<string, string>,
): X402CatalogProductGroup[] {
  if (!catalog) return []
  const byKey = groupByProduct(catalog)
  const groups = [...byKey.entries()]
    .filter(([key]) => !MARKETPLACE_MECHANIC_PRODUCT_KEYS.has(key))
    .map(([key, routes]) => ({ key, title: titleFor(catalog, key), routes }))
  const ping = (byKey.get('catalog') ?? []).find((r) => r.path === X402_PATHS.ping)
  if (ping) groups.push({ key: 'ping', title: t(msgs, 'x402EndpointsPingTitle'), routes: [ping] })
  return groups
}

// Shared price-formatting seam (CLAUDE.md section 5: reuse a shared helper
// rather than hand-rolling per component). Prefers the server-computed
// `price_display` (already unit-rescaled, e.g. "$0.002 / MB / 90 days" --
// see backend's `_price_display`) and falls back to the raw
// price_usd/price_unit concatenation for a route that hasn't set it (older
// catalog documents, or a route type price_display doesn't cover yet).
// Returns null for a free/priceless route.
export function routePriceText(route: X402CatalogRoute): string | null {
  if (!route.paid || !route.price_usd) return null
  if (route.price_display) return route.price_display
  return route.price_unit ? `${route.price_usd} / ${route.price_unit}` : route.price_usd
}

export function routePrice(route: X402CatalogRoute, msgs: Record<string, string>): string {
  return routePriceText(route) ?? t(msgs, 'x402Free')
}

// ── v2 catalog-driven grouping (2026-09-07 marketplace redesign) ───────────
// Everything below reads `sections[]` / `products[].section` -- the
// server-computed grouping the 2026-09-07 UX audit and redesign asked for
// (docs/x402-marketplace-ux-audit.md §2.2/2.4, product-redesign.md §3.4) --
// instead of the hand-maintained MARKETPLACE_MECHANIC_PRODUCT_KEYS exclusion
// list above. Used only by the new marketplace-build route components
// (routes/marketplace/*.svelte); the legacy News-build hub page
// (routes/X402.svelte, routes/X402Endpoints.svelte) keeps using the helpers
// above unchanged.

export type X402CatalogProductGroupWithMeta = X402CatalogProductGroup & {
  section: string
  summary: string
  status: string
  entry: string
}

export type X402SectionGroup = {
  key: string
  title: string
  summary: string
  products: X402CatalogProductGroupWithMeta[]
}

/** Every catalog product, tagged with its own status/summary/entry, grouped by product (not by route) -- what a section card or the Developers accordion iterates. */
export function productGroupsWithMeta(
  catalog: X402Catalog | null,
): X402CatalogProductGroupWithMeta[] {
  if (!catalog) return []
  const byKey = groupByProduct(catalog)
  return (catalog.products ?? []).map((p) => ({
    key: p.key,
    title: p.title,
    section: p.section,
    summary: p.summary,
    status: p.status,
    entry: p.entry,
    routes: byKey.get(p.key) ?? [],
  }))
}

/** Every catalog section in server order, each carrying its own products (live and gated) and their routes. */
export function sectionGroups(catalog: X402Catalog | null): X402SectionGroup[] {
  if (!catalog || !catalog.sections) return []
  const products = productGroupsWithMeta(catalog)
  return catalog.sections.map((section) => ({
    key: section.key,
    title: section.title,
    summary: section.summary,
    products: products.filter((p) => p.section === section.key),
  }))
}

/** Flat product+route groups for one or more sections, roster order -- what a single-section page (Trust, Services, Developers) feeds to X402ProductCatalog. */
export function productsForSections(
  catalog: X402Catalog | null,
  sectionKeys: readonly string[],
): X402CatalogProductGroup[] {
  const keys = new Set(sectionKeys)
  return productGroupsWithMeta(catalog).filter((p) => keys.has(p.section))
}

/** {liveProducts, routes} across the whole catalog -- the Overview page's live-numbers stamp. */
export function catalogStats(catalog: X402Catalog | null): { products: number; routes: number } {
  if (!catalog) return { products: 0, routes: 0 }
  const live = (catalog.products ?? []).filter((p) => p.status === 'live')
  return { products: live.length, routes: catalog.routes.length }
}

// "N routes · from $0.02": the lowest paid price in the group, shown as the
// catalog's own Money string (so "$0.001" never re-renders as "$0.001000"
// or "$0.1") -- a per-unit rate keeps its unit.
export function productSummary(routes: X402CatalogRoute[], msgs: Record<string, string>): string {
  let cheapest: X402CatalogRoute | null = null
  let cheapestValue = Number.POSITIVE_INFINITY
  for (const r of routes) {
    if (!r.paid || !r.price_usd) continue
    const n = Number.parseFloat(r.price_usd.replace('$', ''))
    if (Number.isFinite(n) && n < cheapestValue) {
      cheapest = r
      cheapestValue = n
    }
  }
  const count = t(msgs, 'x402RoutesCount', { count: routes.length })
  if (!cheapest) return `${count} · ${t(msgs, 'x402Free')}`
  return `${count} · ${t(msgs, 'x402FromPrice', { price: routePrice(cheapest, msgs) })}`
}
