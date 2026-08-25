import type { PeraWalletConnect } from '@perawallet/connect'
import { config } from '../config'
import { buildAuthPaymentTxn } from './arc0025'
import {
  openWalletDeepLink,
  walletDeepLink,
  withAlgorandWalletConnectParam,
} from './walletconnect'

export type PeraProof =
  | { proofMethod: 'signed_bytes'; signatureB64: string }
  | { proofMethod: 'arc0025_txn'; signedTxnB64: string }

let client: PeraWalletConnect | null = null
let loading: Promise<typeof import('@perawallet/connect')> | null = null
let deepLinkPatchInstalled = false

const SIGN_IN_PROMPT = 'Sign in to PXke Algorand'

async function loadPeraModule() {
  if (!loading) loading = import('@perawallet/connect')
  return loading
}

function isGeckoMobile(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Firefox|FxiOS/i.test(navigator.userAgent) &&
    /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
}

/**
 * Pera's Android Launch button uses a bare `wc:` href. Firefox ignores those;
 * rewrite to `perawallet-wc://` (same scheme Pera uses for iOS / sign redirect).
 */
function installFirefoxDeepLinkPatch(): void {
  if (deepLinkPatchInstalled || typeof document === 'undefined') return
  if (!isGeckoMobile()) return
  deepLinkPatchInstalled = true

  document.addEventListener(
    'click',
    (event) => {
      const path = typeof event.composedPath === 'function' ? event.composedPath() : []
      for (const node of path) {
        if (!(node instanceof HTMLAnchorElement)) continue
        const href = node.getAttribute('href') || node.href || ''
        if (!href.startsWith('wc:')) continue
        event.preventDefault()
        event.stopPropagation()
        const uri = withAlgorandWalletConnectParam(href)
        openWalletDeepLink(walletDeepLink(uri))
        return
      }
    },
    true,
  )
}

export async function getPera(): Promise<PeraWalletConnect> {
  installFirefoxDeepLinkPatch()
  if (!client) {
    const { PeraWalletConnect } = await loadPeraModule()
    client = new PeraWalletConnect({
      bridge: config.walletConnectBridge,
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
  // Fresh pairing each sign-in — a stale stored session confuses Firefox return.
  try {
    await pera.disconnect()
  } catch {
    /* ignore */
  }
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

async function tryPeraSignData(
  walletAddress: string,
  signingMessage: string,
): Promise<string | null> {
  try {
    const pera = await getPera()
    const data = new TextEncoder().encode(signingMessage)
    const signed = await pera.signData(
      [{ data, message: SIGN_IN_PROMPT }],
      walletAddress,
    )
    const sig = signed[0]
    if (!sig?.length) return null
    return bytesToBase64(sig)
  } catch {
    return null
  }
}

async function peraSignArc0025Txn(
  walletAddress: string,
  signingMessage: string,
): Promise<string> {
  const pera = await getPera()
  const txn = await buildAuthPaymentTxn(walletAddress, signingMessage)
  const signed = await pera.signTransaction([
    [{ txn, signers: [walletAddress] }],
  ])
  const first = signed[0]
  if (!first?.length) throw new Error('Unable to sign auth transaction')
  return bytesToBase64(first)
}

export async function peraSignLoginProof(
  walletAddress: string,
  signingMessage: string,
): Promise<PeraProof> {
  const signature = await tryPeraSignData(walletAddress, signingMessage)
  if (signature) {
    return { proofMethod: 'signed_bytes', signatureB64: signature }
  }
  const signedTxnB64 = await peraSignArc0025Txn(walletAddress, signingMessage)
  return { proofMethod: 'arc0025_txn', signedTxnB64 }
}

type Transportish = {
  close: () => void
  open: () => void
  subscribe?: (topic: string) => void
}

type Connectorish = {
  clientId?: string
  peerId?: string
  handshakeTopic?: string
  _transport?: Transportish
  transportClose?: () => void
}

/**
 * Revive Pera's underlying WC bridge after returning from the wallet app.
 * Same zombie-socket problem as raw WalletConnect on Firefox Android.
 */
export function peraWakeTransport(): void {
  const wc = (client?.connector ?? null) as Connectorish | null
  if (!wc) return
  const transport = wc._transport
  try {
    if (transport) {
      try {
        transport.close()
      } catch {
        /* ignore */
      }
      try {
        transport.open()
      } catch {
        /* ignore */
      }
    } else if (typeof wc.transportClose === 'function') {
      try {
        wc.transportClose()
      } catch {
        /* ignore */
      }
    }
  } catch {
    /* ignore */
  }

  const resub = () => {
    if (!transport || typeof transport.subscribe !== 'function') return
    const topics = [wc.clientId, wc.handshakeTopic, wc.peerId].filter(
      (t): t is string => typeof t === 'string' && t.length > 0,
    )
    for (const topic of topics) {
      try {
        transport.subscribe(topic)
      } catch {
        /* ignore */
      }
    }
  }
  window.setTimeout(resub, 200)
  window.setTimeout(resub, 800)
}

export function peraWakeTransportBurst(): void {
  peraWakeTransport()
  window.setTimeout(() => peraWakeTransport(), 250)
  window.setTimeout(() => peraWakeTransport(), 1000)
  window.setTimeout(() => peraWakeTransport(), 2500)
}

export async function peraDisconnect(): Promise<void> {
  if (!client) return
  try {
    await client.disconnect()
  } catch {
    /* ignore */
  }
  client = null
}
