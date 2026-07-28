/** Path helpers that keep `?lang=` in sync with the active content language. */

import { get } from 'svelte/store'
import { activeLocale, type LocaleCode } from './i18n'
import { articleCanonicalPath } from './seo'

const LANGS = new Set(['en', 'es', 'ar', 'fa', 'fr', 'hi', 'ps', 'ru', 'zh'])

export function isLocaleCode(v: string | null | undefined): v is LocaleCode {
  return !!v && LANGS.has(v)
}

/** Append or strip `lang` on an absolute path (+ optional existing query). */
export function withLang(path: string, lang: string | null | undefined = get(activeLocale)): string {
  const hashIdx = path.indexOf('#')
  const hash = hashIdx >= 0 ? path.slice(hashIdx) : ''
  const bare = hashIdx >= 0 ? path.slice(0, hashIdx) : path
  const qIdx = bare.indexOf('?')
  const base = qIdx >= 0 ? bare.slice(0, qIdx) : bare
  const params = new URLSearchParams(qIdx >= 0 ? bare.slice(qIdx + 1) : '')
  const code = (lang || '').trim()
  if (code && code !== 'en') params.set('lang', code)
  else params.delete('lang')
  const q = params.toString()
  return `${base}${q ? `?${q}` : ''}${hash}`
}

export function articleHref(
  articleId: string,
  lang?: string | null,
  slug?: string | null,
): string {
  return articleCanonicalPath(articleId, lang ?? get(activeLocale), slug)
}

/** Read `?lang=` from the current URL (or a path string). */
export function langFromLocation(search = window.location.search): LocaleCode | null {
  const raw = new URLSearchParams(search).get('lang')
  if (!raw) return null
  const code = raw.trim().toLowerCase()
  return isLocaleCode(code) ? code : null
}
