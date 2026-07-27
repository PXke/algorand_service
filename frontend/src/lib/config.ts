function env(key: string, fallback: string): string {
  const v = import.meta.env[key]
  return typeof v === 'string' && v.length > 0 ? v : fallback
}

function parseAddressList(raw: string): string[] {
  if (!raw.trim()) return []
  return raw
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

export const config = {
  apiBaseUrl: env('VITE_API_BASE_URL', ''),
  authDomain: env('VITE_AUTH_DOMAIN', 'localhost'),
  algodApiUrl: env('VITE_ALGOD_API_URL', 'https://testnet-api.algonode.cloud'),
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
