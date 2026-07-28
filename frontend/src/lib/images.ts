import { config } from './config'

export function looksLikeLogoUrl(url: string): boolean {
  const path = (() => {
    try {
      return new URL(url).pathname.toLowerCase()
    } catch {
      return url.toLowerCase()
    }
  })()
  if (path.endsWith('.svg') || path.endsWith('.ico')) return true
  return /favicon|apple-touch|\/icons?[/. _-]|[/. _-]icons?[._-]|logo|\/og(\/|$)|opengraph/.test(
    path,
  )
}

/** An article's editorial art, proxied — or null when it has none.
 *
 * The one place that answers "what is this story's picture". There is no
 * favicon fallback: a 32px site icon blown up to fill an 88px box identified
 * the source, which the kicker already does, and read as a broken image.
 */
export function articleImageUrl(article: { image_url?: string | null }): string | null {
  const url = article.image_url?.trim()
  return url ? proxiedImageUrl(url) : null
}

export function proxiedImageUrl(url: string): string {
  if (!url || !url.startsWith('http')) return url
  // Same-origin proxy (site nginx + Vite dev both expose /api → backend).
  // Prefer relative URLs so LCP images share the document's connection.
  const base = config.apiBaseUrl
  if (base && url.startsWith(base)) return url
  if (url.includes('/api/v1/img?')) return url
  return `/api/v1/img?url=${encodeURIComponent(url)}`
}

