/**
 * Shared out-of-order-response guard for async fetches triggered by user
 * interaction or a reactive dependency change (search-as-you-type, list-item
 * selection, tab/filter switches, pagination) — anywhere a *function call*
 * fires a request rather than an `$effect` that owns its own AbortController
 * for the length of one reactive run (that pattern — `const ac = new
 * AbortController(); ...; return () => ac.abort()` — is already established
 * per-component for effects and should keep being used there).
 *
 * Each call to `next()` aborts whatever attempt from a *previous* `next()`
 * call is still in flight and returns that attempt's AbortSignal plus a
 * `stale()` check. Pass the signal to the fetch so the network request
 * itself is cancelled, and call `stale()` after every `await` before writing
 * the result to component state — if it returns true, a newer `next()` call
 * has already superseded this one and the response must be discarded rather
 * than applied.
 */
export class LatestOnly {
  #controller: AbortController | null = null

  /** Starts a new attempt, aborting any attempt still in flight. */
  next(): { signal: AbortSignal; stale: () => boolean } {
    this.#controller?.abort()
    const controller = new AbortController()
    this.#controller = controller
    return { signal: controller.signal, stale: () => controller.signal.aborted }
  }

  /** Aborts any in-flight attempt without starting a new one (teardown). */
  cancel(): void {
    this.#controller?.abort()
  }
}
