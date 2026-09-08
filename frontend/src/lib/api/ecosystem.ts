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

/** Human names for the closed category enum (design doc §4), in enum order. The slug stays the API/URL value. */
export const ECOSYSTEM_CATEGORY_LABELS: Record<EcosystemCategory, string> = {
  wallets: 'Wallets & key management',
  'defi-exchange': 'Exchanges & AMMs',
  'defi-lending': 'Lending, stablecoins & yield',
  nfts: 'NFTs & collectibles',
  gaming: 'Gaming & metaverse',
  identity: 'Identity, names & credentials',
  rwa: 'Real-world assets',
  payments: 'Payments & commerce',
  infrastructure: 'Infrastructure & nodes',
  devtools: 'Developer tools & SDKs',
  analytics: 'Explorers & analytics',
  governance: 'Governance & DAOs',
  interop: 'Oracles & bridges',
  security: 'Security & auditing',
  agents: 'AI & agents',
  enterprise: 'Enterprise & impact',
  media: 'Education & media',
  other: 'Other',
}

export function ecosystemCategoryLabel(slug: string): string {
  return (ECOSYSTEM_CATEGORY_LABELS as Record<string, string>)[slug] ?? slug
}

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
// Multi-line + links, markdown-formatted (2026-09-08, owner ask) -- rendered
// through Markdown.svelte-equivalent {@html} + DOMPurify on the entry page
// (see RegistryEntry.svelte), a plain stripped-to-text preview in the list
// (see stripMarkdownToText below).
export const ECOSYSTEM_DESCRIPTION_MAX_LENGTH = 500
export const ECOSYSTEM_MAX_TAGS = 5
export const ECOSYSTEM_CATEGORY_SUGGESTION_MAX_LENGTH = 60

// "Suggest a change" (owner ask, 2026-09-08: "Yes we need a suggest a
// change") -- mirrors backend's MIN/MAX_REQUEST_MESSAGE_LENGTH.
export const ECOSYSTEM_REQUEST_MESSAGE_MIN_LENGTH = 10
export const ECOSYSTEM_REQUEST_MESSAGE_MAX_LENGTH = 1000

/** Crude markdown-syntax stripper for a compact list preview -- the full render (links, emphasis, multi-line) is reserved for the entry detail page (RegistryEntry.svelte); a dense list of many rows has no room for it. Not a security boundary (that's sanitizeArticleHtml, only used where markdown is actually rendered as HTML) -- this never touches {@html}, so a missed edge case just leaves stray punctuation in a text node, not a vulnerability. */
export function stripMarkdownToText(source: string): string {
  return source
    .replace(/```[\s\S]*?```/g, ' ') // fenced code blocks
    .replace(/`([^`]+)`/g, '$1') // inline code
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1') // images -> alt text
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1') // links -> link text
    .replace(/^#{1,6}\s+/gm, '') // headings
    .replace(/^>\s?/gm, '') // blockquotes
    .replace(/[*_]{1,3}([^*_]+)[*_]{1,3}/g, '$1') // bold/italic
    .replace(/^[-*+]\s+/gm, '') // bullet markers
    .replace(/\s*\n+\s*/g, ' ') // collapse line breaks to a single line
    .replace(/\s{2,}/g, ' ')
    .trim()
}

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
  last_http_status: number
  submitted_at_epoch: number
  reviewed_at_epoch: number
}

export type EcosystemSubmitPayload = {
  name: string
  url: string
  description: string
  category: string
  // Free text, shown only to the admin reviewer -- never validated against
  // the closed category enum. Typically only meaningful when category is
  // "other".
  category_suggestion?: string
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

export type EcosystemRequestKind = 'change' | 'removal'

export type EcosystemRequestPayload = {
  kind: EcosystemRequestKind
  message: string
  contact?: string
  // Honeypot: left empty by a human, sent as-is.
  website?: string
}

export type EcosystemRequestResult = {
  ok: boolean
  request_id: string
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
    return (await api.getJson(`/api/v1/ecosystem/submissions/${encodeURIComponent(submissionId)}`, {
      signal,
    })) as unknown as EcosystemSubmissionStatus
  },

  async submit(payload: EcosystemSubmitPayload): Promise<EcosystemSubmitResult> {
    return (await api.postJson(
      '/api/v1/ecosystem/submit',
      payload,
    )) as unknown as EcosystemSubmitResult
  },

  async submitRequest(
    slug: string,
    payload: EcosystemRequestPayload,
  ): Promise<EcosystemRequestResult> {
    return (await api.postJson(
      `/api/v1/ecosystem/${encodeURIComponent(slug)}/request`,
      payload,
    )) as unknown as EcosystemRequestResult
  },

  badgeUrl(slug: string): string {
    return `/api/v1/ecosystem/${encodeURIComponent(slug)}/badge.svg`
  },

  badgeMarkdownSnippet(slug: string, name: string): string {
    return `[![Listed on PXke Algorand](${ecosystemApi.badgeUrl(slug)})](https://algorand.pxke.me/registry/${slug} "${name} is listed on the Algorand Open Registry")`
  },
}
