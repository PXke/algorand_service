import { config } from './config'

export function looksLikeFaviconUrl(url: string): boolean {
  const path = (() => {
    try {
      return new URL(url).pathname.toLowerCase()
    } catch {
      return url.toLowerCase()
    }
  })()
  if (path.endsWith('.ico')) return true
  return /favicon|apple-touch-icon/.test(path)
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

/** Host + path, unwrapping `/api/v1/img?url=` so a hero and a markdown
 * embed of the same file match even when one is proxied. */
export function imageIdentity(url: string): string {
  let current = url.trim()
  for (let i = 0; i < 3; i += 1) {
    try {
      const parsed = new URL(current, 'https://algorand.pxke.me')
      if (parsed.pathname.includes('/api/v1/img')) {
        const inner = parsed.searchParams.get('url')
        if (inner) {
          current = inner
          continue
        }
      }
      return `${parsed.host}${parsed.pathname}`.toLowerCase()
    } catch {
      return current.toLowerCase()
    }
  }
  return current.toLowerCase()
}

export function sameImageUrl(a: string, b: string): boolean {
  const left = a.trim()
  const right = b.trim()
  if (!left || !right) return false
  return left === right || imageIdentity(left) === imageIdentity(right)
}

