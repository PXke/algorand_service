import { t } from '../i18n'
import type { X402Catalog, X402CatalogProduct, X402CatalogRoute } from '../api/x402'

// One grouping of the live catalog document by `product`, shared by every
// storefront page (routes/marketplace/*.svelte) and the admin promo-codes
// tab -- one implementation, not a copy per page (CLAUDE.md §3).

/** The three products the storefront sells, in the order the site presents them. */
export const X402_PRODUCT_KEYS = ['scan', 'storage', 'news'] as const
export type X402ProductKey = (typeof X402_PRODUCT_KEYS)[number]

export function isX402ProductKey(key: string): key is X402ProductKey {
  return (X402_PRODUCT_KEYS as readonly string[]).includes(key)
}

export type X402ProductGroup = {
  key: X402ProductKey
  /** The catalog's own `products[]` entry, or null when the document lacks it (an older document, or a product the backend has not registered). */
  meta: X402CatalogProduct | null
  routes: X402CatalogRoute[]
}

function routesByProduct(catalog: X402Catalog): Map<string, X402CatalogRoute[]> {
  const byKey = new Map<string, X402CatalogRoute[]>()
  for (const route of catalog.routes ?? []) {
    const group = byKey.get(route.product)
    if (group) group.push(route)
    else byKey.set(route.product, [route])
  }
  return byKey
}

/** The catalog's routes and product entry for one of the kept product keys. Empty routes when the catalog is null. */
export function productGroup(catalog: X402Catalog | null, key: X402ProductKey): X402ProductGroup {
  if (!catalog) return { key, meta: null, routes: [] }
  return {
    key,
    meta: catalog.products?.find((p) => p.key === key) ?? null,
    routes: routesByProduct(catalog).get(key) ?? [],
  }
}

/** Every kept product, site order, each with its catalog entry and routes. */
export function productGroups(catalog: X402Catalog | null): X402ProductGroup[] {
  return X402_PRODUCT_KEYS.map((key) => productGroup(catalog, key))
}

// Shared price-formatting seam. Prefers the server-computed `price_display`
// (already unit-rescaled, e.g. "$0.002 / MB / 90 days" -- see backend's
// `_price_display`) and falls back to the raw price_usd/price_unit
// concatenation for a route that hasn't set it. Returns null for a free route.
export function routePriceText(route: X402CatalogRoute): string | null {
  if (!route.paid || !route.price_usd) return null
  if (route.price_display) return route.price_display
  return route.price_unit ? `${route.price_usd} / ${route.price_unit}` : route.price_usd
}

export function routePrice(route: X402CatalogRoute, msgs: Record<string, string>): string {
  return routePriceText(route) ?? t(msgs, 'x402Free')
}

/** The lowest paid price among `routes`, as the catalog's own display string; null when every route is free. */
export function cheapestPriceText(routes: X402CatalogRoute[]): string | null {
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
  return cheapest ? routePriceText(cheapest) : null
}

/** The one route of a group matching `path` (any method), or null. */
export function findRoute(routes: X402CatalogRoute[], path: string, method?: string): X402CatalogRoute | null {
  return (
    routes.find((r) => r.path === path && (!method || r.method.toUpperCase() === method.toUpperCase())) ??
    null
  )
}
