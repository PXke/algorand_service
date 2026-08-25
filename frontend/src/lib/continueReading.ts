/** Persist last-read article + per-article scroll for "Continue reading".
 *
 * Everything here is client-only (localStorage/sessionStorage) — read
 * progress is never sent to or stored on the backend. */

export type ContinueReading = {
  articleId: string
  title: string
  path: string
  at: number
  /** Fraction (0-1) of the article read so far, if known. */
  progress?: number
}

const CONTINUE_KEY = 'pxke_continue'
const SCROLL_PREFIX = 'pxke_scroll:'

export function rememberContinue(entry: Omit<ContinueReading, 'at'>): void {
  try {
    const payload: ContinueReading = { ...entry, at: Date.now() }
    localStorage.setItem(CONTINUE_KEY, JSON.stringify(payload))
  } catch {
    /* ignore */
  }
}

/** Update the read-fraction on the stored entry, only if it's still the one
 * for this article (a stale debounced write racing a navigation away must
 * not resurrect a "continue reading" entry for the wrong story). */
export function updateContinueProgress(articleId: string, progress: number): void {
  try {
    const raw = localStorage.getItem(CONTINUE_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw) as ContinueReading
    if (parsed?.articleId !== articleId) return
    parsed.progress = Math.min(1, Math.max(0, progress))
    localStorage.setItem(CONTINUE_KEY, JSON.stringify(parsed))
  } catch {
    /* ignore */
  }
}

export function readContinue(): ContinueReading | null {
  try {
    const raw = localStorage.getItem(CONTINUE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ContinueReading
    if (!parsed?.articleId || !parsed?.path) return null
    // Drop after 14 days.
    if (Date.now() - (parsed.at || 0) > 14 * 86400_000) {
      localStorage.removeItem(CONTINUE_KEY)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

export function clearContinue(): void {
  try {
    localStorage.removeItem(CONTINUE_KEY)
  } catch {
    /* ignore */
  }
}

export function saveArticleScroll(articleId: string, y: number): void {
  try {
    if (y < 80) {
      sessionStorage.removeItem(SCROLL_PREFIX + articleId)
      return
    }
    sessionStorage.setItem(SCROLL_PREFIX + articleId, String(Math.round(y)))
  } catch {
    /* ignore */
  }
}

export function takeArticleScroll(articleId: string): number | null {
  try {
    const raw = sessionStorage.getItem(SCROLL_PREFIX + articleId)
    if (!raw) return null
    sessionStorage.removeItem(SCROLL_PREFIX + articleId)
    const y = Number(raw)
    return Number.isFinite(y) && y > 0 ? y : null
  } catch {
    return null
  }
}
