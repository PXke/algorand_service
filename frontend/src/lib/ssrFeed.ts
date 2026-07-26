/** Take the server-embedded homepage feed JSON (if present) before the SPA drops it. */
export function takeSsrFeed<T = unknown>(): T | null {
  if (typeof document === 'undefined') return null
  const el = document.getElementById('pxke-ssr-feed')
  if (!el?.textContent?.trim()) return null
  try {
    return JSON.parse(el.textContent) as T
  } catch {
    return null
  } finally {
    el.remove()
  }
}
