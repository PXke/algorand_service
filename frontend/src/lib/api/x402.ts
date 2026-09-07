import { api, ApiException, type RequestOpts } from './client'
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
  // Only present on the detail read (GET /api/v1/x402/listings?url=) --
  // omitted from the search feed to keep that response small.
  schema?: Record<string, unknown> | null
  created_at_epoch?: number
  settlement_tx_id?: string
  reimburses?: boolean
  contact?: string
}

// The newest unpaid probe of one listed endpoint -- see backend
// x402_directory/api/routes.py's `_probe_json`.
export type X402ListingProbe = {
  probed_at_epoch: number
  reachable: boolean
  http_status: number
  latency_ms: number
  served_valid_402: boolean
  payto_seen: string
  error: string
}

export type X402ListingDetail = {
  listing: X402Listing
  probe: X402ListingProbe | null
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
  // The live settings Money string ("$0.10"), already $-prefixed -- not a number.
  price_usd?: string | null
  // Set ("MB") when price_usd is a per-unit rate, not the flat amount charged.
  price_unit?: string | null
  // Server-computed human-scale price string (e.g. "$0.002 / MB / 90 days"),
  // already unit-rescaled -- see backend's `_price_display`. Prefer this for
  // display over hand-concatenating price_usd/price_unit; null for a free
  // route, absent on any catalog document that predates this field.
  price_display?: string | null
  resource?: string | null
  description: string
  supports_preview?: boolean
}

// v2 (2026-09-07, see docs/x402-marketplace-product-redesign.md §3.4): every
// product now carries which `sections[]` group it belongs to, a one-sentence
// summary, a live-computed `status` and the one route worth calling first --
// the fields the marketplace nav/pages below read instead of a hand-
// maintained key list. See backend catalog.py's `_product_json`.
export type X402CatalogProduct = {
  key: string
  title: string
  section: string
  summary: string
  status: 'live' | 'gated'
  entry: string
  auth: string
}

// One `sections[]` entry -- a visitor-intent grouping ("Start here", "Get
// found", ...), not an author boundary. See backend catalog.py's `SECTIONS`.
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
  assets?: X402CatalogAsset[]
  products?: X402CatalogProduct[]
  // v2 field; absent on a catalog document that predates it.
  sections?: X402CatalogSection[]
  routes: X402CatalogRoute[]
}

// Free per-URL grade existence-tier summary -- see backend
// x402_grading/api/routes.py's `_summary_json`. Count and recency only,
// never a score (the score itself is a paid read).
export type X402GradeSummary = {
  url_hash: string
  url: string
  count: number
  last_graded_at_epoch?: number
  truncated?: boolean
}

export type X402ProbeHistory = {
  url: string
  history: X402ListingProbe[]
}

export const X402_PATHS = {
  catalog: '/api/v1/x402',
  search: '/api/v1/x402/search',
  board: '/api/v1/x402/board',
  features: '/api/v1/x402/features',
  grades: '/api/v1/x402/grades',
  news: '/api/v1/x402/news',
  list: '/api/v1/x402/list',
  listings: '/api/v1/x402/listings',
  // Free trust-layer reads the marketplace redesign's listing detail page
  // and Trust page surface -- both already computed server-side, neither
  // new backend work (docs/x402-marketplace-product-redesign.md §2.1: an
  // agent should be able to rank a listing in one call).
  probeHistory: '/api/v1/x402/directory/probe/history',
  gradesSummary: '/api/v1/x402/grades/summary',
  // The marketplace's lowest-price payment-test route -- lives in the
  // catalog's "catalog" product bucket server-side, but is a direct-utility
  // call in its own right, so the Our Endpoints page pulls it out by path
  // (see lib/x402/catalog.ts's ourEndpointProducts).
  ping: '/api/v1/x402/ping',
} as const

// Cross-origin (API domain, not the site's same-origin proxy): the
// machine-readable catalog of every live x402 route this backend serves --
// prices, accepted assets, payTo, network. What an agent should fetch
// first, before any of the per-product endpoints above.
export const X402_CATALOG_URL = 'https://algorand-api.pxke.me/api/v1/x402'

// The same catalog document at the conventional well-known URI x402
// directories crawl, and as a real OpenAPI 3.1 document -- all three are
// generated from the same live route roster.
export const X402_WELLKNOWN_URL = 'https://algorand-api.pxke.me/.well-known/x402'
export const X402_OPENAPI_URL = 'https://algorand-api.pxke.me/openapi.json'

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

// ── POST /api/v1/x402/list field bounds ─────────────────────────────────────
// Mirrors backend/app/schemas.py's X402ListingRequest and
// x402_directory/services/listing_service.py's own constants exactly --
// there is no live JSON-Schema surface for this write route (its request
// body is documented in the 402 offer's discovery extension, which is only
// reachable by triggering a real 402 challenge, and in openapi.json's
// requestBody as a bare `{"type": "object"}` with an example, not a full
// schema), so this mirrors the read backend source the same way
// X402_CATEGORIES above already does. Keep in sync with those two files by
// hand if either changes.
export const X402_LISTING_URL_MIN_LENGTH = 8
export const X402_LISTING_URL_MAX_LENGTH = 2048
export const X402_LISTING_PRICE_MAX_LENGTH = 64
export const X402_LISTING_DESCRIPTION_MAX_LENGTH = 2000
export const X402_LISTING_ASSETS_MAX_ITEMS = 16
export const X402_LISTING_ASSET_MAX_LENGTH = 64
export const X402_LISTING_TAGS_MAX_ITEMS = 16
export const X402_LISTING_TAG_MAX_LENGTH = 64
export const X402_LISTING_SCHEMA_MAX_BYTES = 4096
export const X402_LISTING_CONTACT_MAX_LENGTH = 256
export const X402_LISTING_CATEGORY_TAG_PREFIX = 'category:'

export type X402ListingDraft = {
  url: string
  price: string
  description: string
  assets: string[]
  tags: string[]
  category: string
  schema: Record<string, unknown> | null
  reimburses: boolean
  contact: string
}

/**
 * Client-side mirror of listing_service.normalize_url: lowercase scheme and
 * host (case-insensitive per RFC 3986), drop the fragment, leave path/query
 * exactly as given. Returns the error listing_service would raise so the
 * form can show the same message a real submission would get, before any
 * money is spent finding out.
 */
export function normalizeListingUrl(raw: string): { url: string } | { error: string } {
  const trimmed = raw.trim()
  if (
    !trimmed ||
    trimmed.length < X402_LISTING_URL_MIN_LENGTH ||
    trimmed.length > X402_LISTING_URL_MAX_LENGTH
  ) {
    return {
      error: `url must be ${X402_LISTING_URL_MIN_LENGTH}-${X402_LISTING_URL_MAX_LENGTH} characters`,
    }
  }
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    return { error: 'url must be a valid, fully-qualified http(s) URL' }
  }
  const scheme = parsed.protocol.replace(/:$/, '').toLowerCase()
  if (scheme !== 'http' && scheme !== 'https') {
    return { error: 'url must be http or https' }
  }
  if (!parsed.hostname) {
    return { error: 'url must include a host' }
  }
  parsed.hash = ''
  return { url: parsed.toString() }
}

/** Client-side mirror of listing_service.normalize_tag: trimmed and lowercased. */
export function normalizeListingTag(raw: string): string {
  return raw.trim().toLowerCase()
}

/**
 * Client-side mirror of listing_service.validate_tags: blank tags dropped,
 * duplicates folded, sorted -- the exact form the server stores under and
 * `?tag=` matches against. Returns the DirectoryError message for a
 * reserved `category:`-prefixed tag rather than silently dropping it, so
 * the form can surface it as a blocking error the same way the real
 * request would.
 */
export function validateListingTags(rawTags: string[]): { tags: string[] } | { error: string } {
  const normalized = [...new Set(rawTags.map(normalizeListingTag).filter(Boolean))].sort()
  const reserved = normalized.find((tag) => tag.startsWith(X402_LISTING_CATEGORY_TAG_PREFIX))
  if (reserved) {
    return {
      error: `tag \`${reserved}\` is reserved: tags starting with \`${X402_LISTING_CATEGORY_TAG_PREFIX}\` are written by the directory itself from \`category\``,
    }
  }
  return { tags: normalized }
}

/** Client-side mirror of the trim+dedupe+sort `create()` applies to `assets` (never lowercased -- an asset symbol's case is meaningful). */
export function normalizeListingAssets(rawAssets: string[]): string[] {
  return [...new Set(rawAssets.map((a) => a.trim()).filter(Boolean))].sort()
}

/** UTF-8 byte length of a JSON-encoded value, for the same size check encode_schema() enforces server-side. */
export function jsonByteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).length
}

export const x402Api = {
  async catalog(opts?: RequestOpts): Promise<X402Catalog> {
    return (await api.getJson(X402_PATHS.catalog, opts)) as unknown as X402Catalog
  },
  /** Free: the directory's full record of one URL, or null if it is not (or no longer) listed. */
  async listingDetail(url: string, opts?: RequestOpts): Promise<X402ListingDetail | null> {
    try {
      const body = await api.getJson(
        `${X402_PATHS.listings}?url=${encodeURIComponent(url)}`,
        opts,
      )
      return body as unknown as X402ListingDetail
    } catch (e) {
      if (e instanceof ApiException && e.statusCode === 404) return null
      throw e
    }
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
  /** Free: up to `limit` past probe results for one listed URL, newest first; null if the URL is not (or no longer) listed. */
  async probeHistory(
    url: string,
    limit?: number,
    opts?: RequestOpts,
  ): Promise<X402ProbeHistory | null> {
    try {
      const params = new URLSearchParams({ url })
      if (limit) params.set('limit', String(limit))
      const body = await api.getJson(`${X402_PATHS.probeHistory}?${params.toString()}`, opts)
      return body as unknown as X402ProbeHistory
    } catch (e) {
      if (e instanceof ApiException && e.statusCode === 404) return null
      throw e
    }
  },
  /** Free: grade count and last-graded time for one URL, never a score; null if nobody has graded it yet. */
  async gradeSummary(url: string, opts?: RequestOpts): Promise<X402GradeSummary | null> {
    try {
      const body = await api.getJson(
        `${X402_PATHS.gradesSummary}?url=${encodeURIComponent(url)}`,
        opts,
      )
      return body as unknown as X402GradeSummary
    } catch (e) {
      if (e instanceof ApiException && e.statusCode === 404) return null
      throw e
    }
  },
  /** Free, anonymous: file one feature request. */
  async fileFeatureRequest(
    title: string,
    description: string,
    opts?: RequestOpts,
  ): Promise<Record<string, unknown>> {
    return api.postJson(X402_PATHS.features, { title, description }, opts)
  },
}
