import { api } from './client'

export type ArticleItem = {
  article_id: string
  title?: string
  summary?: string
  body?: string
  published_at_epoch?: number
  service_id?: string
  image_url?: string
  /** Permanent URL slug; absent on rows written before migration 056. */
  slug?: string | null
  source_url?: string
  tags?: string[]
  trigger_kind?: string
  trigger_txid?: string
  trigger_round?: number
  views?: number
}

function itemsOf(body: Record<string, unknown>): ArticleItem[] {
  const items = body.items
  if (!Array.isArray(items)) return []
  return items.filter((x): x is ArticleItem => !!x && typeof x === 'object') as ArticleItem[]
}

export const newsApi = {
  async fetchFeedPage(opts: {
    limit?: number
    cursor?: string | null
    serviceId?: string
    tag?: string
    lang?: string
  } = {}): Promise<{ items: ArticleItem[]; next_cursor: string | null }> {
    const q = new URLSearchParams()
    q.set('limit', String(opts.limit ?? 30))
    if (opts.cursor) q.set('cursor', opts.cursor)
    if (opts.serviceId) q.set('service_id', opts.serviceId)
    if (opts.tag) q.set('tag', opts.tag)
    if (opts.lang) q.set('lang', opts.lang)
    const body = await api.getJson(`/api/v1/news/feed?${q}`)
    return {
      items: itemsOf(body),
      next_cursor: typeof body.next_cursor === 'string' ? body.next_cursor : null,
    }
  },

  async fetchHot(limit = 30, rank: 'hot' | 'top' = 'hot', lang?: string): Promise<ArticleItem[]> {
    const q = new URLSearchParams({ limit: String(limit), rank })
    if (lang) q.set('lang', lang)
    const body = await api.getJson(`/api/v1/news/hot?${q}`)
    return itemsOf(body)
  },

  async fetchTags(): Promise<{
    article_count: number
    tags: Array<{ tag: string; count: number; views?: number; last_epoch?: number }>
  }> {
    const body = await api.getJson('/api/v1/news/tags')
    const tags = Array.isArray(body.tags) ? body.tags : []
    return {
      article_count: Number(body.article_count ?? 0),
      tags: tags as Array<{ tag: string; count: number; views?: number; last_epoch?: number }>,
    }
  },

  async fetchArticle(id: string, lang?: string): Promise<ArticleItem> {
    const q = lang ? `?lang=${encodeURIComponent(lang)}` : ''
    return (await api.getJson(`/api/v1/news/articles/${id}${q}`)) as ArticleItem
  },

  async fetchPlacements(slot = 'feed', limit = 5) {
    const body = await api.getJson(
      `/api/v1/news/placements?slot=${encodeURIComponent(slot)}&limit=${limit}`,
    )
    return itemsOf(body) as unknown as Array<Record<string, unknown>>
  },

  async fetchPrice() {
    return api.getJson('/api/v1/metrics/price')
  },

  async fetchMetricsDashboard(): Promise<{
    tiles: Array<{
      id: string
      label: string
      value: string
      hint?: string | null
      available: boolean
    }>
  }> {
    const body = await api.getJson('/api/v1/metrics/dashboard')
    const tiles = Array.isArray(body.tiles) ? body.tiles : []
    return {
      tiles: tiles as Array<{
        id: string
        label: string
        value: string
        hint?: string | null
        available: boolean
      }>,
    }
  },

  async fetchPriceHistory() {
    return api.getJson('/api/v1/metrics/price/history')
  },
}
