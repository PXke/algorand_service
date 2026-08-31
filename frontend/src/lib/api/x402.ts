import { api, type RequestOpts } from './client'
import { arrayItemsOf } from './parse'

// Free, read-only x402 marketplace surfaces. Every optional field below is
// one the backend may omit (older rows, or a paid-only number kept off the
// free surface), so readers must tolerate its absence.

export type X402Listing = {
  url: string
  price: string
  description: string
  assets?: string[]
  tags?: string[]
  category?: string
  term_end_epoch?: number
  payer?: string
  verified_wallet?: string
}

export type X402Placement = {
  link: string
  name?: string
  pitch?: string
  term_end_epoch?: number
  clicks?: number
}

export type X402FeatureRequest = {
  request_id?: string
  title: string
  description?: string
  created_at_epoch?: number
  vote_total?: number
  claims_count?: number
  latest_claimer?: string
}

export type X402GradedEndpoint = {
  url: string
  last_graded_at_epoch?: number
}

export type X402NewsItem = {
  article_id: string
  slug: string
  title: string
  summary?: string
  tags?: string[]
  published_at_epoch?: number
  url: string
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
  price_usd?: number | null
  resource?: string | null
  description: string
  supports_preview?: boolean
}

export type X402Catalog = {
  name: string
  network: string
  network_name: string
  pay_to: string
  routes: X402CatalogRoute[]
}

export const X402_PATHS = {
  catalog: '/api/v1/x402',
  search: '/api/v1/x402/search',
  board: '/api/v1/x402/board',
  features: '/api/v1/x402/features',
  grades: '/api/v1/x402/grades',
  news: '/api/v1/x402/news',
} as const

// Cross-origin (API domain, not the site's same-origin proxy): the
// machine-readable catalog of every live x402 route this backend serves --
// prices, accepted assets, payTo, network. What an agent should fetch
// first, before any of the per-product endpoints above.
export const X402_CATALOG_URL = 'https://algorand-api.pxke.me/api/v1/x402'

// Fixed listing categories (backend LISTING_CATEGORIES, migration 099) --
// a closed enum, not free text, so this mirrors it rather than deriving it
// from a live call.
export const X402_CATEGORIES = [
  'data',
  'ai',
  'finance',
  'identity',
  'storage',
  'compute',
  'social',
  'tooling',
  'other',
] as const

export const x402Api = {
  async catalog(opts?: RequestOpts): Promise<X402Catalog> {
    return (await api.getJson(X402_PATHS.catalog, opts)) as unknown as X402Catalog
  },
  async search(tag: string, category?: string, opts?: RequestOpts): Promise<X402Listing[]> {
    const params = new URLSearchParams()
    const trimmedTag = tag.trim()
    const trimmedCategory = (category ?? '').trim()
    if (trimmedTag) params.set('tag', trimmedTag)
    if (trimmedCategory) params.set('category', trimmedCategory)
    const qs = params.toString()
    const body = await api.getJson(`${X402_PATHS.search}${qs ? `?${qs}` : ''}`, opts)
    return arrayItemsOf<X402Listing>(body)
  },
  async board(opts?: RequestOpts): Promise<X402Placement[]> {
    const body = await api.getJson(X402_PATHS.board, opts)
    return arrayItemsOf<X402Placement>(body)
  },
  async features(opts?: RequestOpts): Promise<X402FeatureRequest[]> {
    const body = await api.getJson(X402_PATHS.features, opts)
    return arrayItemsOf<X402FeatureRequest>(body)
  },
  async grades(opts?: RequestOpts): Promise<X402GradedEndpoint[]> {
    const body = await api.getJson(X402_PATHS.grades, opts)
    return arrayItemsOf<X402GradedEndpoint>(body)
  },
  async news(opts?: RequestOpts): Promise<X402NewsItem[]> {
    const body = await api.getJson(X402_PATHS.news, opts)
    return arrayItemsOf<X402NewsItem>(body)
  },
}
