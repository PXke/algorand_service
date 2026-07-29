/** Client-side SEO helpers (titles, absolute URLs, JSON-LD). */

export const SITE_NAME = 'PXke Algorand'
export const SITE_TAGLINE = 'Independent coverage of the Algorand ecosystem'

/** Bing warns past ~65; Google truncates around the same point. */
const TITLE_MAX_CHARS = 65

/**
 * Brand-suffix a page title when it still fits the ~65-char SERP budget.
 * Long headlines stand alone; over-budget headlines are word-boundary clamped.
 * Only `<title>` should use this — og:/twitter:/JSON-LD keep the full headline.
 */
export function formatPageTitle(headline: string, siteName = SITE_NAME): string {
  const title = (headline || '').trim() || siteName
  if (title === siteName || title.endsWith(siteName)) return title

  const suffixed = `${title} · ${siteName}`
  if (suffixed.length <= TITLE_MAX_CHARS) return suffixed
  if (title.length <= TITLE_MAX_CHARS) return title

  let cut = title.slice(0, TITLE_MAX_CHARS - 1)
  const space = cut.lastIndexOf(' ')
  if (space > TITLE_MAX_CHARS / 2) cut = cut.slice(0, space)
  return `${cut.replace(/[\s,;:—-]+$/u, '')}…`
}

/** Absolute URL on the public site origin (not the API host). */
export function absoluteUrl(pathOrUrl: string): string {
  if (pathOrUrl.startsWith('http://') || pathOrUrl.startsWith('https://')) {
    return pathOrUrl
  }
  const origin =
    typeof window !== 'undefined' ? window.location.origin : 'https://algorand.pxke.me'
  return `${origin}${pathOrUrl.startsWith('/') ? pathOrUrl : `/${pathOrUrl}`}`
}

/** Branded 1200×630 share card served by nginx → backend. */
export function articleOgImageUrl(articleId: string): string {
  return absoluteUrl(`/og/article/${articleId}.png`)
}

/** Prefers the permanent slug; falls back to the id so pre-migration-056 rows
 *  (and anything whose slug failed to load) still resolve. The server 301s
 *  id -> slug, so an id href is correct but costs a redirect. */
export function articleCanonicalPath(
  articleId: string,
  lang?: string | null,
  slug?: string | null,
): string {
  const base = `/news/articles/${slug || articleId}`
  const code = (lang || '').trim()
  // Locale path segment, mirroring render.article_path on the server.
  if (code && code !== 'en') return `/${encodeURIComponent(code)}${base}`
  return base
}

/** Open Graph locale tags (underscore form). */
export function ogLocaleFor(lang: string): string {
  const map: Record<string, string> = {
    en: 'en_US',
    fa: 'fa_AF',
    ps: 'ps_AF',
    ar: 'ar_AR',
    ru: 'ru_RU',
    zh: 'zh_CN',
    hi: 'hi_IN',
    es: 'es_ES',
    fr: 'fr_FR',
  }
  return map[lang] ?? 'en_US'
}

export function truncateMeta(text: string, max = 160): string {
  const t = text.replace(/\s+/g, ' ').trim()
  if (t.length <= max) return t
  let cut = t.slice(0, max - 1)
  const space = cut.lastIndexOf(' ')
  if (space > max / 2) cut = cut.slice(0, space)
  return `${cut.replace(/[\s,;:—-]+$/u, '')}…`
}

export function newsArticleJsonLd(opts: {
  articleId: string
  title: string
  description: string
  publishedEpoch?: number
  tags?: string[]
  lang?: string | null
}): Record<string, unknown> {
  const canonical = absoluteUrl(articleCanonicalPath(opts.articleId, opts.lang))
  const image = articleOgImageUrl(opts.articleId)
  const published =
    opts.publishedEpoch && opts.publishedEpoch > 0
      ? new Date(opts.publishedEpoch * 1000).toISOString()
      : undefined
  const publisher = {
    '@type': 'Organization',
    name: SITE_NAME,
    logo: {
      '@type': 'ImageObject',
      url: absoluteUrl('/icons/icon-512.png'),
    },
  }
  return {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    headline: truncateMeta(opts.title, 110),
    description: opts.description,
    ...(published
      ? { datePublished: published, dateModified: published }
      : {}),
    url: canonical,
    mainEntityOfPage: { '@type': 'WebPage', '@id': canonical },
    image: [image],
    publisher,
    author: publisher,
    keywords: (opts.tags ?? []).join(', '),
    isAccessibleForFree: true,
  }
}

/** Escape `</` so a JSON-LD payload cannot break out of its script tag. */
export function safeJsonLd(data: unknown): string {
  return JSON.stringify(data).replace(/</g, '\\u003c')
}
