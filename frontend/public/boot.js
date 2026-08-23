// WalletConnect / crypto bundles expect Node's `global`.
globalThis.global ??= globalThis
try {
  const mode = localStorage.getItem('theme_mode')
  const dark =
    mode === 'dark' ||
    (mode !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
} catch {
  /* ignore */
}
if (document.getElementById('ssr-body')) {
  document.documentElement.classList.add('spa-booting')
}
