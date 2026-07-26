import { writable, derived, get } from 'svelte/store'
import { ApiException } from '../api/client'
import { authApi } from '../api/auth'
import { isAdminWallet } from '../config'
import {
  wcCancelPending,
  wcConnect,
  wcDisconnect,
  wcSignLoginProof,
  wcWakeTransport,
} from './walletconnect'

const TOKEN_KEY = 'wallet_auth_session_token'

export type Session = {
  token: string
  walletAddress: string
  expiresInEpoch?: number
}

export type WalletFlowPhase = 'idle' | 'pairing' | 'signing' | 'error'

export type WalletFlowState = {
  phase: WalletFlowPhase
  uri: string | null
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
  uri: null,
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
  walletFlow.set({ phase: 'idle', uri: null, walletAddress: null, error: null })
}

/**
 * Universal WalletConnect sign-in: QR / deep-link pair → nonce → sign → verify.
 * Works with Pera, Defly, and other ARC-0025 Algorand wallets.
 */
export async function signInWithWalletConnect(): Promise<void> {
  cancelSignIn = false
  authBusy.set(true)
  authError.set(null)
  walletFlow.set({ phase: 'pairing', uri: null, walletAddress: null, error: null })

  try {
    const address = await wcConnect({
      onDisplayUri: (uri) => {
        walletFlow.update((s) => ({ ...s, phase: 'pairing', uri }))
      },
    })
    if (cancelSignIn) throw new Error('Wallet connection cancelled')

    walletFlow.set({
      phase: 'signing',
      uri: get(walletFlow).uri,
      walletAddress: address,
      error: null,
    })

    const challenge = await authApi.requestNonce(address)
    const nonce = String(challenge.nonce ?? '')
    const signingMessage = String(challenge.signing_message ?? '')
    if (!nonce || !signingMessage) throw new Error('Invalid auth challenge')

    const proof = await wcSignLoginProof(address, signingMessage)
    if (cancelSignIn) throw new Error('Wallet connection cancelled')

    await completeSignIn({
      walletAddress: address,
      nonce,
      signatureB64: proof.proofMethod === 'signed_bytes' ? proof.signatureB64 : undefined,
      signedTxnB64: proof.proofMethod === 'arc0025_txn' ? proof.signedTxnB64 : undefined,
      proofMethod: proof.proofMethod,
    })
    // Durable login is the API session token; drop the WC bridge pairing so a
    // later dead socket cannot confuse a re-login.
    try {
      await wcDisconnect()
    } catch {
      /* ignore */
    }
    resetWalletFlow()
  } catch (e) {
    try {
      await wcDisconnect()
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
      uri: get(walletFlow).uri,
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
  await wcCancelPending()
}

export function wakeWalletTransport(): void {
  wcWakeTransport()
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
  await wcDisconnect()
}
