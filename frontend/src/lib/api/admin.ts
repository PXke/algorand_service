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
    fetchAnalytics: (days = 14, signal?: AbortSignal) =>
      api.getJson(`/api/v1/admin/analytics?days=${days}`, { headers: h(), signal }),
    listBriefs: () => api.getJson('/api/v1/admin/briefs', h()),
    createBrief: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/briefs', body, h()),
    assignBriefNow: (briefId: string) =>
      api.postJson(`/api/v1/admin/briefs/${briefId}/assign-now`, {}, h()),
    listPendingFeedBacklog: (signal?: AbortSignal) =>
      api.getJson('/api/v1/admin/pending-feed-backlog', { headers: h(), signal }),
    getTrainingStats: () => api.getJson('/api/v1/admin/training-stats', h()),
    triggerRetrain: () => api.postJson('/api/v1/admin/retrain', {}, h()),
    listDomains: (status = 'all', page = 1, pageSize = 25, signal?: AbortSignal) =>
      api.getJson(
        `/api/v1/admin/domains?status=${encodeURIComponent(status)}&page=${page}&page_size=${pageSize}`,
        { headers: h(), signal },
      ),
    setDomainRelevant: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/domains/set', body, h()),
    listScrapers: () => api.getJson('/api/v1/admin/scrapers', h()),
    runScraper: (action: string) =>
      api.postJson('/api/v1/admin/scrapers/run', { action }, h()),
    celeryWorkers: () => api.getJson('/api/v1/admin/celery', h()),
    healthCheck: (name: string) =>
      api.getJson(`/api/v1/admin/health-checks/${encodeURIComponent(name)}`, h()),
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
    listComposeSessions: (opts?: {
      before?: string | null
      limit?: number
      signal?: AbortSignal
    }) => {
      const q = new URLSearchParams()
      if (opts?.before) q.set('before', opts.before)
      if (opts?.limit) q.set('limit', String(opts.limit))
      const qs = q.toString()
      return api.getJson(`/api/v1/admin/compose-sessions${qs ? `?${qs}` : ''}`, {
        headers: h(),
        signal: opts?.signal,
      })
    },
    getComposeSessionDetail: (sessionId: string, createdAt: string) =>
      api.getJson(
        `/api/v1/admin/compose-sessions/${sessionId}?created_at=${encodeURIComponent(createdAt)}`,
        h(),
      ),
    interrogateComposeSession: (
      sessionId: string,
      body: { question: string; history?: Array<Record<string, string>>; ground_truth?: boolean },
    ) => api.postJson(`/api/v1/admin/compose-sessions/${sessionId}/interrogate`, body, h()),
    recomposeSession: (sessionId: string, sourceUrl: string) =>
      api.postJson(
        `/api/v1/admin/compose-sessions/${sessionId}/recompose`,
        { source_url: sourceUrl },
        h(),
      ),
    listSources: () =>
      api.getJson('/api/v1/registry/services?seeds_only=1', h()),
    upsertSource: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/sources', body, h()),
    deleteSource: (serviceId: string) =>
      api.deleteJson(`/api/v1/admin/sources/${encodeURIComponent(serviceId)}`, h()),
    mergeSources: (body: { target_service_id: string; fold_service_ids: string[] }) =>
      api.postJson('/api/v1/admin/sources/merge', body, h()),
    getArticle: (id: string) =>
      api.getJson(`/api/v1/admin/articles/${id}`, h()),
    patchArticle: (id: string, body: Record<string, unknown>) =>
      api.patchJson(`/api/v1/admin/articles/${id}`, body, h()),
    deleteArticle: (id: string, blockSource = false) =>
      api.deleteJson(
        `/api/v1/admin/articles/${id}${blockSource ? '?block_source=true' : ''}`,
        h(),
      ),
    recomposeArticle: (id: string) =>
      api.postJson(`/api/v1/admin/articles/${id}/recompose`, {}, h()),
    setArticleDraft: (id: string, draft: boolean) =>
      api.postJson(`/api/v1/admin/articles/${id}/draft`, { draft }, h()),
    listDraftArticles: () =>
      api.getJson('/api/v1/admin/articles/drafts', h()),
    listArticleVersions: (id: string) =>
      api.getJson(`/api/v1/admin/articles/${id}/versions`, h()),
    getArticleVersion: (id: string, version: number) =>
      api.getJson(`/api/v1/admin/articles/${id}/versions/${version}`, h()),
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
    listGlossary: () => api.getJson('/api/v1/admin/glossary', h()),
    upsertGlossaryTerm: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/glossary', body, h()),
    deleteGlossaryTerm: (slug: string) =>
      api.deleteJson(`/api/v1/admin/glossary/${encodeURIComponent(slug)}`, h()),
    createShareLink: (articleId: string, label = '') =>
      api.postJson(`/api/v1/admin/articles/${articleId}/share-links`, { label }, h()),
    listShareLinks: (articleId: string) =>
      api.getJson(`/api/v1/admin/articles/${articleId}/share-links`, h()),
    revokeShareLink: (articleId: string, token: string) =>
      api.deleteJson(
        `/api/v1/admin/articles/${articleId}/share-links/${encodeURIComponent(token)}`,
        h(),
      ),
    listArticleComments: (articleId: string) =>
      api.getJson(`/api/v1/admin/articles/${articleId}/comments`, h()),
    deleteComment: (articleId: string, commentId: string) =>
      api.deleteJson(`/api/v1/admin/articles/${articleId}/comments/${commentId}`, h()),
    // Owner-supplied article sources (docs/newspaper-article-sources-design.md,
    // Phase 1) -- attach is storage-only, no Celery dispatch; the next
    // Recompose click loads whatever's active here.
    listArticleSources: (articleId: string, signal?: AbortSignal) =>
      api.getJson(`/api/v1/admin/articles/${articleId}/sources`, { headers: h(), signal }),
    createArticleSource: (articleId: string, body: Record<string, unknown>) =>
      api.postJson(`/api/v1/admin/articles/${articleId}/sources`, body, h()),
    deleteArticleSource: (articleId: string, sourceId: string) =>
      api.deleteJson(
        `/api/v1/admin/articles/${articleId}/sources/${encodeURIComponent(sourceId)}`,
        h(),
      ),
    // Editorial-room artifact system — backs the Queue tab's ranked
    // pending-artifact list, the real-selection lookup, and the
    // pin-for-tomorrow action.
    artifactsToComposePreview: (day?: string, signal?: AbortSignal) =>
      api.getJson(
        `/api/v1/admin/artifacts/to-compose-preview${day ? `?day=${encodeURIComponent(day)}` : ''}`,
        { headers: h(), signal },
      ),
    artifactsToComposeSelected: (day?: string, signal?: AbortSignal) =>
      api.getJson(
        `/api/v1/admin/artifacts/to-compose-selected${day ? `?day=${encodeURIComponent(day)}` : ''}`,
        { headers: h(), signal },
      ),
    pinArtifactForTomorrow: (artifactId: string) =>
      api.postJson(`/api/v1/admin/artifacts/${encodeURIComponent(artifactId)}/pin-for-tomorrow`, {}, h()),
    getArtifactContent: (artifactId: string) =>
      api.getJson(`/api/v1/admin/artifacts/${encodeURIComponent(artifactId)}/content`, h()),
    resetToComposeForDay: (day?: string) =>
      api.postJson(
        `/api/v1/admin/artifacts/to-compose-reset${day ? `?day=${encodeURIComponent(day)}` : ''}`,
        {},
        h(),
      ),
    // x402 promo-code management (CLAUDE.md section 9) -- create/deactivate
    // routes have lived in backend/app/modules/x402_catalog/api/routes.py
    // since the promo mechanism shipped; the list route was added alongside
    // this admin tab.
    listPromoCodes: (signal?: AbortSignal) =>
      api.getJson('/api/v1/admin/x402/promo', { headers: h(), signal }),
    createPromoCode: (body: Record<string, unknown>) =>
      api.postJson('/api/v1/admin/x402/promo', body, h()),
    deletePromoCode: (code: string) =>
      api.deleteJson(`/api/v1/admin/x402/promo?code=${encodeURIComponent(code)}`, h()),
  }
}
