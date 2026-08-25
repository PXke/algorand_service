import type { Attachment } from 'svelte/attachments'
import { localeTag, type LocaleCode } from './i18n'

/** Tick local minutes — not Svelte state, not seconds. One masthead clock is enough.
 *  Reads the viewer's own timezone from the runtime (Intl.DateTimeFormat's default
 *  resolves to it), so this always reads as "my clock," not a fixed UTC readout. */
export function liveClock(lang: string): Attachment {
  return (node) => {
    const tick = () => {
      const time = new Date().toLocaleTimeString(localeTag(lang as LocaleCode), {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZoneName: 'short',
      })
      node.textContent = time
    }
    tick()
    const id = setInterval(tick, 15_000)
    return () => clearInterval(id)
  }
}

/** Masthead edition date. Compact omits weekday/year so it fits under the nameplate. */
export function formatDateline(lang: string, compact = false): string {
  return new Date().toLocaleDateString(
    localeTag(lang as LocaleCode),
    compact
      ? { month: 'short', day: 'numeric' }
      : { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' },
  )
}

/** Filed-at stamp for a dispatch folio — date and clock in the viewer's own
 *  timezone (not relative, and not a fixed UTC readout: the reader wants to
 *  know when it published against their own clock, which Intl's default
 *  timezone resolution gives us for free). The stored epoch is always UTC;
 *  only this human-facing rendering converts it. Absolute machine-readable
 *  timestamps (JSON-LD, meta tags) stay in UTC/ISO8601 — see seo.ts. */
export function formatDispatchStamp(epoch: number, lang: string): string {
  const d = new Date(epoch * 1000)
  const tag = localeTag(lang as LocaleCode)
  const date = d.toLocaleDateString(tag, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
  const time = d.toLocaleTimeString(tag, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZoneName: 'short',
  })
  return `${date} · ${time}`
}
