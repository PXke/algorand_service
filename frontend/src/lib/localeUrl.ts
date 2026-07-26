/** Boot + keep `?lang=` in sync with the active content language. */

import { get } from 'svelte/store'
import { activeLocale, localePreference } from './i18n'
import { langFromLocation } from './paths'
import { syncLangQuery } from './router'

/** Prefer URL `?lang=` over stored preference on first paint. */
export function applyLangFromUrl(): void {
  const fromUrl = langFromLocation()
  if (fromUrl) localePreference.set(fromUrl)
}

/** Mirror active locale into the address bar; honor back/forward `?lang=`. */
export function startLocaleUrlSync(): void {
  let skipFirst = true
  activeLocale.subscribe((lang) => {
    if (skipFirst) {
      skipFirst = false
      // Still align the bar once after boot (e.g. preference is es, URL bare).
      syncLangQuery(lang)
      return
    }
    syncLangQuery(lang)
  })

  window.addEventListener('popstate', () => {
    const fromUrl = langFromLocation()
    if (fromUrl) {
      if (get(localePreference) !== fromUrl) localePreference.set(fromUrl)
      return
    }
    // Bare URL → English content preference for this history entry.
    if (get(activeLocale) !== 'en') localePreference.set('en')
  })
}
