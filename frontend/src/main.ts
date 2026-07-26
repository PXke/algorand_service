import { mount } from 'svelte'
import './app.css'
import './lib/theme'
import './lib/i18n'
import App from './App.svelte'
import { restoreSession } from './lib/auth/session'
import { startPageviewTracking } from './lib/router'
import { applyLangFromUrl, startLocaleUrlSync } from './lib/localeUrl'

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

// Don't block first paint on /auth/session — wallet state can hydrate after.
void restoreSession()
startPageviewTracking()
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
