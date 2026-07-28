import { writable, derived, get } from 'svelte/store'
import { designToken } from './designTokens'

export type ThemeMode = 'light' | 'dark' | 'system'

const KEY = 'theme_mode'

function initial(): ThemeMode {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'light' || v === 'dark' || v === 'system') return v
  } catch {
    /* ignore */
  }
  return 'system'
}

export const themeMode = writable<ThemeMode>(initial())

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
}

export const resolvedTheme = derived(themeMode, ($m) => {
  if ($m !== 'system') return $m
  return systemPrefersDark() ? 'dark' : 'light'
})

/** Bar toggle: flip resolved light ↔ dark (explicit mode, not system). */
export function toggleLightDark(): void {
  themeMode.set(get(resolvedTheme) === 'dark' ? 'light' : 'dark')
}

themeMode.subscribe((mode) => {
  try {
    localStorage.setItem(KEY, mode)
  } catch {
    /* ignore */
  }
})

resolvedTheme.subscribe((t) => {
  document.documentElement.dataset.theme = t
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute('content', designToken('theme-color', t))
  }
})

if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    themeMode.update((m) => m)
  })
}
