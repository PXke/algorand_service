import { api } from './client'
import { sessionHeaders } from './auth'

export const suggestionsApi = {
  config: () => api.getJson('/api/v1/suggestions/config'),
  list: (token: string | null) =>
    api.getJson('/api/v1/suggestions', sessionHeaders(token)),
  submit: (
    token: string,
    body: { title: string; body: string; submission_txid: string },
  ) => api.postJson('/api/v1/suggestions', body, sessionHeaders(token)),
}
