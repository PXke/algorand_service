// Small formatting helpers shared by the storefront's route components
// (routes/marketplace/*.svelte, components/x402/*.svelte) -- one place, not
// a per-page copy of the same one-liners (CLAUDE.md §3).
import { formatDispatchStamp } from '../liveClock'

export function x402Stamp(epoch: number | undefined | null, locale: string): string {
  if (!epoch || !Number.isFinite(epoch)) return ''
  return formatDispatchStamp(epoch, locale)
}

export function x402ShortAddr(a: string): string {
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
}

/** Pretty-printed JSON for a `<pre>` -- text, never markup, so it needs no sanitizer. */
export function x402Json(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
