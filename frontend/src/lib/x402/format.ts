// Small formatting helpers shared by the marketplace redesign's route
// components (routes/marketplace/*.svelte) -- extracted so five-plus new
// pages don't each hand-roll the same three one-liners the old X402.svelte/
// X402Endpoints.svelte each defined locally (CLAUDE.md §3).
import { formatDispatchStamp } from '../liveClock'

export function x402Stamp(epoch: number | undefined | null, locale: string): string {
  if (!epoch || !Number.isFinite(epoch)) return ''
  return formatDispatchStamp(epoch, locale)
}

export function x402HostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

export function x402ShortAddr(a: string): string {
  return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
}
