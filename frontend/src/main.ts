import { mount } from 'svelte'
import './app.css'
import './lib/theme'
import './lib/i18n'
import App from './App.svelte'
import { initBugsnag } from './lib/bugsnag'
import { restoreSession } from './lib/auth/session'
import { startPageviewTracking } from './lib/router'
import { applyLangFromUrl, startLocaleUrlSync } from './lib/localeUrl'

// First thing on the page: catch as much of the bootstrap sequence as
// possible under passive error monitoring.
initBugsnag()

applyLangFromUrl()
startLocaleUrlSync()

/**
 * Reveal SPA: remove fixed SSR overlay (no layout impact) and make #app
 * visible. #app already owns in-flow space at min-height 100vh while hidden.
 */
function revealSpa(): void {
  const ssr = document.getElementById('ssr-body')
  if (ssr) {
    ssr.setAttribute('aria-hidden', 'true')
    ssr.remove()
  }
  // Home may already have consumed #pxke-ssr-feed; remove any leftover.
  document.getElementById('pxke-ssr-feed')?.remove()
  document.documentElement.classList.remove('spa-booting')
  document.getElementById('app')?.classList.add('spa-revealed')
  window.dispatchEvent(new Event('pxke-spa-ready'))
}

function signalSpaReady(): void {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      revealSpa()
    })
  })
}

function registerSwWhenIdle(): void {
  const run = () => {
    void import('virtual:pwa-register')
      .then(({ registerSW }) => {
        registerSW({
          immediate: true,
          onRegisteredSW(_url, registration) {
            // Pick up new hashed chunks soon after a deploy lands.
            if (!registration) return
            window.setInterval(() => {
              void registration.update()
            }, 60_000)
          },
        })
      })
      .catch(() => {
        /* PWA plugin absent in some local setups */
      })
  }
  const ric = (
    window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
    }
  ).requestIdleCallback
  if (typeof ric === 'function') {
    ric(run, { timeout: 4000 })
  } else {
    window.setTimeout(run, 2500)
  }
}

/**
 * The backend server-renders a full head block so crawlers get real meta
 * tags before any JS runs -- but PageMeta.svelte's svelte:head
 * unconditionally injects its own copy of several of the same tags on
 * mount, since it has no way to know the backend already rendered them
 * (this isn't Svelte's own SSR, so its usual head-diffing doesn't apply).
 * Root-caused via a Bing audit 2026-08-10: every article page carried two
 * description metas and two canonical links, flagged as an on-page SEO
 * error. Removing the server-rendered set right before mount is safe
 * specifically because nothing Svelte-managed exists in the DOM yet --
 * every match here is guaranteed to be the backend's copy, about to be
 * replaced by PageMeta's.
 *
 * Deliberately NOT included: og:image*, twitter:image*, and the ld+json
 * script. PageMeta only renders those when a route passes an `image` or
 * `jsonLd` prop -- most routes (Home, Topic, Glossary, ...) pass neither,
 * relying entirely on the backend's copy. Stripping those unconditionally
 * would silently delete real content on every route that doesn't supply
 * its own replacement, not fix a duplicate.
 */
function stripServerRenderedHeadTags(): void {
  const selectors = [
    'title',
    'meta[name="description"]',
    'link[rel="canonical"]',
    'meta[name="robots"]',
    'meta[property="og:title"]',
    'meta[property="og:description"]',
    'meta[property="og:url"]',
    'meta[property="og:type"]',
    'meta[property="og:site_name"]',
    'meta[property="og:locale"]',
    'meta[name="twitter:card"]',
    'meta[name="twitter:title"]',
    'meta[name="twitter:description"]',
  ]
  for (const selector of selectors) {
    document.head.querySelectorAll(selector).forEach((el) => el.remove())
  }
}

// Don't block first paint on /auth/session — wallet state can hydrate after.
void restoreSession()
startPageviewTracking()
stripServerRenderedHeadTags()
mount(App, {
  target: document.getElementById('app')!,
})
if (document.documentElement.classList.contains('spa-booting')) {
  signalSpaReady()
} else {
  document.getElementById('app')?.classList.add('spa-revealed')
  window.dispatchEvent(new Event('pxke-spa-ready'))
}
registerSwWhenIdle()
