import { api } from './client'
import { arrayItemsOf } from './parse'

export type GlossaryTerm = {
  slug: string
  term: string
  definition: string
  aliases?: string[]
  status?: string
}

export const glossaryApi = {
  async fetchList(): Promise<GlossaryTerm[]> {
    const body = await api.getJson('/api/v1/glossary')
    return arrayItemsOf<GlossaryTerm>(body)
  },
  async fetchTerm(slug: string): Promise<GlossaryTerm> {
    return (await api.getJson(`/api/v1/glossary/${encodeURIComponent(slug)}`)) as GlossaryTerm
  },
}
