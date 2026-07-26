import { mount } from 'svelte'
import './app.css'
import './lib/theme'
import App from './App.svelte'
import { restoreSession } from './lib/auth/session'
import { startPageviewTracking } from './lib/router'

function signalSpaReady(): void {
  const ssr = document.getElementById('ssr-body')
  if (ssr) {
    ssr.setAttribute('aria-hidden', 'true')
    ssr.setAttribute('inert', '')
  }
  // Let the SPA commit a frame under the fixed SSR overlay before removal.
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      window.dispatchEvent(new Event('pxke-spa-ready'))
    })
  })
}

function registerSwWhenIdle(): void {
  const run = () => {
    void import('virtual:pwa-register')
      .then(({ registerSW }) => {
        registerSW({ immediate: true })
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

// Restore before mount so a slow/failed /auth/session cannot race a fresh
// login and wipe the new token from localStorage.
void restoreSession().finally(() => {
  startPageviewTracking()
  mount(App, {
    target: document.getElementById('app')!,
  })
  signalSpaReady()
  registerSwWhenIdle()
})
