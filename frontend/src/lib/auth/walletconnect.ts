import '../polyfills'
import algosdk from 'algosdk'
import { config } from '../config'

export type WalletProof =
  | { proofMethod: 'signed_bytes'; signatureB64: string }
  | { proofMethod: 'arc0025_txn'; signedTxnB64: string }

type WalletConnectInstance = {
  connected: boolean
  accounts: string[]
  uri: string
  createSession: (opts?: { chainId?: number }) => Promise<void>
  killSession: () => Promise<void>
  sendCustomRequest: (request: {
    method: string
    params: unknown[]
  }) => Promise<unknown>
  on: (
    event: string,
    callback: (error: Error | null, payload: { params?: unknown[] } | null) => void,
  ) => void
  off: (event: string) => void
  transportClose: () => void
  _transport?: { close: () => void; open: () => void }
}

let connector: WalletConnectInstance | null = null
let cancelRequested = false

const SIGN_IN_PROMPT = 'Sign in to PXke Algorand'
const STORAGE_ID = 'pxke-algorand-walletconnect'

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]!)
  return btoa(binary)
}

function utf8ToBase64(text: string): string {
  return bytesToBase64(new TextEncoder().encode(text))
}

/** ARC-0025: wallets filter WC URIs with `algorand=true`. */
export function withAlgorandWalletConnectParam(wcUri: string): string {
  try {
    const url = new URL(wcUri)
    if (url.searchParams.get('algorand') === 'true') return wcUri
    url.searchParams.set('algorand', 'true')
    return url.toString()
  } catch {
    if (wcUri.includes('algorand=true')) return wcUri
    return wcUri.includes('?') ? `${wcUri}&algorand=true` : `${wcUri}?algorand=true`
  }
}

/** Pera iOS needs `perawallet-wc://`; Android accepts bare `wc:`. */
export function walletDeepLink(wcUri: string): string {
  const isIOS =
    typeof navigator !== 'undefined' &&
    (/iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1))
  if (isIOS) {
    return `perawallet-wc://wc?uri=${encodeURIComponent(wcUri)}`
  }
  return wcUri
}

export function isMobileWalletClient(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
}

function resolveCtor(mod: unknown): new (opts: Record<string, unknown>) => WalletConnectInstance {
  const m = mod as { default?: unknown }
  const d = m?.default ?? mod
  if (typeof d === 'function') return d as new (opts: Record<string, unknown>) => WalletConnectInstance
  if (d && typeof (d as { default?: unknown }).default === 'function') {
    return (d as { default: new (opts: Record<string, unknown>) => WalletConnectInstance }).default
  }
  throw new Error('WalletConnect client failed to load')
}

async function getConnector(): Promise<WalletConnectInstance> {
  if (connector) return connector
  const mod = await import('@walletconnect/client')
  const WalletConnect = resolveCtor(mod)
  const origin = typeof window !== 'undefined' ? window.location.origin : 'https://algorand.pxke.me'
  connector = new WalletConnect({
    bridge: config.walletConnectBridge,
    storageId: STORAGE_ID,
    clientMeta: {
      name: 'PXke Algorand',
      description: 'Independent coverage of the Algorand ecosystem',
      url: origin,
      icons: [`${origin}/icons/icon-192.png`],
    },
  })
  return connector
}

function extractSignatureB64(result: unknown): string | null {
  if (typeof result === 'string' && result.length > 0) return result
  if (Array.isArray(result) && result.length > 0) {
    const first = result[0]
    if (typeof first === 'string' && first.length > 0) return first
    if (first instanceof Uint8Array) return bytesToBase64(first)
    if (Array.isArray(first)) return bytesToBase64(Uint8Array.from(first as number[]))
  }
  return null
}

function extractSignedTxnB64(result: unknown): string {
  if (result == null || !Array.isArray(result) || result.length === 0) {
    throw new Error('Unable to sign auth transaction')
  }
  const first = result[0]
  if (typeof first === 'string') return first
  if (first instanceof Uint8Array) return bytesToBase64(first)
  if (Array.isArray(first)) return bytesToBase64(Uint8Array.from(first as number[]))
  throw new Error('Unable to sign auth transaction')
}

async function trySignData(
  wc: WalletConnectInstance,
  walletAddress: string,
  signingMessage: string,
): Promise<string | null> {
  try {
    const result = await wc.sendCustomRequest({
      method: 'algo_signData',
      params: [
        {
          data: utf8ToBase64(signingMessage),
          message: SIGN_IN_PROMPT,
          signer: walletAddress,
          chainId: config.walletConnectChainId,
        },
      ],
    })
    return extractSignatureB64(result)
  } catch {
    return null
  }
}

async function signArc0025Txn(
  wc: WalletConnectInstance,
  walletAddress: string,
  signingMessage: string,
): Promise<string> {
  const algod = new algosdk.Algodv2('', config.algodApiUrl, '')
  const suggested = await algod.getTransactionParams().do()
  const txn = algosdk.makePaymentTxnWithSuggestedParamsFromObject({
    sender: walletAddress,
    receiver: walletAddress,
    amount: 0,
    note: new TextEncoder().encode(signingMessage),
    suggestedParams: {
      ...suggested,
      fee: 0,
      flatFee: true,
    },
  })
  const txBytes = algosdk.encodeUnsignedTransaction(txn)
  const walletTxn = {
    txn: bytesToBase64(txBytes),
    signers: [walletAddress],
    message: SIGN_IN_PROMPT,
  }
  const result = await wc.sendCustomRequest({
    method: 'algo_signTxn',
    params: [[walletTxn], { message: SIGN_IN_PROMPT }],
  })
  return extractSignedTxnB64(result)
}

/**
 * Pair via WalletConnect v1. Fires `onDisplayUri` with an Algorand-patched URI
 * for in-app QR / deep links (no Pera modal).
 */
export async function wcConnect(opts: {
  onDisplayUri?: (uri: string) => void
  timeoutMs?: number
}): Promise<string> {
  cancelRequested = false
  const wc = await getConnector()

  if (wc.connected) {
    try {
      await wc.killSession()
    } catch {
      /* ignore */
    }
  }

  const timeoutMs = opts.timeoutMs ?? 3 * 60 * 1000

  return new Promise<string>((resolve, reject) => {
    let settled = false

    const cleanup = () => {
      wc.off('display_uri')
      wc.off('connect')
      wc.off('disconnect')
      clearTimeout(timer)
    }

    const fail = (err: Error) => {
      if (settled) return
      settled = true
      cleanup()
      reject(err)
    }

    const succeed = (address: string) => {
      if (settled) return
      settled = true
      cleanup()
      resolve(address)
    }

    const onDisplayUri = (_error: Error | null, payload: { params?: unknown[] } | null) => {
      const raw = payload?.params?.[0]
      if (typeof raw !== 'string') return
      opts.onDisplayUri?.(withAlgorandWalletConnectParam(raw))
    }

    const onConnect = (error: Error | null, payload: { params?: unknown[] } | null) => {
      if (error) {
        fail(error)
        return
      }
      const status = payload?.params?.[0] as { accounts?: string[] } | undefined
      const address = status?.accounts?.[0] ?? wc.accounts[0]
      if (!address) {
        fail(new Error('No account returned from wallet'))
        return
      }
      succeed(address)
    }

    const onDisconnect = () => {
      if (cancelRequested) {
        fail(new Error('Wallet connection cancelled'))
      }
    }

    const timer = setTimeout(() => {
      fail(new Error('The sign-in request timed out. Open your wallet app and try again.'))
    }, timeoutMs)

    wc.on('display_uri', onDisplayUri)
    wc.on('connect', onConnect)
    wc.on('disconnect', onDisconnect)

    void wc.createSession({ chainId: config.walletConnectChainId }).catch((e) => {
      fail(e instanceof Error ? e : new Error(String(e)))
    })
  })
}

export async function wcSignLoginProof(
  walletAddress: string,
  signingMessage: string,
): Promise<WalletProof> {
  const wc = await getConnector()
  if (!wc.connected) throw new Error('Wallet session is not connected')

  const signature = await trySignData(wc, walletAddress, signingMessage)
  if (signature) {
    return { proofMethod: 'signed_bytes', signatureB64: signature }
  }

  const signedTxnB64 = await signArc0025Txn(wc, walletAddress, signingMessage)
  return { proofMethod: 'arc0025_txn', signedTxnB64 }
}

/** Revive the bridge socket after returning from a mobile wallet app. */
export function wcWakeTransport(): void {
  const wc = connector
  if (!wc) return
  try {
    const transport = wc._transport
    if (transport) {
      transport.close()
      transport.open()
      return
    }
    wc.transportClose()
  } catch {
    /* ignore */
  }
}

export async function wcDisconnect(): Promise<void> {
  cancelRequested = true
  const wc = connector
  connector = null
  if (!wc) return
  try {
    if (wc.connected) await wc.killSession()
    else wc.transportClose()
  } catch {
    try {
      wc.transportClose()
    } catch {
      /* ignore */
    }
  }
}

export async function wcCancelPending(): Promise<void> {
  await wcDisconnect()
}
