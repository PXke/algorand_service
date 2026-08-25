import type { Breadcrumb } from '@bugsnag/browser'
import { config } from './config'

/**
 * Browser-side Bugsnag: passive client-side error monitoring, so real
 * user-facing JS bugs get caught even when nobody's watching devtools.
 * Mirrors the backend's init_bugsnag (backend/app/core/observability.py) —
 * opt-in on an env var, silent no-op if it's blank so local dev and test
 * runs never report.
 */

// Matches a full Algorand address (58-char base32, A-Z2-7). Truncated
// display forms (e.g. "RR4F…S4LS") don't match and are left alone — at that
// length they're already non-identifying.
const ALGO_ADDRESS_RE = /\b[A-Z2-7]{58}\b/g
const REDACTED = '[redacted]'

function scrubString(value: string): string {
  let out = value.replace(ALGO_ADDRESS_RE, REDACTED)
  // This app's own API design never puts auth material in a URL (session
  // token goes via the x-session-token header — see lib/api/admin.ts), but
  // scrub token/session/auth/key-ish query params and the one URL that does
  // carry a capability token (share-link revoke) anyway, as defense in
  // depth against a future endpoint or a third-party script doing otherwise.
  out = out.replace(/([?&](?:token|session|auth|key)[^=&]*=)[^&]+/gi, `$1${REDACTED}`)
  out = out.replace(/(\/share-links\/)[^/?#]+/i, `$1${REDACTED}`)
  return out
}

function scrubMetadata(metadata: Record<string, unknown> | undefined): void {
  if (!metadata) return
  for (const key of Object.keys(metadata)) {
    const value = metadata[key]
    if (typeof value === 'string') metadata[key] = scrubString(value)
  }
}

function scrubBreadcrumb(breadcrumb: Breadcrumb): void {
  breadcrumb.message = scrubString(breadcrumb.message)
  scrubMetadata(breadcrumb.metadata)
}

let starting: Promise<void> | null = null

/**
 * Deliberately gated on import.meta.env.PROD (Vite's built-in build-mode
 * flag), not just the presence of a key: a key sitting in a local .env must
 * not turn on reporting from a `vite dev` session.
 */
export function initBugsnag(): void {
  if (starting) return
  if (!config.bugsnagApiKey || !import.meta.env.PROD) return

  starting = import('@bugsnag/browser')
    .then(({ default: Bugsnag }) => {
      Bugsnag.start({
        apiKey: config.bugsnagApiKey,
        appVersion: config.appVersion || undefined,
        releaseStage: 'production',
        enabledReleaseStages: ['production'],
        autoTrackSessions: true,
        enabledErrorTypes: {
          unhandledExceptions: true,
          unhandledRejections: true,
        },
        // The client IP is the only automatic PII this SDK collects beyond
        // what's scrubbed below — skip it.
        collectUserIp: false,
        // Built-in key-based metadata scrub (belt): matches token/session/
        // auth/wallet-ish keys wherever Bugsnag attaches structured
        // metadata (breadcrumbs, requests, custom sections).
        redactedKeys: [
          'token',
          'session',
          'authorization',
          'password',
          'secret',
          /wallet.?address/i,
          /session.?token/i,
          /api.?key/i,
        ],
        // Value-based scrub (suspenders) for content that isn't under an
        // obviously-sensitive key — e.g. a UI click breadcrumb capturing
        // visible text next to a rendered wallet address.
        onBreadcrumb: scrubBreadcrumb,
        onError: (event) => {
          if (event.context) event.context = scrubString(event.context)
          if (event.request?.url) event.request.url = scrubString(event.request.url)
          for (const breadcrumb of event.breadcrumbs) scrubBreadcrumb(breadcrumb)
          // No Bugsnag.setUser() call anywhere in this app: wallet identity
          // never becomes the Bugsnag "user", and the admin session token
          // lives only in localStorage / a request header, never anywhere
          // Bugsnag reads from automatically.
        },
      })
    })
    .catch(() => {
      // Never let observability wiring break the app (matches the
      // try/except around bugsnag.configure() on the backend).
    })
}
