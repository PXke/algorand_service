import { writable, get } from 'svelte/store'

export type WcDebugEvent = {
  t: number
  msg: string
}

const MAX = 80
const startedAt = Date.now()

export const wcDebugEnabled = writable(false)
export const wcDebugLog = writable<WcDebugEvent[]>([])

export function isWcDebugQuery(): boolean {
  if (typeof window === 'undefined') return false
  try {
    return new URLSearchParams(window.location.search).get('wcdebug') === '1'
  } catch {
    return false
  }
}

export function enableWcDebugFromQuery(): void {
  if (isWcDebugQuery()) wcDebugEnabled.set(true)
}

export function toggleWcDebug(): void {
  wcDebugEnabled.update((v) => !v)
}

export function wcDebug(msg: string): void {
  if (!get(wcDebugEnabled) && !isWcDebugQuery()) return
  if (isWcDebugQuery()) wcDebugEnabled.set(true)
  const entry = { t: Date.now() - startedAt, msg }
  wcDebugLog.update((rows) => {
    const next = [...rows, entry]
    return next.length > MAX ? next.slice(next.length - MAX) : next
  })
}

export function summarizeWcUri(wcUri: string): string {
  try {
    const rawBridge = wcUri.match(/[?&]bridge=([^&]*)/)?.[1] ?? ''
    const bridgeAlreadyEnc = /%3A%2F%2F|%253A/.test(rawBridge)
    const q = wcUri.includes('?') ? wcUri.slice(wcUri.indexOf('?') + 1) : ''
    const params = new URLSearchParams(q)
    const key = params.get('key') ?? ''
    const bridge = params.get('bridge') ?? ''
    const topic = wcUri.split('@')[0]?.replace(/^wc:/, '') ?? ''
    return `topic=${topic.slice(0, 8)}… keyLen=${key.length} bridgeRawEnc=${bridgeAlreadyEnc} bridge=${bridge.slice(0, 48)}`
  } catch {
    return `rawLen=${wcUri.length}`
  }
}
