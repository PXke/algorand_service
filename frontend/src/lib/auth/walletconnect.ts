import '../polyfills'
import { wcDebug, summarizeWcUri } from './wcDebug'

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
