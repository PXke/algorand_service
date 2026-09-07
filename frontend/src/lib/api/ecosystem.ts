// Algorand Open Registry (roadmap item 26): free public browse/detail/submit,
// mirroring backend/app/modules/ecosystem/. A NEW, separate concern from the
// paid x402 marketplace client (x402.ts) -- no payment flow here.
import { api } from './client'
import { arrayItemsOf } from './parse'

// Closed category enum (design doc section 4) -- mirrors backend's
// app.modules.ecosystem.models.domain.CATEGORIES 1:1, kept in sync by hand
// (same "own copy, no cross-service import" convention x402.ts's own
// CATEGORIES mirror already documents).
export const ECOSYSTEM_CATEGORIES = [
  'wallets',
  'defi-exchange',
  'defi-lending',
  'nfts',
  'gaming',
  'identity',
  'rwa',
  'payments',
  'infrastructure',
  'devtools',
  'analytics',
  'governance',
  'interop',
  'security',
  'agents',
  'enterprise',
  'media',
  'other',
] as const
export type EcosystemCategory = (typeof ECOSYSTEM_CATEGORIES)[number]

export const ECOSYSTEM_REJECT_REASONS = [
  'not_algorand',
  'unreachable',
  'duplicate',
  'low_quality',
  'spam_or_scam',
  'other',
] as const

export const ECOSYSTEM_NAME_MAX_LENGTH = 60
export const ECOSYSTEM_DESCRIPTION_MIN_LENGTH = 20
export const ECOSYSTEM_DESCRIPTION_MAX_LENGTH = 200
export const ECOSYSTEM_MAX_TAGS = 5

export type EcosystemEntry = {
  slug: string
  name: string
  domain: string
  url: string
  repo_url: string
  x402_url: string
  x402_enabled: boolean
  description: string
  category: string
  tags: string[]
  stage: string
  open_source: boolean
  source: string
  editor_pick: boolean
  last_probed_at_epoch: number
  reachable: boolean | null
  submitted_at_epoch: number
  reviewed_at_epoch: number
}

export type EcosystemSubmitPayload = {
  name: string
  url: string
  description: string
  category: string
  repo_url?: string
  x402_url?: string
  tags?: string[]
  contact?: string
  // Honeypot: left empty by a human, sent as-is.
  website?: string
}

export type EcosystemSubmitResult = {
  ok: boolean
  submission_id: string
  status: string
  status_url?: string
}

export type EcosystemSubmissionStatus = {
  submission_id: string
  status: string
  slug?: string
  reason?: string
}

export const ecosystemApi = {
  async fetchCategories(signal?: AbortSignal): Promise<string[]> {
    const body = await api.getJson('/api/v1/ecosystem/categories', { signal })
    return Array.isArray(body.categories) ? (body.categories as string[]) : []
  },

  async fetchList(
    opts: { category?: string; tag?: string; signal?: AbortSignal } = {},
  ): Promise<EcosystemEntry[]> {
    const params = new URLSearchParams()
    if (opts.category) params.set('category', opts.category)
    if (opts.tag) params.set('tag', opts.tag)
    const qs = params.toString()
    const body = await api.getJson(`/api/v1/ecosystem${qs ? `?${qs}` : ''}`, {
      signal: opts.signal,
    })
    return arrayItemsOf<EcosystemEntry>(body)
  },

  async fetchEntry(slug: string, signal?: AbortSignal): Promise<EcosystemEntry> {
    return (await api.getJson(`/api/v1/ecosystem/${encodeURIComponent(slug)}`, {
      signal,
    })) as unknown as EcosystemEntry
  },

  async fetchSubmissionStatus(
    submissionId: string,
    signal?: AbortSignal,
  ): Promise<EcosystemSubmissionStatus> {
    return (await api.getJson(
      `/api/v1/ecosystem/submissions/${encodeURIComponent(submissionId)}`,
      { signal },
    )) as unknown as EcosystemSubmissionStatus
  },

  async submit(payload: EcosystemSubmitPayload): Promise<EcosystemSubmitResult> {
    return (await api.postJson('/api/v1/ecosystem/submit', payload)) as unknown as EcosystemSubmitResult
  },

  badgeUrl(slug: string): string {
    return `/api/v1/ecosystem/${encodeURIComponent(slug)}/badge.svg`
  },

  badgeMarkdownSnippet(slug: string, name: string): string {
    return `[![Listed on PXke Algorand](${ecosystemApi.badgeUrl(slug)})](https://algorand.pxke.me/registry/${slug} "${name} is listed on the Algorand Open Registry")`
  },
}
