import { api } from './client'

export const searchApi = {
  async search(q: string, limit = 20, serviceId?: string) {
    const params = new URLSearchParams({ q, limit: String(limit) })
    if (serviceId) params.set('service_id', serviceId)
    const body = await api.getJson(`/api/v1/search?${params}`)
    const items = Array.isArray(body.items) ? body.items : []
    return {
      engine: String(body.engine ?? ''),
      items: items as Array<Record<string, unknown>>,
    }
  },
}
