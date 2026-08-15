import { api } from './client'

export type CommentQuoteAnchor = {
  quote: string
  prefix: string
  suffix: string
}

export type CommentItem = {
  comment_id: string
  article_id: string
  body: string
  author_name: string
  created_at_epoch: number
  anchor_quote?: string | null
  anchor_prefix?: string | null
  anchor_suffix?: string | null
}

export type SharedArticleResponse = {
  article: Record<string, unknown>
  is_draft: boolean
  link_label: string
}

/** Public, token-gated draft-share API — the token is a URL path segment, no auth headers. */
export const sharingApi = {
  async fetchSharedArticle(token: string): Promise<SharedArticleResponse> {
    return (await api.getJson(`/api/v1/shared/${encodeURIComponent(token)}`)) as SharedArticleResponse
  },

  async listSharedComments(token: string): Promise<{ items: CommentItem[] }> {
    return (await api.getJson(`/api/v1/shared/${encodeURIComponent(token)}/comments`)) as {
      items: CommentItem[]
    }
  },

  async postSharedComment(
    token: string,
    body: string,
    authorName: string,
    anchor: CommentQuoteAnchor | null,
  ): Promise<CommentItem> {
    return (await api.postJson(`/api/v1/shared/${encodeURIComponent(token)}/comments`, {
      body,
      author_name: authorName,
      anchor,
    })) as CommentItem
  },
}
