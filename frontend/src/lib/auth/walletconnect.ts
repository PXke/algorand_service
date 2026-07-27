import '../polyfills'
import { config } from '../config'
import { summarizeWcUri, wcDebug } from './wcDebug'

export type WalletProof =
  | { proofMethod: 'signed_bytes'; signatureB64: string }
  | { proofMethod: 'arc0025_txn'; signedTxnB64: string }

type Transportish = {
  close: () => void
  open: () => void
  subscribe?: (topic: string) => void
  readyState?: number
  opts?: { subscriptions?: string[] }
}

type WalletConnectInstance = {
  connected: boolean
  accounts: string[]
  uri: string
  clientId?: string
  peerId?: string
  handshakeTopic?: string
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
  _transport?: Transportish
}

let connector: WalletConnectInstance | null = null
let cancelRequested = false
/** Called after transport revive so a completed-but-missed connect can settle. */
let afterWakeCheck: (() => void) | null = null
let wakeInFlight = false
/** True while a sign request is waiting on the bridge — do not close/open socket. */
let signRequestInFlight = false

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

/**
 * WalletConnect v1 URIs must keep `bridge=https://…` (raw). Some paths
 * (URL.toString, URLSearchParams, or the client) leave `bridge=https%3A%2F%2F…`.
 * Encoding that again for `perawallet-wc://…?uri=` makes Pera open with no
 * connect sheet. Decode over-encoded query values in place.
 */
export function normalizeWcUri(wcUri: string): string {
  const q = wcUri.indexOf('?')
  if (q < 0) return wcUri
  const head = wcUri.slice(0, q + 1)
  const pairs = wcUri.slice(q + 1).split('&')
  const out: string[] = []
  for (const pair of pairs) {
    const eq = pair.indexOf('=')
    if (eq < 0) {
      out.push(pair)
      continue
    }
    const key = pair.slice(0, eq)
    let value = pair.slice(eq + 1)
    if (key === 'bridge' || key === 'key') {
      // Decode until stable so bridge is literal https://…
      for (let i = 0; i < 3; i++) {
        if (!/%[0-9A-Fa-f]{2}/.test(value)) break
        try {
          const next = decodeURIComponent(value)
          if (next === value) break
          value = next
        } catch {
          break
        }
      }
    }
    out.push(`${key}=${value}`)
  }
  return head + out.join('&')
}

/** ARC-0025: wallets filter WC URIs with `algorand=true`.
 * Never run wc: URIs through `URL` / `URLSearchParams.toString()` — they
 * re-encode `bridge=https://…` and break Pera's connect sheet.
 */
export function withAlgorandWalletConnectParam(wcUri: string): string {
  let uri = normalizeWcUri(wcUri)
  if (/[?&]algorand=true(?:&|$)/.test(uri)) return uri
  return uri.includes('?') ? `${uri}&algorand=true` : `${uri}?algorand=true`
}

export function isMobileWalletClient(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
}

function isAndroidClient(): boolean {
  return typeof navigator !== 'undefined' && /Android/i.test(navigator.userAgent)
}

/** Firefox (and other Gecko) often ignore bare `wc:` navigations on Android. */
export function isGeckoMobile(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Firefox|FxiOS/i.test(navigator.userAgent)
}

function detectBrowserName(): string | null {
  if (typeof navigator === 'undefined') return null
  const ua = navigator.userAgent
  if (/Firefox|FxiOS/i.test(ua)) return 'Firefox'
  if (/Edg\//i.test(ua)) return 'Microsoft Edge'
  if (/OPR\//i.test(ua) || /Opera/i.test(ua)) return 'Opera'
  if (/Chrome/i.test(ua) && !/Edg\//i.test(ua)) return 'Chrome'
  if (/Safari/i.test(ua)) return 'Safari'
  return null
}

/**
 * Deep link that opens Pera with a WalletConnect v1 session.
 *
 * Firefox Android once showed the connect sheet with Pera's HTTPS App Link
 * (custom-scheme iframe/_blank opens the app but drops the session).
 *
 * HARD INVARIANTS:
 * - Never `intent://…&key=…#Intent` (`&` truncates WC key)
 * - Always normalize bridge to raw `https://` before encodeURIComponent
 */
export function walletDeepLink(wcUri: string): string {
  const browser = detectBrowserName()
  const normalized = withAlgorandWalletConnectParam(wcUri)

  // Firefox Android: official Pera App Link — this is what delivered the
  // account/connect sheet earlier in the session.
  if (isAndroidClient() && isGeckoMobile()) {
    let uri = normalized
    if (browser && !/[?&]browser=/.test(uri)) {
      uri = `${uri}&browser=${encodeURIComponent(browser)}`
    }
    const link = `https://perawallet.app/qr/perawallet-wc/?uri=${encodeURIComponent(uri)}`
    wcDebug(
      `deepLink https-applink doubleEnc=${/bridge%3Dhttps%253A%252F%252F/.test(link)} ${summarizeWcUri(normalized)}`,
    )
    return link
  }

  const withOuterBrowser = (link: string) =>
    browser
      ? `${link}${link.includes('?') ? '&' : '?'}browser=${encodeURIComponent(browser)}`
      : link

  // Chrome Android: bare `wc:` + browser hint (OS intent resolver)
  if (isAndroidClient()) {
    const link = withOuterBrowser(normalized)
    wcDebug(`deepLink chrome ${summarizeWcUri(normalized)}`)
    return link
  }

  // iOS / desktop: Pera custom scheme
  const link = withOuterBrowser(`perawallet-wc://wc?uri=${encodeURIComponent(normalized)}`)
  wcDebug(`deepLink scheme ${summarizeWcUri(normalized)}`)
  return link
}

/** @deprecated alias */
export function walletDeepLinkCustomScheme(wcUri: string): string {
  const browser = detectBrowserName()
  const normalized = withAlgorandWalletConnectParam(wcUri)
  const base = `perawallet-wc://wc?uri=${encodeURIComponent(normalized)}`
  return browser ? `${base}&browser=${encodeURIComponent(browser)}` : base
}

/** HTTPS App Link helper (same as Firefox primary). */
export function walletHttpsDeepLink(wcUri: string): string {
  return walletDeepLink(wcUri)
}

/** Just wake the installed wallet app (e.g. after pairing, for sign approval). */
export function walletAppLaunchLink(): string {
  const browser = detectBrowserName()
  const base = 'perawallet-wc://'
  return browser ? `${base}?browser=${encodeURIComponent(browser)}` : base
}

/**
 * Open a wallet deep link.
 *
 * Firefox Android + session URI: same-tab `location.assign` — the only open
 * method that previously produced Pera's connect sheet. The SPA may pause;
 * visibility/focus handlers revive the bridge when you return.
 *
 * Elsewhere: iframe + _blank (keeps SPA alive). Never intent://.
 */
export function openWalletDeepLink(link: string, _opts?: { sameTab?: boolean }): boolean {
  if (/^intent:/i.test(link)) {
    wcDebug('open BLOCKED intent://')
    return false
  }

  const geckoAndroid = isAndroidClient() && isGeckoMobile()
  const hasSessionUri =
    link.includes('uri=') || link.startsWith('https://perawallet.app/')

  // Pairing on Firefox: assign so Android hands the full App Link to Pera.
  if (geckoAndroid && hasSessionUri) {
    try {
      wcDebug(`open assign ${link.slice(0, 96)}…`)
      window.location.assign(link)
      return true
    } catch {
      wcDebug('open assign failed')
      return false
    }
  }

  let opened = false
  const methods: string[] = []

  try {
    const iframe = document.createElement('iframe')
    iframe.setAttribute('aria-hidden', 'true')
    iframe.style.cssText =
      'display:none;width:0;height:0;border:0;position:fixed;left:-9999px'
    iframe.src = link
    document.body.appendChild(iframe)
    window.setTimeout(() => iframe.remove(), 3000)
    opened = true
    methods.push('iframe')
  } catch {
    /* ignore */
  }

  try {
    const a = document.createElement('a')
    a.setAttribute('href', link)
    a.rel = 'noopener noreferrer'
    a.target = '_blank'
    a.style.display = 'none'
    document.body.appendChild(a)
    a.click()
    a.remove()
    opened = true
    methods.push('_blank')
  } catch {
    try {
      const w = window.open(link, '_blank', 'noopener,noreferrer')
      if (w) {
        opened = true
        methods.push('window.open')
      }
    } catch {
      /* ignore */
    }
  }

  wcDebug(`open ${opened ? 'ok' : 'fail'} [${methods.join('+')}]`)
  return opened
}

/** Open Pera with the WC session URI. */
export function openWalletDeepLinkRobust(wcUri: string): boolean {
  return openWalletDeepLink(walletDeepLink(wcUri))
}

function transportReadyState(): string {
  const rs = connector?._transport?.readyState
  return rs == null ? 'n/a' : String(rs)
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
  wcDebug(`connector bridge=${config.walletConnectBridge} chainId=${config.walletConnectChainId}`)
  return connector
}

/**
 * SocketTransport.open() only re-subscribes `opts.subscriptions` (defaults to
 * clientId). Persist handshake/peer topics there so a reconnect can still
 * deliver the session approval / sign response.
 */
function persistTopicSubscriptions(wc: WalletConnectInstance): void {
  const transport = wc._transport
  const subs = transport?.opts?.subscriptions
  if (!Array.isArray(subs)) return
  for (const topic of [wc.clientId, wc.handshakeTopic, wc.peerId]) {
    if (typeof topic === 'string' && topic.length > 0 && !subs.includes(topic)) {
      subs.push(topic)
    }
  }
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
  onRequestSent?: () => void,
): Promise<string | null> {
  try {
    const pending = wc.sendCustomRequest({
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
    // Request is on the wire; open the wallet app only after this.
    onRequestSent?.()
    const result = await pending
    return extractSignatureB64(result)
  } catch {
    return null
  }
}

async function signArc0025Txn(
  wc: WalletConnectInstance,
  walletAddress: string,
  signingMessage: string,
  onRequestSent?: () => void,
): Promise<string> {
  const algosdk = (await import('algosdk')).default
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
  const pending = wc.sendCustomRequest({
    method: 'algo_signTxn',
    params: [[walletTxn], { message: SIGN_IN_PROMPT }],
  })
  onRequestSent?.()
  const result = await pending
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
      afterWakeCheck = null
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
      wcDebug(`connect ok addr=${address.slice(0, 8)}… readyState=${transportReadyState()}`)
      resolve(address)
    }

    const takeConnectedAccount = () => {
      if (!wc.connected) return
      const address = wc.accounts?.[0]
      if (address) succeed(address)
    }

    // Mobile OS often drops the bridge WS while the wallet app is open; on
    // return the approval may already be on the bridge — revive + re-check.
    afterWakeCheck = () => {
      wcDebug(`afterWake connected=${wc.connected} readyState=${transportReadyState()}`)
      takeConnectedAccount()
    }

    const onDisplayUri = (_error: Error | null, payload: { params?: unknown[] } | null) => {
      const raw = payload?.params?.[0]
      if (typeof raw !== 'string') return
      persistTopicSubscriptions(wc)
      const patched = withAlgorandWalletConnectParam(raw)
      wcDebug(`display_uri ${summarizeWcUri(patched)}`)
      opts.onDisplayUri?.(patched)
    }

    const onConnect = (error: Error | null, payload: { params?: unknown[] } | null) => {
      if (error) {
        wcDebug(`connect error: ${error.message}`)
        fail(error)
        return
      }
      persistTopicSubscriptions(wc)
      const status = payload?.params?.[0] as { accounts?: string[] } | undefined
      const address = status?.accounts?.[0] ?? wc.accounts[0]
      if (!address) {
        fail(new Error('No account returned from wallet'))
        return
      }
      succeed(address)
    }

    const onDisconnect = () => {
      wcDebug('disconnect event')
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

    wcDebug(`createSession chainId=${config.walletConnectChainId}`)
    void wc.createSession({ chainId: config.walletConnectChainId }).catch((e) => {
      fail(e instanceof Error ? e : new Error(String(e)))
    })
  })
}

export async function wcSignLoginProof(
  walletAddress: string,
  signingMessage: string,
  opts?: { onRequestSent?: () => void },
): Promise<WalletProof> {
  const wc = await getConnector()
  if (!wc.connected) throw new Error('Wallet session is not connected')
  persistTopicSubscriptions(wc)

  // Do NOT wake/close the socket here — that drops in-flight sign responses.
  // Wake only when the tab returns from the wallet app (visibility handler).
  signRequestInFlight = true
  wcDebug(`sign start readyState=${transportReadyState()}`)
  try {
    const wrappedSent = () => {
      wcDebug('sign request sent — may open wallet')
      opts?.onRequestSent?.()
    }

    const signature = await trySignData(wc, walletAddress, signingMessage, wrappedSent)
    if (signature) {
      wcDebug('sign ok algo_signData')
      return { proofMethod: 'signed_bytes', signatureB64: signature }
    }

    const signedTxnB64 = await signArc0025Txn(
      wc,
      walletAddress,
      signingMessage,
      wrappedSent,
    )
    wcDebug('sign ok algo_signTxn')
    return { proofMethod: 'arc0025_txn', signedTxnB64 }
  } finally {
    signRequestInFlight = false
  }
}

/**
 * Revive the bridge socket after returning from a mobile wallet app.
 *
 * Firefox debug proved close+open while readyState=1 leaves the socket CLOSED
 * and drops the session approval. Only reopen when the transport is dead.
 */
export function wcWakeTransport(): void {
  const wc = connector
  if (!wc || wakeInFlight) return
  wakeInFlight = true
  window.setTimeout(() => {
    wakeInFlight = false
  }, 800)

  persistTopicSubscriptions(wc)
  const transport = wc._transport
  const rs = transport?.readyState
  // WebSocket readyState: 0 CONNECTING, 1 OPEN, 2 CLOSING, 3 CLOSED
  const alive = rs === 0 || rs === 1

  if (alive) {
    wcDebug(`wake noop (socket alive) readyState=${rs} signInFlight=${signRequestInFlight}`)
    window.setTimeout(() => {
      try {
        afterWakeCheck?.()
      } catch {
        /* ignore */
      }
    }, 200)
    return
  }

  wcDebug(`wake open (socket dead) readyState=${transportReadyState()} signInFlight=${signRequestInFlight}`)
  try {
    if (transport) {
      try {
        transport.open()
      } catch {
        /* ignore */
      }
    }
  } catch {
    /* ignore */
  }

  window.setTimeout(() => {
    try {
      afterWakeCheck?.()
    } catch {
      /* ignore */
    }
  }, 400)
}

/** One revive now; one retry shortly if still unpaired and socket still dead. */
export function wcWakeTransportBurst(): void {
  wcWakeTransport()
  if (signRequestInFlight) return
  window.setTimeout(() => {
    const wc = connector
    if (!wc || wc.connected || signRequestInFlight) return
    const rs = wc._transport?.readyState
    if (rs === 0 || rs === 1) {
      // Still open — don't thrash; just re-check accounts.
      try {
        afterWakeCheck?.()
      } catch {
        /* ignore */
      }
      return
    }
    wcWakeTransport()
  }, 1500)
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
