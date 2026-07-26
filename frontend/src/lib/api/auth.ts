import { api, type JsonHeaders } from './client'

export const authApi = {
  async requestNonce(walletAddress: string) {
    return api.postJson('/api/v1/auth/nonce', { wallet_address: walletAddress })
  },

  async verify(opts: {
    walletAddress: string
    nonce: string
    proofMethod?: string
    signatureB64?: string
    signedTxnB64?: string
    arc0060?: Record<string, unknown>
  }) {
    return api.postJson('/api/v1/auth/verify-wallet-signature', {
      wallet_address: opts.walletAddress,
      nonce: opts.nonce,
      proof_method: opts.proofMethod ?? 'arc0060',
      signature_b64: opts.signatureB64,
      signed_txn_b64: opts.signedTxnB64,
      arc0060: opts.arc0060,
    })
  },

  async session(token: string) {
    return api.getJson('/api/v1/auth/session', { 'x-session-token': token })
  },

  async logout(token: string) {
    return api.postJson('/api/v1/auth/logout', {}, { 'x-session-token': token })
  },
}

export function sessionHeaders(token: string | null): JsonHeaders {
  return token ? { 'x-session-token': token } : {}
}
