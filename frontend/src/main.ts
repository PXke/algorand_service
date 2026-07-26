import { mount } from 'svelte'
import './app.css'
import './lib/theme'
import App from './App.svelte'
import { restoreSession } from './lib/auth/session'
import { startPageviewTracking } from './lib/router'

/**
 * Atomic SSR → SPA handoff: drop #ssr-body and reveal #app in one turn so
 * layout shifts from mounting stay invisible (and don't score as CLS).
 */
function revealSpa(): void {
  const ssr = document.getElementById('ssr-body')
  if (ssr) {
    ssr.setAttribute('aria-hidden', 'true')
    ssr.remove()
  }
  document.getElementById('pxke-ssr-feed')?.remove()
  document.documentElement.classList.remove('spa-booting')
  // Backend also listens; removal is already done, title restore still runs.
  window.dispatchEvent(new Event('pxke-spa-ready'))
}

function signalSpaReady(): void {
  // Two rAFs: mount + style flush while #app is still hidden under spa-booting.
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
  if (document.documentElement.classList.contains('spa-booting')) {
    signalSpaReady()
  } else {
    window.dispatchEvent(new Event('pxke-spa-ready'))
  }
  registerSwWhenIdle()
})
