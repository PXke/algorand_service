import type { Attachment } from 'svelte/attachments'
import { localeTag, type LocaleCode } from './i18n'

/** Tick UTC minutes — not Svelte state, not seconds. One masthead clock is enough. */
export function liveClock(lang: string): Attachment {
  return (node) => {
    const tick = () => {
      const time = new Date().toLocaleTimeString(localeTag(lang as LocaleCode), {
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
        timeZone: 'UTC',
      })
      node.textContent = `${time} UTC`
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

/** Filed-at stamp for a dispatch folio — date and clock in UTC, not relative. */
export function formatDispatchStamp(epoch: number, lang: string): string {
  const d = new Date(epoch * 1000)
  const tag = localeTag(lang as LocaleCode)
  const date = d.toLocaleDateString(tag, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  })
  const time = d.toLocaleTimeString(tag, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'UTC',
  })
  return `${date} · ${time} UTC`
}
