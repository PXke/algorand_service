import { api } from './client'

export type GlossaryTerm = {
  slug: string
  term: string
  definition: string
  aliases?: string[]
  status?: string
}

function itemsOf(body: Record<string, unknown>): GlossaryTerm[] {
  const items = body.items
  if (!Array.isArray(items)) return []
  return items.filter((x): x is GlossaryTerm => !!x && typeof x === 'object') as GlossaryTerm[]
}

export const glossaryApi = {
  async fetchList(): Promise<GlossaryTerm[]> {
    const body = await api.getJson('/api/v1/glossary')
    return itemsOf(body)
  },
  async fetchTerm(slug: string): Promise<GlossaryTerm> {
    return (await api.getJson(`/api/v1/glossary/${encodeURIComponent(slug)}`)) as GlossaryTerm
  },
}
