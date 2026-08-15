import { api, type RequestOpts } from './client'
import { arrayItemsOf } from './parse'

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

/** Short in-memory TTL so revisiting / or /news within a session skips a round trip. */
const FEED_CACHE_TTL_MS = 20_000
const cache = new Map<string, { at: number; value: unknown }>()

async function cachedGet<T>(
  key: string,
  signal: AbortSignal | undefined,
  compute: () => Promise<T>,
): Promise<T> {
  const hit = cache.get(key)
  if (hit && Date.now() - hit.at < FEED_CACHE_TTL_MS) {
    return hit.value as T
  }
  const value = await compute()
  if (!signal?.aborted) {
    cache.set(key, { at: Date.now(), value })
  }
  return value
}

function optsOf(signal?: AbortSignal): RequestOpts | undefined {
  return signal ? { signal } : undefined
}

export const newsApi = {
  async fetchFeedPage(
    opts: {
      limit?: number
      cursor?: string | null
      serviceId?: string
      tag?: string
      lang?: string
      signal?: AbortSignal
    } = {},
  ): Promise<{ items: ArticleItem[]; next_cursor: string | null }> {
    const q = new URLSearchParams()
    q.set('limit', String(opts.limit ?? 30))
    if (opts.cursor) q.set('cursor', opts.cursor)
    if (opts.serviceId) q.set('service_id', opts.serviceId)
    if (opts.tag) q.set('tag', opts.tag)
    if (opts.lang) q.set('lang', opts.lang)
    const path = `/api/v1/news/feed?${q}`
    // Cursor pages are one-shot — only cache the first page.
    if (opts.cursor) {
      const body = await api.getJson(path, optsOf(opts.signal))
      return {
        items: arrayItemsOf<ArticleItem>(body),
        // The API returns next_cursor as a JSON number (a millisecond epoch,
        // see backend cassandra.py's list_feed_page), never a string —
        // root-caused 2026-08-06: a stale `typeof === 'string'` guard here
        // silently discarded every real cursor as null, breaking "load
        // more" pagination everywhere this is called (News/Topic pages,
        // the admin Articles tab) while the initial page still loaded fine.
        next_cursor: typeof body.next_cursor === 'number' ? String(body.next_cursor) : null,
      }
    }
    return cachedGet(path, opts.signal, async () => {
      const body = await api.getJson(path, optsOf(opts.signal))
      return {
        items: arrayItemsOf<ArticleItem>(body),
        // The API returns next_cursor as a JSON number (a millisecond epoch,
        // see backend cassandra.py's list_feed_page), never a string —
        // root-caused 2026-08-06: a stale `typeof === 'string'` guard here
        // silently discarded every real cursor as null, breaking "load
        // more" pagination everywhere this is called (News/Topic pages,
        // the admin Articles tab) while the initial page still loaded fine.
        next_cursor: typeof body.next_cursor === 'number' ? String(body.next_cursor) : null,
      }
    })
  },

  async fetchHot(
    limit = 30,
    rank: 'hot' | 'top' = 'hot',
    lang?: string,
    signal?: AbortSignal,
  ): Promise<ArticleItem[]> {
    const q = new URLSearchParams({ limit: String(limit), rank })
    if (lang) q.set('lang', lang)
    const path = `/api/v1/news/hot?${q}`
    return cachedGet(path, signal, async () => {
      const body = await api.getJson(path, optsOf(signal))
      return arrayItemsOf<ArticleItem>(body)
    })
  },

  async fetchTags(signal?: AbortSignal): Promise<{
    article_count: number
    tags: Array<{ tag: string; count: number; views?: number; last_epoch?: number }>
  }> {
    const path = '/api/v1/news/tags'
    return cachedGet(path, signal, async () => {
      const body = await api.getJson(path, optsOf(signal))
      const tags = Array.isArray(body.tags) ? body.tags : []
      return {
        article_count: Number(body.article_count ?? 0),
        tags: tags as Array<{ tag: string; count: number; views?: number; last_epoch?: number }>,
      }
    })
  },

  async fetchArticle(id: string, lang?: string, signal?: AbortSignal): Promise<ArticleItem> {
    const q = lang ? `?lang=${encodeURIComponent(lang)}` : ''
    return (await api.getJson(`/api/v1/news/articles/${id}${q}`, optsOf(signal))) as ArticleItem
  },

  async fetchPrice(signal?: AbortSignal) {
    return api.getJson('/api/v1/metrics/price', optsOf(signal))
  },

  async fetchMetricsDashboard(signal?: AbortSignal): Promise<{
    tiles: Array<{
      id: string
      label: string
      value: string
      hint?: string | null
      available: boolean
    }>
  }> {
    const path = '/api/v1/metrics/dashboard'
    return cachedGet(path, signal, async () => {
      const body = await api.getJson(path, optsOf(signal))
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
    })
  },

  async fetchPriceHistory(signal?: AbortSignal) {
    return api.getJson('/api/v1/metrics/price/history', optsOf(signal))
  },
}
