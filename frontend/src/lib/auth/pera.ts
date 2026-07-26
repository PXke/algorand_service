import type { PeraWalletConnect } from '@perawallet/connect'
import { config } from '../config'

let client: PeraWalletConnect | null = null
let loading: Promise<typeof import('@perawallet/connect')> | null = null

async function loadPeraModule() {
  if (!loading) loading = import('@perawallet/connect')
  return loading
}

export async function getPera(): Promise<PeraWalletConnect> {
  if (!client) {
    const { PeraWalletConnect } = await loadPeraModule()
    client = new PeraWalletConnect({
      bridge: 'https://wallet-connect-a.perawallet.app',
      chainId: config.walletConnectChainId as 416001 | 416002,
      shouldShowSignTxnToast: true,
    })
  }
  return client
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]!)
  return btoa(binary)
}

export async function peraConnect(): Promise<string> {
  const pera = await getPera()
  try {
    const accounts = await pera.connect()
    const addr = accounts[0]
    if (!addr) throw new Error('No account returned from wallet')
    return addr
  } catch (e) {
    const err = e as { data?: { type?: string } }
    if (err?.data?.type === 'CONNECT_MODAL_CLOSED') {
      throw new Error('Wallet connection cancelled')
    }
    throw e instanceof Error ? e : new Error(String(e))
  }
}

export async function peraSignSiwa(
  walletAddress: string,
  signingMessage: string,
): Promise<{ proofMethod: 'signed_bytes'; signatureB64: string }> {
  const pera = await getPera()
  const data = new TextEncoder().encode(signingMessage)
  const signed = await pera.signData(
    [{ data, message: 'Sign in to PXke Algorand' }],
    walletAddress,
  )
  const sig = signed[0]
  if (!sig?.length) throw new Error('Empty signature from wallet')
  return { proofMethod: 'signed_bytes', signatureB64: bytesToBase64(sig) }
}

export async function peraDisconnect(): Promise<void> {
  if (!client) return
  try {
    await client.disconnect()
  } catch {
    /* ignore */
  }
}
