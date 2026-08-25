import type { DeflyWalletConnect } from '@blockshake/defly-connect'
import { config } from '../config'
import { buildAuthPaymentTxn, bytesToBase64 } from './arc0025'

export type DeflyProof = { proofMethod: 'arc0025_txn'; signedTxnB64: string }

let client: DeflyWalletConnect | null = null
let loading: Promise<typeof import('@blockshake/defly-connect')> | null = null

async function loadDeflyModule() {
  if (!loading) loading = import('@blockshake/defly-connect')
  return loading
}

export async function getDefly(): Promise<DeflyWalletConnect> {
  if (!client) {
    const { DeflyWalletConnect } = await loadDeflyModule()
    client = new DeflyWalletConnect({
      bridge: config.walletConnectBridge,
      chainId: config.walletConnectChainId as 416001 | 416002,
      shouldShowSignTxnToast: true,
    })
  }
  return client
}

export async function deflyConnect(): Promise<string> {
  const defly = await getDefly()
  // Fresh pairing each sign-in — a stale stored session confuses return flows,
  // same reasoning as Pera's connect (see pera.ts).
  try {
    await defly.disconnect()
  } catch {
    /* ignore */
  }
  try {
    const accounts = await defly.connect()
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

/**
 * Defly's SDK does not expose an ARC-0060/`signData` method (confirmed
 * against @blockshake/defly-connect@1.2.1's type declarations) — only
 * `signTransaction`. So its login proof is always the ARC-0025 0-ALGO
 * self-payment fallback, same mechanism as Pera's fallback path.
 */
export async function deflySignLoginProof(
  walletAddress: string,
  signingMessage: string,
): Promise<DeflyProof> {
  const defly = await getDefly()
  const txn = await buildAuthPaymentTxn(walletAddress, signingMessage)
  const signed = await defly.signTransaction([[{ txn, signers: [walletAddress] }]])
  const first = signed[0]
  if (!first?.length) throw new Error('Unable to sign auth transaction')
  return { proofMethod: 'arc0025_txn', signedTxnB64: bytesToBase64(first) }
}

export async function deflyDisconnect(): Promise<void> {
  if (!client) return
  try {
    await client.disconnect()
  } catch {
    /* ignore */
  }
  client = null
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
 * Revive Defly's underlying WC v1 bridge after returning from the wallet
 * app — Defly is built on the same @walletconnect/client v1 as Pera, so
 * the same zombie-socket issue applies (see pera.ts's peraWakeTransport).
 */
export function deflyWakeTransport(): void {
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

export function deflyWakeTransportBurst(): void {
  deflyWakeTransport()
  window.setTimeout(() => deflyWakeTransport(), 250)
  window.setTimeout(() => deflyWakeTransport(), 1000)
  window.setTimeout(() => deflyWakeTransport(), 2500)
}

/** Just wake the installed Defly app while awaiting sign approval. */
export function deflyAppLaunchLink(): string {
  return 'defly-wc://'
}
