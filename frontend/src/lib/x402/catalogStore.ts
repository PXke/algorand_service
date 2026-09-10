import { writable } from 'svelte/store'
import { x402Api, type X402Catalog } from '../api/x402'

export type X402CatalogState = {
  catalog: X402Catalog | null
  loading: boolean
  failed: boolean
}

const state = writable<X402CatalogState>({ catalog: null, loading: true, failed: false })

export const x402CatalogState = { subscribe: state.subscribe }

let started = false

/**
 * Fetch the catalog v2 document exactly once per page session and share it.
 *
 * Every routes/marketplace/*.svelte page reads this one singleton instead
 * of firing its own `x402Api.catalog()` call on mount (CLAUDE.md §3: no new
 * copies of existing logic).
 *
 * Idempotent and safe to call from multiple components -- only the first
 * call actually fetches.
 */
export function ensureX402Catalog(): void {
  if (started) return
  started = true
  void (async () => {
    try {
      const doc = await x402Api.catalog()
      state.set({ catalog: doc, loading: false, failed: false })
    } catch {
      state.set({ catalog: null, loading: false, failed: true })
    }
  })()
}
