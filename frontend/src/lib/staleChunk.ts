/**
 * Recovery for a lazily-`import()`ed chunk that 404s because a newer deploy
 * deleted the content-hashed file it points at — the classic "tab was left
 * open across a release" failure: the page's already-parsed module graph
 * still references the OLD hashed filenames, the server only serves the
 * LATEST build's files, and (with the PWA's `registerType: 'autoUpdate'`)
 * a background service-worker update may have already swapped the active
 * worker/precache under the open tab before the failure even happened.
 *
 * `recoverFromStaleChunk` unregisters any service worker and clears Cache
 * Storage (a lingering SW/cache can itself keep serving the stale
 * entrypoint after a plain reload), then reloads once. Guarded by
 * sessionStorage so a *genuine* load failure (bad network, a real broken
 * build) doesn't reload-loop forever — call sites get `false` back when a
 * recovery reload already ran this episode, and must fall back to their
 * own failure UI (stop a spinner, close a dialog) instead of assuming the
 * page is about to navigate away.
 *
 * Every lazy `import()` of a route/tab/dialog component in this app should
 * route its `.catch()` through this (not just the top-level route loader)
 * — an admin tab or the wallet-connect dialog hitting the same stale-chunk
 * 404 deserves the same self-heal, not a silently-stuck loading state.
 */

const GUARD_KEY = 'pxke-chunk-reload'

/** Call after a lazy import succeeds, so a *later* staleness episode this
 * session (a subsequent deploy) can still trigger recovery. */
export function clearStaleChunkGuard(): void {
  try {
    sessionStorage.removeItem(GUARD_KEY)
  } catch {
    /* ignore */
  }
}

/** Returns true if a recovery reload was triggered, false if one already
 * ran this episode (caller must apply its own fallback failure UI). */
export async function recoverFromStaleChunk(): Promise<boolean> {
  try {
    if (sessionStorage.getItem(GUARD_KEY)) return false
    sessionStorage.setItem(GUARD_KEY, '1')
  } catch {
    /* sessionStorage unavailable — proceed as if this is the first attempt */
  }
  try {
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations()
      await Promise.all(regs.map((r) => r.unregister()))
    }
    if ('caches' in window) {
      const keys = await caches.keys()
      await Promise.all(keys.map((k) => caches.delete(k)))
    }
  } catch {
    /* ignore */
  }
  window.location.reload()
  return true
}
