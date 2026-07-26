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

export function proxiedImageUrl(url: string): string {
  if (!url || !url.startsWith('http')) return url
  // Same-origin proxy (site nginx + Vite dev both expose /api → backend).
  // Prefer relative URLs so LCP images share the document's connection.
  const base = config.apiBaseUrl
  if (base && url.startsWith(base)) return url
  if (url.includes('/api/v1/img?')) return url
  return `/api/v1/img?url=${encodeURIComponent(url)}`
}

export function faviconUrl(sourceUrl?: string | null): string | null {
  if (!sourceUrl) return null
  try {
    const host = new URL(sourceUrl).host
    if (!host) return null
    return proxiedImageUrl(`https://icons.duckduckgo.com/ip3/${host}.ico`)
  } catch {
    return null
  }
}

export function articleLogoUrl(opts: {
  sourceUrl?: string | null
  serviceId?: string | null
}): string | null {
  if (opts.sourceUrl) {
    try {
      const host = new URL(opts.sourceUrl).host.replace(/^www\./, '')
      if (host) return proxiedImageUrl(`https://icons.duckduckgo.com/ip3/${host}.ico`)
    } catch {
      /* fall through */
    }
  }
  const s = opts.serviceId?.trim().toLowerCase()
  if (!s || !s.includes('-') || !/^[a-z0-9.-]+$/.test(s)) return null
  return proxiedImageUrl(`https://icons.duckduckgo.com/ip3/${s.replaceAll('-', '.')}.ico`)
}
