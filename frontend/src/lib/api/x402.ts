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
}

export type X402GradedEndpoint = {
  url: string
  last_graded_at_epoch?: number
}

export const X402_PATHS = {
  search: '/api/v1/x402/search',
  board: '/api/v1/x402/board',
  features: '/api/v1/x402/features',
  grades: '/api/v1/x402/grades',
} as const

export const x402Api = {
  async search(tag: string, opts?: RequestOpts): Promise<X402Listing[]> {
    const trimmed = tag.trim()
    const qs = trimmed ? `?tag=${encodeURIComponent(trimmed)}` : ''
    const body = await api.getJson(`${X402_PATHS.search}${qs}`, opts)
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
}
