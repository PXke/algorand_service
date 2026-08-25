import { api } from './client'
import { arrayItemsOf } from './parse'

export type GlossaryTerm = {
  slug: string
  term: string
  definition: string
  aliases?: string[]
  status?: string
}

// One published article that links this term (see backend
// SearchService.list_by_glossary_slug -- a Typesense `glossary_slugs`
// filter, not a term-text match, so it's correct per-locale even though the
// article's own anchor text/title vary by language).
export type GlossaryArticleRef = {
  article_id: string
  title: string
  summary: string
  service_id?: string | null
  published_at_epoch?: number | null
}

export const glossaryApi = {
  async fetchList(): Promise<GlossaryTerm[]> {
    const body = await api.getJson('/api/v1/glossary')
    return arrayItemsOf<GlossaryTerm>(body)
  },
  async fetchTerm(slug: string, lang?: string): Promise<GlossaryTerm> {
    const qs = lang ? `?lang=${encodeURIComponent(lang)}` : ''
    return (await api.getJson(
      `/api/v1/glossary/${encodeURIComponent(slug)}${qs}`,
    )) as GlossaryTerm
  },
  async fetchArticles(slug: string, lang?: string, limit = 12): Promise<GlossaryArticleRef[]> {
    const params = new URLSearchParams({ limit: String(limit) })
    if (lang) params.set('lang', lang)
    const body = await api.getJson(
      `/api/v1/glossary/${encodeURIComponent(slug)}/articles?${params}`,
    )
    return arrayItemsOf<GlossaryArticleRef>(body)
  },
}
