import { describe, expect, it } from 'vitest'
import { LatestOnly } from './asyncGuard'

describe('LatestOnly', () => {
  it('flags a request stale once a newer request has been started', () => {
    const guard = new LatestOnly()
    const first = guard.next()
    const second = guard.next()

    expect(first.stale()).toBe(true)
    expect(second.stale()).toBe(false)
  })

  it('aborts the previous attempt\'s signal when a newer one starts', () => {
    const guard = new LatestOnly()
    const first = guard.next()
    expect(first.signal.aborted).toBe(false)

    guard.next()
    expect(first.signal.aborted).toBe(true)
  })

  it('regression: an out-of-order resolution does not win over a newer request', async () => {
    // Simulates the exact bug shape: request A (e.g. typing "algo") is fired,
    // then request B (e.g. typing "algorand") is fired before A resolves.
    // The network resolves them out of order — A's response lands *after*
    // B's. Without this guard, whichever `await` finishes last wins and the
    // stale result for "algo" clobbers the fresh result for "algorand".
    const guard = new LatestOnly()
    let applied: string | null = null

    async function run(query: string, resolveAfterMs: number) {
      const { stale } = guard.next()
      const result = await new Promise<string>((resolve) =>
        setTimeout(() => resolve(query), resolveAfterMs),
      )
      if (stale()) return
      applied = result
    }

    const a = run('algo', 20) // fired first, resolves LAST
    const b = run('algorand', 1) // fired second, resolves FIRST

    await Promise.all([a, b])

    expect(applied).toBe('algorand')
  })

  it('cancel() aborts the in-flight attempt without starting a new one', () => {
    const guard = new LatestOnly()
    const { signal } = guard.next()
    expect(signal.aborted).toBe(false)
    guard.cancel()
    expect(signal.aborted).toBe(true)
  })
})
