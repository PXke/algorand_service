import { api, ApiException, type RequestOpts } from './client'
import { arrayItemsOf } from './parse'

// Free, read-only x402 surfaces the storefront (x402.pxke.me) reads. Every
// optional field below is one the backend may omit, so readers must
// tolerate its absence.

export type X402NewsItem = {
  article_id: string
  slug: string
  title: string
  summary?: string
  tags?: string[]
  published_at_epoch?: number
  url: string
}

export type X402NewsTag = {
  tag: string
  count: number
  views?: number
  last_epoch?: number
}

export type X402NewsTags = {
  article_count: number
  tags: X402NewsTag[]
  tag_count_total: number
}

// One row of the free proof-of-volume feed (GET /api/v1/x402/settlements/
// recent) -- see backend x402_catalog/services/catalog.py's
// recent_settlements_json. `eur_value` is null when the oracle had no rate.
export type X402Settlement = {
  tx_id: string
  asset: string | number
  amount: string
  payer: string
  resource: string
  settled_at_epoch: number
  eur_value: number | null
  fulfilled: boolean
}

export type X402SettlementsFeed = {
  items: X402Settlement[]
  note?: string
  verify_at?: string
}

// One route as the machine-readable catalog (GET /api/v1/x402) describes it
// -- see backend app/modules/x402_catalog/services/catalog.py's _route_json.
// Only a paid route carries a `resource` id; that id is what a promo code
// scopes to (the admin promo-codes tab's create-form dropdown source).
export type X402CatalogRoute = {
  product: string
  method: string
  path: string
  paid: boolean
  // The live settings Money string ("$0.10"), already $-prefixed -- not a number.
  price_usd?: string | null
  // Set ("KB") when price_usd is a per-unit rate, not the flat amount charged.
  price_unit?: string | null
  // Server-computed human-scale price string (e.g. "$0.002 / MB / 90 days"),
  // already unit-rescaled -- see backend's `_price_display`. Prefer this for
  // display over hand-concatenating price_usd/price_unit; null for a free
  // route, absent on any catalog document that predates this field.
  price_display?: string | null
  resource?: string | null
  description: string
  input_example?: Record<string, unknown> | null
  supports_preview?: boolean
  supports_promo?: boolean
}

// Every product carries which `sections[]` group it belongs to, a
// one-sentence summary, a live-computed `status` and the one route worth
// calling first. See backend catalog.py's `_product_json`.
export type X402CatalogProduct = {
  key: string
  title: string
  section: string
  summary: string
  status: 'live' | 'gated'
  entry: string
  auth: string
}

// One `sections[]` entry ("Start here", "Services"). See backend catalog.py's `SECTIONS`.
export type X402CatalogSection = {
  key: string
  title: string
  summary: string
}

// One accepted settlement asset, as the catalog's top-level `assets` array
// describes it (backend x402_catalog/services/catalog.py's `_asset_json`).
export type X402CatalogAsset = {
  symbol: string
  asa_id?: number
  decimals?: number
}

export type X402Catalog = {
  name: string
  network: string
  network_name: string
  pay_to: string
  facilitator_url?: string
  merchant?: { id?: string; name?: string; payTo?: string }
  assets?: X402CatalogAsset[]
  products?: X402CatalogProduct[]
  sections?: X402CatalogSection[]
  routes: X402CatalogRoute[]
}

export const X402_PATHS = {
  catalog: '/api/v1/x402',
  settlements: '/api/v1/x402/settlements/recent',
  news: '/api/v1/x402/news',
  newsTags: '/api/v1/x402/news/tags',
  newsSearch: '/api/v1/x402/news/search',
  newsArticle: '/api/v1/x402/news/articles',
  scanUrl: '/api/v1/x402/scan/url',
  storageChallenge: '/api/v1/x402/storage/auth/challenge',
  storageBackups: '/api/v1/x402/storage/backups',
} as const

// Cross-origin (API domain, not the site's same-origin proxy): the
// machine-readable catalog of every live x402 route this backend serves --
// prices, accepted assets, payTo, network. What an agent should fetch
// first, before any of the per-product endpoints above.
export const X402_API_ORIGIN = 'https://algorand-api.pxke.me'
export const X402_CATALOG_URL = `${X402_API_ORIGIN}${X402_PATHS.catalog}`

// The same catalog document at the conventional well-known URI x402
// directories crawl, and as a real OpenAPI 3.1 document -- all three are
// generated from the same live route roster.
export const X402_WELLKNOWN_URL = `${X402_API_ORIGIN}/.well-known/x402`
export const X402_OPENAPI_URL = `${X402_API_ORIGIN}/openapi.json`

// The Python client's tarball index, served by nginx from shared/sdk/ on
// the API host (deploy/nginx/algorand-platform.conf `location ^~ /sdk/`) and
// mirrored under the same path on this site.
export const X402_SDK_URL = '/sdk/'

export const x402Api = {
  async catalog(opts?: RequestOpts): Promise<X402Catalog> {
    return (await api.getJson(X402_PATHS.catalog, opts)) as unknown as X402Catalog
  },
  /** Free: the latest published headlines, newest first. */
  async news(limit?: number, opts?: RequestOpts): Promise<X402NewsItem[]> {
    const qs = limit ? `?limit=${limit}` : ''
    const body = await api.getJson(`${X402_PATHS.news}${qs}`, opts)
    return arrayItemsOf<X402NewsItem>(body)
  },
  /** Free: the live tag taxonomy, sorted by coverage. */
  async newsTags(limit?: number, opts?: RequestOpts): Promise<X402NewsTags> {
    const qs = limit ? `?limit=${limit}` : ''
    return (await api.getJson(`${X402_PATHS.newsTags}${qs}`, opts)) as unknown as X402NewsTags
  },
  /** Free: one published article in full by uuid or slug; null when unpublished or unknown. */
  async article(idOrSlug: string, opts?: RequestOpts): Promise<Record<string, unknown> | null> {
    try {
      return await api.getJson(`${X402_PATHS.newsArticle}/${encodeURIComponent(idOrSlug)}`, opts)
    } catch (e) {
      if (e instanceof ApiException && e.statusCode === 404) return null
      throw e
    }
  },
  /** Free, rate-limited: real (non-operator) settlements across every product, newest first. */
  async settlements(limit?: number, opts?: RequestOpts): Promise<X402SettlementsFeed> {
    const qs = limit ? `?limit=${limit}` : ''
    const body = await api.getJson(`${X402_PATHS.settlements}${qs}`, opts)
    return {
      items: arrayItemsOf<X402Settlement>(body),
      note: typeof body.note === 'string' ? body.note : undefined,
      verify_at: typeof body.verify_at === 'string' ? body.verify_at : undefined,
    }
  },
}
