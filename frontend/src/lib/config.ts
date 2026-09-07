function env(key: string, fallback: string): string {
  const v = import.meta.env[key]
  return typeof v === 'string' && v.length > 0 ? v : fallback
}

/** Browser algod base. Empty VITE_ALGOD_API_URL means "our API's local-node proxy". */
function algodApiUrl(): string {
  const explicit = env('VITE_ALGOD_API_URL', '')
  if (explicit) return explicit
  const api = env('VITE_API_BASE_URL', '').replace(/\/$/, '')
  return api ? `${api}/api/v1/algod` : '/api/v1/algod'
}

function parseAddressList(raw: string): string[] {
  if (!raw.trim()) return []
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export type Product = 'news' | 'marketplace' | 'registry'

/** Which Vite build this is — see vite.config.ts and docs/x402-marketplace-product-redesign.md §3.1 row 7. */
function product(): Product {
  const raw = env('VITE_PRODUCT', 'news')
  if (raw === 'marketplace') return 'marketplace'
  if (raw === 'registry') return 'registry'
  return 'news'
}

export const config = {
  apiBaseUrl: env('VITE_API_BASE_URL', ''),
  authDomain: env('VITE_AUTH_DOMAIN', 'localhost'),
  // 'news' (default, algorand.pxke.me), 'marketplace' (x402.pxke.me), or
  // 'registry' (algorand-registry.pxke.me) — drives App.svelte's route table
  // and AppShell's chrome. One codebase, three build outputs (npm run build /
  // build:marketplace / build:registry).
  product: product(),
  // Absolute origins of the sibling products, used by each build's product
  // switcher to link back across domains (real cross-origin navs, not
  // client-side routing — three separate SPAs, one company).
  newsSiteUrl: env('VITE_NEWS_SITE_URL', 'https://algorand.pxke.me'),
  marketplaceSiteUrl: env('VITE_MARKETPLACE_SITE_URL', 'https://x402.pxke.me'),
  registrySiteUrl: env('VITE_REGISTRY_SITE_URL', 'https://algorand-registry.pxke.me'),
  algodApiUrl: algodApiUrl(),
  // Pera keeps WalletConnect v1 alive on its bridges; bridge.walletconnect.org is gone.
  walletConnectBridge: env(
    'VITE_WALLET_CONNECT_BRIDGE',
    'https://wallet-connect-a.perawallet.app',
  ),
  walletConnectProjectId: env('VITE_WALLETCONNECT_PROJECT_ID', ''),
  // Default TestNet for local/dev; production deploy sets VITE_WALLET_CONNECT_CHAIN_ID=416001 (MainNet).
  walletConnectChainId: Number(env('VITE_WALLET_CONNECT_CHAIN_ID', '416002')) || 416002,
  explorerBaseUrl: env(
    'VITE_EXPLORER_BASE_URL',
    'https://testnet.explorer.perawallet.app',
  ),
  adminWalletAddresses: parseAddressList(env('VITE_ADMIN_WALLET_ADDRESSES', '')),
  suggestionsEnabled: env('VITE_SUGGESTIONS_ENABLED', 'false') === 'true',
  bugsnagApiKey: env('VITE_BUGSNAG_API_KEY', ''),
  appVersion: env('VITE_APP_VERSION', ''),
}

export function explorerTxUrl(txid: string): string {
  return `${config.explorerBaseUrl}/tx/${txid}`
}

/** True only for on-chain Algorand txids — not crawl hashes, digests, or recomposes. */
export function isAlgorandTxid(txid: string | null | undefined): boolean {
  const tx = (txid ?? '').trim()
  return (
    tx.length === 52 &&
    /^[A-Z0-9]+$/.test(tx) &&
    tx === tx.toUpperCase() &&
    !tx.startsWith('WEEKLY')
  )
}

export function isAdminWallet(address: string | null | undefined): boolean {
  if (!address) return false
  const allow = config.adminWalletAddresses
  if (allow.length === 0) return false
  return allow.some((a) => a.toLowerCase() === address.toLowerCase())
}
