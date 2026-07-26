import { writable, derived } from 'svelte/store'
import { config } from './config'

export type RouteMatch = {
  path: string
  params: Record<string, string>
  query: URLSearchParams
}

function parse(): RouteMatch {
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  const query = new URLSearchParams(window.location.search)
  return { path, params: {}, query }
}

export const route = writable<RouteMatch>(parse())

export function navigate(to: string, replace = false): void {
  if (replace) history.replaceState({}, '', to)
  else history.pushState({}, '', to)
  route.set(parse())
  window.scrollTo(0, 0)
  void trackPageview()
}

window.addEventListener('popstate', () => {
  route.set(parse())
  void trackPageview()
})

/** Match `/news/articles/:articleId` style patterns. */
export function matchPath(
  pattern: string,
  path: string,
): Record<string, string> | null {
  const pp = pattern.split('/').filter(Boolean)
  const sp = path.split('/').filter(Boolean)
  if (pp.length !== sp.length) return null
  const params: Record<string, string> = {}
  for (let i = 0; i < pp.length; i++) {
    if (pp[i].startsWith(':')) params[pp[i].slice(1)] = decodeURIComponent(sp[i])
    else if (pp[i] !== sp[i]) return null
  }
  return params
}

export const pathOnly = derived(route, ($r) => $r.path)

let lastTracked = ''

async function trackPageview(): Promise<void> {
  const path = window.location.pathname
  if (path === lastTracked) return
  lastTracked = path
  try {
    if (document.cookie.includes('pxke_no_track=')) return
    // Initial document GET already recorded server-side — skip duplicate beacon.
    try {
      const ssr = sessionStorage.getItem('pxke_ssr_pv')
      if (ssr === path) {
        sessionStorage.removeItem('pxke_ssr_pv')
        return
      }
    } catch {
      /* ignore */
    }
    await fetch(`${config.apiBaseUrl}/api/v1/analytics/pageview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
      keepalive: true,
    })
  } catch {
    /* ignore */
  }
}

export function startPageviewTracking(): void {
  void trackPageview()
}
