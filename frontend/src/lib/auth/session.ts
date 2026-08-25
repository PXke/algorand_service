import { writable, derived, get } from 'svelte/store'
import { ApiException } from '../api/client'
import { authApi } from '../api/auth'
import { isAdminWallet } from '../config'
import type { LoginChallenge, WalletId } from './walletProviders'

export type { WalletId } from './walletProviders'

const TOKEN_KEY = 'wallet_auth_session_token'

export type Session = {
  token: string
  walletAddress: string
  expiresInEpoch?: number
}

export type WalletFlowPhase = 'idle' | 'pairing' | 'signing' | 'error'

export type WalletFlowState = {
  phase: WalletFlowPhase
  walletAddress: string | null
  error: string | null
}

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

function writeToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore */
  }
}

export const session = writable<Session | null>(null)
export const sessionReady = writable(false)
export const authBusy = writable(false)
export const authError = writable<string | null>(null)
export const walletFlow = writable<WalletFlowState>({
  phase: 'idle',
  walletAddress: null,
  error: null,
})

export const walletAddress = derived(session, ($s) => $s?.walletAddress ?? null)
export const sessionToken = derived(session, ($s) => $s?.token ?? null)
export const isAdmin = derived(walletAddress, ($w) => isAdminWallet($w))

let cancelSignIn = false

function applySession(
  token: string,
  walletAddress: string,
  expiresInEpoch?: number,
): void {
  writeToken(token)
  session.set({ token, walletAddress, expiresInEpoch })
}

/** Only wipe storage when the failed restore still owns the stored token. */
function clearTokenIfUnchanged(expected: string): void {
  if (readStoredToken() === expected) {
    writeToken(null)
    session.set(null)
  }
}

export async function restoreSession(): Promise<void> {
  const token = readStoredToken()
  if (!token) {
    session.set(null)
    sessionReady.set(true)
    return
  }
  try {
    const info = await authApi.session(token)
    // A newer login may have won while this request was in flight.
    if (readStoredToken() !== token) {
      sessionReady.set(true)
      return
    }
    const addr = String(info.wallet_address ?? '')
    if (!addr) {
      clearTokenIfUnchanged(token)
      sessionReady.set(true)
      return
    }
    session.set({
      token,
      walletAddress: addr,
      expiresInEpoch:
        typeof info.expires_in_epoch === 'number' ? info.expires_in_epoch : undefined,
    })
  } catch (e) {
    if (readStoredToken() !== token) {
      sessionReady.set(true)
      return
    }
    const expired =
      e instanceof ApiException &&
      (e.statusCode === 401 ||
        e.code === 'invalid_or_expired_session' ||
        e.code === 'missing_session_token')
    // Network / 5xx: keep the token so a refresh can recover.
    if (expired) clearTokenIfUnchanged(token)
  } finally {
    sessionReady.set(true)
  }
}

export async function completeSignIn(opts: {
  walletAddress: string
  nonce: string
  signatureB64?: string
  signedTxnB64?: string
  proofMethod?: string
  arc0060?: Record<string, unknown>
}): Promise<void> {
  authBusy.set(true)
  authError.set(null)
  try {
    const res = await authApi.verify({
      walletAddress: opts.walletAddress,
      nonce: opts.nonce,
      signatureB64: opts.signatureB64,
      signedTxnB64: opts.signedTxnB64,
      proofMethod: opts.proofMethod ?? 'signed_bytes',
      arc0060: opts.arc0060,
    })
    const token = String(res.session_token ?? '')
    const addr = String(res.wallet_address ?? opts.walletAddress)
    if (!token) throw new Error('No session token returned')
    applySession(
      token,
      addr,
      typeof res.expires_in_epoch === 'number' ? res.expires_in_epoch : undefined,
    )
  } catch (e) {
    authError.set(e instanceof Error ? e.message : String(e))
    throw e
  } finally {
    authBusy.set(false)
  }
}

export async function startChallenge(walletAddress: string) {
  authBusy.set(true)
  authError.set(null)
  try {
    return await authApi.requestNonce(walletAddress)
  } catch (e) {
    authError.set(e instanceof Error ? e.message : String(e))
    throw e
  } finally {
    authBusy.set(false)
  }
}

function resetWalletFlow() {
  walletFlow.set({ phase: 'idle', walletAddress: null, error: null })
}

// Tracks which wallet the current/last sign-in attempt used, so
// cancel/wake/logout disconnect the right one. Defaults to 'pera' —
// same wallet every caller used before wallet choice existed, so a
// disconnect/wake called before any pick still behaves as before.
let activeWalletId: WalletId = 'pera'

/**
 * Wallet sign-in: connect (the wallet's own modal handles QR/deep-link/
 * Firefox quirks) → nonce → sign → verify. Each provider (Pera/Defly/Lute)
 * lands on this same flow via `loadWalletAdapter` (see walletProviders.ts)
 * — Pera keeps calling its own unmodified peraConnect/peraSignLoginProof/
 * peraDisconnect functions through that adapter, so its behavior is
 * unchanged from when this only supported Pera.
 *
 * (2026-08-02: this used to wrap a generic WalletConnect v1 client, dropped
 * because it drifted out of sync with Pera's own, actively maintained
 * client and QR pairing silently stopped completing — see pera.ts.)
 */
export async function signInWithWalletConnect(walletId: WalletId = 'pera'): Promise<void> {
  cancelSignIn = false
  activeWalletId = walletId
  authBusy.set(true)
  authError.set(null)
  walletFlow.set({ phase: 'pairing', walletAddress: null, error: null })

  const { loadWalletAdapter } = await import('./walletProviders')
  const { isMobileWalletClient, openWalletDeepLink } = await import('./walletconnect')
  const adapter = await loadWalletAdapter(walletId)

  try {
    const address = await adapter.connect()
    if (cancelSignIn) throw new Error('Wallet connection cancelled')

    walletFlow.set({
      phase: 'signing',
      walletAddress: address,
      error: null,
    })

    const challengeRes = await authApi.requestNonce(address)
    const nonce = String(challengeRes.nonce ?? '')
    const signingMessage = String(challengeRes.signing_message ?? '')
    if (!nonce || !signingMessage) throw new Error('Invalid auth challenge')
    const challenge: LoginChallenge = {
      nonce,
      signingMessage,
      caip122: (challengeRes.caip122 as Record<string, unknown> | undefined) ?? {},
    }

    // Open the wallet only AFTER the sign request is on the bridge — opening
    // earlier races the socket wake and drops the reply on Firefox.
    const proofPromise = adapter.signLoginProof(address, challenge)
    const appLink = adapter.appLaunchLink()
    if (appLink && isMobileWalletClient()) {
      window.setTimeout(() => {
        openWalletDeepLink(appLink)
      }, 200)
    }
    const proof = await proofPromise
    if (cancelSignIn) throw new Error('Wallet connection cancelled')

    await completeSignIn({
      walletAddress: address,
      nonce,
      signatureB64: proof.proofMethod === 'signed_bytes' ? proof.signatureB64 : undefined,
      signedTxnB64: proof.proofMethod === 'arc0025_txn' ? proof.signedTxnB64 : undefined,
      arc0060: proof.proofMethod === 'arc0060' ? proof.arc0060 : undefined,
      proofMethod: proof.proofMethod,
    })
    try {
      await adapter.disconnect()
    } catch {
      /* ignore */
    }
    resetWalletFlow()
  } catch (e) {
    try {
      await adapter.disconnect()
    } catch {
      /* ignore */
    }
    if (cancelSignIn) {
      resetWalletFlow()
      authError.set(null)
      return
    }
    const message = e instanceof Error ? e.message : String(e)
    authError.set(message)
    walletFlow.set({
      phase: 'error',
      walletAddress: get(walletFlow).walletAddress,
      error: message,
    })
    throw e
  } finally {
    authBusy.set(false)
  }
}

export async function cancelWalletSignIn(): Promise<void> {
  cancelSignIn = true
  authBusy.set(false)
  authError.set(null)
  resetWalletFlow()
  const { loadWalletAdapter } = await import('./walletProviders')
  const adapter = await loadWalletAdapter(activeWalletId)
  await adapter.disconnect()
}

export function wakeWalletTransport(): void {
  void import('./walletProviders').then(({ loadWalletAdapter }) =>
    loadWalletAdapter(activeWalletId).then((adapter) => adapter.wakeTransport()),
  )
}

export async function logout(): Promise<void> {
  const s = get(session)
  if (s?.token) {
    try {
      await authApi.logout(s.token)
    } catch {
      /* ignore */
    }
  }
  writeToken(null)
  session.set(null)
  // Clearing the analytics opt-out belongs HERE, not in a reactive effect on
  // isAdmin: that flag is false while a session is being restored, so clearing
  // from there wiped the cookie on every page load.
  try {
    const { setAnalyticsOptOut } = await import('../analyticsOptOut')
    setAnalyticsOptOut(false)
  } catch {
    /* ignore */
  }
  try {
    const { loadWalletAdapter } = await import('./walletProviders')
    const adapter = await loadWalletAdapter(activeWalletId)
    await adapter.disconnect()
  } catch {
    /* ignore */
  }
}
