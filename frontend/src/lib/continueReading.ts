/** Persist last-read article + per-article scroll for "Continue reading". */

export type ContinueReading = {
  articleId: string
  title: string
  path: string
  at: number
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
