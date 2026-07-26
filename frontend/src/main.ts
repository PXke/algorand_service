import './lib/polyfills'
import { mount } from 'svelte'
import './app.css'
import './lib/theme'
import App from './App.svelte'
import { restoreSession } from './lib/auth/session'
import { startPageviewTracking } from './lib/router'

// Restore before mount so a slow/failed /auth/session cannot race a fresh
// login and wipe the new token from localStorage.
void restoreSession().finally(() => {
  startPageviewTracking()
  mount(App, {
    target: document.getElementById('app')!,
  })
  // Lets the SSR shell drop #ssr-body once the SPA owns the page.
  window.dispatchEvent(new Event('pxke-spa-ready'))
})
