import { describe, expect, it } from 'vitest'
import { articleCanonicalPath, sharedArticleRedirectPath } from './seo'

describe('articleCanonicalPath', () => {
  it('prefers the slug over the raw id', () => {
    expect(articleCanonicalPath('abc-123', null, 'my-article-slug')).toBe(
      '/news/articles/my-article-slug',
    )
  })

  it('falls back to the id when there is no slug', () => {
    expect(articleCanonicalPath('abc-123', null, null)).toBe('/news/articles/abc-123')
  })

  it('prefixes a non-English locale segment', () => {
    expect(articleCanonicalPath('abc-123', 'fr', 'x')).toBe('/fr/news/articles/x')
  })

  it('treats "en" as no locale prefix', () => {
    expect(articleCanonicalPath('abc-123', 'en', 'x')).toBe('/news/articles/x')
  })
})

describe('sharedArticleRedirectPath', () => {
  const article = { article_id: 'ab6819e4-320b-4ebe-b6b4-648ea841f18e', slug: 'ceo-appointment' }

  it('returns null while the shared article is still a draft', () => {
    expect(sharedArticleRedirectPath(true, article)).toBeNull()
  })

  it('returns the canonical URL once the article has gone live', () => {
    expect(sharedArticleRedirectPath(false, article)).toBe('/news/articles/ceo-appointment')
  })

  it('falls back to the article id when a now-live article still has no slug', () => {
    expect(sharedArticleRedirectPath(false, { article_id: 'abc-123', slug: null })).toBe(
      '/news/articles/abc-123',
    )
  })
})
