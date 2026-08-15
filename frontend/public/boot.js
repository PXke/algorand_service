// WalletConnect / crypto bundles expect Node's `global`.
globalThis.global ??= globalThis
if (document.getElementById('ssr-body')) {
  document.documentElement.classList.add('spa-booting')
}
