/** Path helpers that keep the locale path segment in sync with the active content language. */

import { get } from 'svelte/store'
import { activeLocale, type LocaleCode } from './i18n'
import { articleCanonicalPath } from './seo'

const LANGS = new Set(['en', 'es', 'ar', 'fa', 'fr', 'hi', 'ps', 'ru', 'zh'])

/**
 * Only article documents have translations, so only they carry a locale
 * prefix. Prefixing the front page or /topics would mint URLs the server
 * 404s, and the locale preference survives navigation via localStorage
 * (see i18n.localePreference) rather than by riding in every URL.
 */
const ARTICLE_PATH = /^\/news\/articles\//

export function isLocaleCode(v: string | null | undefined): v is LocaleCode {
  return !!v && LANGS.has(v)
}

/** Split a leading locale segment off a path: `/fr/news/articles/x` -> `fr`, `/news/articles/x`. */
export function splitLocalePath(path: string): {
  lang: LocaleCode | null
  rest: string
} {
  const m = /^\/([^/?#]+)(\/.*)?$/.exec(path)
  const head = m?.[1]
  if (head && isLocaleCode(head) && head !== 'en') {
    return { lang: head, rest: m?.[2] || '/' }
  }
  return { lang: null, rest: path }
}

/** Apply (or strip) the locale path segment on an absolute path, preserving query + hash. */
export function withLang(path: string, lang: string | null | undefined = get(activeLocale)): string {
  const hashIdx = path.indexOf('#')
  const hash = hashIdx >= 0 ? path.slice(hashIdx) : ''
  const bare = hashIdx >= 0 ? path.slice(0, hashIdx) : path
  const qIdx = bare.indexOf('?')
  const base = qIdx >= 0 ? bare.slice(0, qIdx) : bare
  // Drop any legacy `?lang=` rather than carry both forms of the same signal.
  const params = new URLSearchParams(qIdx >= 0 ? bare.slice(qIdx + 1) : '')
  params.delete('lang')
  const q = params.toString()

  const { rest } = splitLocalePath(base)
  const code = (lang || '').trim()
  const next = code && code !== 'en' && ARTICLE_PATH.test(rest) ? `/${code}${rest}` : rest
  return `${next}${q ? `?${q}` : ''}${hash}`
}

export function articleHref(
  articleId: string,
  lang?: string | null,
  slug?: string | null,
): string {
  return articleCanonicalPath(articleId, lang ?? get(activeLocale), slug)
}

/** Read the active locale from the current URL's path segment (legacy `?lang=` still honoured). */
export function langFromLocation(
  pathname = window.location.pathname,
  search = window.location.search,
): LocaleCode | null {
  const { lang } = splitLocalePath(pathname)
  if (lang) return lang
  // Old indexed/bookmarked form. The server 301s these, so this only fires for
  // a client-side URL the redirect never saw.
  const raw = new URLSearchParams(search).get('lang')
  if (!raw) return null
  const code = raw.trim().toLowerCase()
  return isLocaleCode(code) ? code : null
}
