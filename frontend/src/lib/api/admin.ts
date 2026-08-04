import { api, type JsonHeaders } from './client'

function adminHeaders(wallet: string, token: string | null): JsonHeaders {
  return {
    'X-Admin-Wallet': wallet,
    ...(token ? { 'x-session-token': token } : {}),
  }
}

export type AdminApi = ReturnType<typeof createAdminApi>

export function createAdminApi(wallet: string, token: string | null) {
  const h = () => adminHeaders(wallet, token)
  return {
    listContactMessages: () => api.getJson('/api/v1/admin/contact-messages', h()),
    fetchAnalytics: (days = 14) =>
      api.getJson(`/api/v1/admin/analytics?days=${days}`, h()),
    listBriefs: () => api.getJson('/api/v1/admin/briefs', h()),
    createBrief: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/briefs', body, h()),
    assignBriefNow: (briefId: string) =>
      api.postJson(`/api/v1/admin/briefs/${briefId}/assign-now`, {}, h()),
    listPublishQueue: (limit = 200) =>
      api.getJson(`/api/v1/admin/publish-queue?limit=${limit}`, h()),
    listPendingFeedBacklog: () =>
      api.getJson('/api/v1/admin/pending-feed-backlog', h()),
    publishQueueBreakdown: (queueId: string) =>
      api.getJson(`/api/v1/admin/publish-queue/${queueId}/breakdown`, h()),
    composeQueueItemNext: (queueId: string) =>
      api.postJson(`/api/v1/admin/publish-queue/${queueId}/compose-next`, {}, h()),
    deadEndQueueItemDomain: (queueId: string) =>
      api.postJson(`/api/v1/admin/publish-queue/${queueId}/dead-end`, {}, h()),
    getTrainingStats: () => api.getJson('/api/v1/admin/training-stats', h()),
    triggerRetrain: () => api.postJson('/api/v1/admin/retrain', {}, h()),
    listDomains: (status = 'all', page = 1, pageSize = 25) =>
      api.getJson(
        `/api/v1/admin/domains?status=${encodeURIComponent(status)}&page=${page}&page_size=${pageSize}`,
        h(),
      ),
    setDomainRelevant: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/domains/set', body, h()),
    listScrapers: () => api.getJson('/api/v1/admin/scrapers', h()),
    runScraper: (action: string) =>
      api.postJson('/api/v1/admin/scrapers/run', { action }, h()),
    celeryWorkers: () => api.getJson('/api/v1/admin/celery', h()),
    listClassifierReviews: () =>
      api.getJson('/api/v1/admin/classifier-reviews', h()),
    submitClassifierFeedback: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/classifier-feedback', body, h()),
    composeNextReview: () =>
      api.postJson('/api/v1/admin/classifier-reviews/compose-next', {}, h()),
    clearClassifierReviews: () =>
      api.postJson('/api/v1/admin/classifier-reviews/clear', {}, h()),
    listToolSuggestions: () =>
      api.getJson('/api/v1/admin/tool-suggestions', h()),
    listComposeFeedback: () =>
      api.getJson('/api/v1/admin/compose-feedback', h()),
    listComposeSessions: (opts?: { before?: string | null; limit?: number }) => {
      const q = new URLSearchParams()
      if (opts?.before) q.set('before', opts.before)
      if (opts?.limit) q.set('limit', String(opts.limit))
      const qs = q.toString()
      return api.getJson(`/api/v1/admin/compose-sessions${qs ? `?${qs}` : ''}`, h())
    },
    getComposeSessionDetail: (sessionId: string, createdAt: string) =>
      api.getJson(
        `/api/v1/admin/compose-sessions/${sessionId}?created_at=${encodeURIComponent(createdAt)}`,
        h(),
      ),
    listGatekeeperAnchors: () =>
      api.getJson('/api/v1/admin/gatekeeper/anchors', h()),
    getGatekeeperValidationReport: () =>
      api.getJson('/api/v1/admin/gatekeeper/validation-report', h()),
    runGatekeeperValidation: () =>
      api.postJson('/api/v1/admin/gatekeeper/validate', {}, h()),
    addGatekeeperAnchor: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/gatekeeper/anchor', body, h()),
    listSources: () =>
      api.getJson('/api/v1/registry/services?seeds_only=1', h()),
    upsertSource: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/sources', body, h()),
    deleteSource: (serviceId: string) =>
      api.deleteJson(`/api/v1/admin/sources/${encodeURIComponent(serviceId)}`, h()),
    mergeSources: (body: { target_service_id: string; fold_service_ids: string[] }) =>
      api.postJson('/api/v1/admin/sources/merge', body, h()),
    patchArticle: (id: string, body: Record<string, unknown>) =>
      api.patchJson(`/api/v1/admin/articles/${id}`, body, h()),
    deleteArticle: (id: string, blockSource = false) =>
      api.deleteJson(
        `/api/v1/admin/articles/${id}${blockSource ? '?block_source=true' : ''}`,
        h(),
      ),
    recomposeReview: (body: { review_id: string }) =>
      api.postJson('/api/v1/admin/classifier-reviews/recompose', body, h()),
    investigationFindings: (url: string) =>
      api.getJson(
        `/api/v1/admin/investigations?url=${encodeURIComponent(url)}`,
        h(),
      ),
    backfillArticleTranslations: (limit = 50) =>
      api.postJson('/api/v1/admin/translations/backfill', { limit }, h()),
    clearDomains: () => api.postJson('/api/v1/admin/domains/clear', {}, h()),
    resetPipeline: () => api.postJson('/api/v1/admin/articles/reset', {}, h()),
    healthReady: () => api.getJson('/health/ready'),
    listGlossary: () => api.getJson('/api/v1/admin/glossary', h()),
    upsertGlossaryTerm: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/glossary', body, h()),
    deleteGlossaryTerm: (slug: string) =>
      api.deleteJson(`/api/v1/admin/glossary/${encodeURIComponent(slug)}`, h()),
  }
}
