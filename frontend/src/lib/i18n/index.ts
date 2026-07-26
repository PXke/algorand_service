import { writable, derived } from 'svelte/store'
import en from './locales/en.json'

export type LocaleCode = 'en' | 'es' | 'ar' | 'fa' | 'fr' | 'hi' | 'ps' | 'ru' | 'zh'

const LOCALE_KEY = 'app_locale'

const catalogs: Record<string, Record<string, string>> = { en: en as Record<string, string> }

async function loadCatalog(code: LocaleCode): Promise<Record<string, string>> {
  if (catalogs[code]) return catalogs[code]
  try {
    const mod = await import(`./locales/${code}.json`)
    catalogs[code] = mod.default as Record<string, string>
    return catalogs[code]
  } catch {
    return catalogs.en
  }
}

function initialLocale(): LocaleCode | 'system' {
  try {
    const v = localStorage.getItem(LOCALE_KEY)
    if (v === 'system' || !v) return 'system'
    if (['en', 'es', 'ar', 'fa', 'fr', 'hi', 'ps', 'ru', 'zh'].includes(v)) {
      return v as LocaleCode
    }
  } catch {
    /* ignore */
  }
  return 'system'
}

function resolveSystem(): LocaleCode {
  const nav = (navigator.language || 'en').toLowerCase()
  const map: Array<[string, LocaleCode]> = [
    ['zh', 'zh'],
    ['es', 'es'],
    ['ar', 'ar'],
    ['fa', 'fa'],
    ['fr', 'fr'],
    ['hi', 'hi'],
    ['ps', 'ps'],
    ['ru', 'ru'],
  ]
  for (const [prefix, code] of map) {
    if (nav.startsWith(prefix)) return code
  }
  return 'en'
}

export const localePreference = writable<LocaleCode | 'system'>(initialLocale())
export const activeLocale = derived(localePreference, ($p) =>
  $p === 'system' ? resolveSystem() : $p,
)

export const messages = writable<Record<string, string>>(en as Record<string, string>)

activeLocale.subscribe((code) => {
  void loadCatalog(code).then((cat) => messages.set(cat))
  document.documentElement.lang = code
  document.documentElement.dir = ['ar', 'fa', 'ps'].includes(code) ? 'rtl' : 'ltr'
})

localePreference.subscribe((pref) => {
  try {
    localStorage.setItem(LOCALE_KEY, pref)
  } catch {
    /* ignore */
  }
})

export function t(
  msgs: Record<string, string>,
  key: string,
  vars?: Record<string, string | number>,
): string {
  let s = msgs[key] ?? (en as Record<string, string>)[key] ?? key
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.replaceAll(`{${k}}`, String(v))
    }
  }
  return s
}

/** Resolve ICU-lite plurals: `{count, plural, =1{1 read} other{{count} reads}}`. */
export function tPlural(
  msgs: Record<string, string>,
  key: string,
  count: number,
): string {
  const raw = msgs[key] ?? (en as Record<string, string>)[key] ?? key
  const m = raw.match(/^\{(\w+),\s*plural,\s*(.+)\}$/s)
  if (!m) return t(msgs, key, { count })
  const varName = m[1]
  const forms = extractPluralForms(m[2])
  const picked =
    forms[`=${count}`] ??
    (count === 0 ? forms.zero : undefined) ??
    (count === 1 ? forms.one : undefined) ??
    (count === 2 ? forms.two : undefined) ??
    forms.other ??
    raw
  return picked.replaceAll(`{${varName}}`, String(count)).replaceAll('{count}', String(count))
}

function extractPluralForms(body: string): Record<string, string> {
  const forms: Record<string, string> = {}
  let i = 0
  while (i < body.length) {
    while (i < body.length && /\s/.test(body[i])) i++
    const keyMatch = /^(=\d+|zero|one|two|few|many|other)/.exec(body.slice(i))
    if (!keyMatch) break
    const formKey = keyMatch[1]
    i += formKey.length
    if (body[i] !== '{') break
    i += 1
    let depth = 1
    const start = i
    while (i < body.length && depth > 0) {
      if (body[i] === '{') depth += 1
      else if (body[i] === '}') depth -= 1
      i += 1
    }
    forms[formKey] = body.slice(start, i - 1)
  }
  return forms
}

export const localeOptions: Array<{ value: LocaleCode | 'system'; labelKey: string }> = [
  { value: 'system', labelKey: 'localeSystem' },
  { value: 'en', labelKey: 'localeEnglish' },
  { value: 'es', labelKey: 'localeSpanish' },
  { value: 'fr', labelKey: 'localeFrench' },
  { value: 'ar', labelKey: 'localeArabic' },
  { value: 'zh', labelKey: 'localeChinese' },
  { value: 'hi', labelKey: 'localeHindi' },
  { value: 'ru', labelKey: 'localeRussian' },
  { value: 'fa', labelKey: 'localeDari' },
  { value: 'ps', labelKey: 'localePashto' },
]
