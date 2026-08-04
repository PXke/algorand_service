import { api } from './client'
import { sessionHeaders } from './auth'

export const suggestionsApi = {
  list: (token: string | null) =>
    api.getJson('/api/v1/suggestions', sessionHeaders(token)),
}
