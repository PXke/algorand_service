/**
 * Wallet-provider routing. Each provider (Pera/Defly/Lute) lands on the
 * SAME sign-in flow (`session.ts`'s signInWithWalletConnect) and the SAME
 * backend verification cascade (`proof_method`: arc0025_txn / arc0060 /
 * signed_bytes / legacy_message) — only the connect+sign calls differ.
 *
 * Pera keeps calling its own unmodified functions (peraConnect,
 * peraSignLoginProof, peraDisconnect, peraWakeTransportBurst) through this
 * thin wrapper, so its runtime behavior is unchanged from before wallet
 * choice existed.
 */

export type WalletId = 'pera' | 'defly' | 'lute'

export type WalletOption = { id: WalletId; label: string }

export const WALLET_OPTIONS: WalletOption[] = [
  { id: 'pera', label: 'Pera' },
  { id: 'defly', label: 'Defly' },
  { id: 'lute', label: 'Lute' },
]

export function isWalletId(value: string): value is WalletId {
  return WALLET_OPTIONS.some((w) => w.id === value)
}

export type LoginChallenge = {
  nonce: string
  signingMessage: string
  caip122: Record<string, unknown>
}

export type WalletProof =
  | { proofMethod: 'signed_bytes'; signatureB64: string }
  | { proofMethod: 'arc0025_txn'; signedTxnB64: string }
  | {
      proofMethod: 'arc0060'
      arc0060: {
        data_b64: string
        signature_b64: string
        authenticator_data_b64: string
        domain: string
      }
    }

export type WalletAdapter = {
  id: WalletId
  connect(): Promise<string>
  signLoginProof(walletAddress: string, challenge: LoginChallenge): Promise<WalletProof>
  disconnect(): Promise<void>
  wakeTransport(): void
  /** Deep link to reopen the wallet app while awaiting a mobile sign approval, or null. */
  appLaunchLink(): string | null
}

export async function loadWalletAdapter(id: WalletId): Promise<WalletAdapter> {
  switch (id) {
    case 'pera': {
      const pera = await import('./pera')
      const wc = await import('./walletconnect')
      return {
        id: 'pera',
        connect: () => pera.peraConnect(),
        signLoginProof: (addr, challenge) =>
          pera.peraSignLoginProof(addr, challenge.signingMessage),
        disconnect: () => pera.peraDisconnect(),
        wakeTransport: () => pera.peraWakeTransportBurst(),
        appLaunchLink: () => wc.walletAppLaunchLink(),
      }
    }
    case 'defly': {
      const defly = await import('./defly')
      return {
        id: 'defly',
        connect: () => defly.deflyConnect(),
        signLoginProof: (addr, challenge) =>
          defly.deflySignLoginProof(addr, challenge.signingMessage),
        disconnect: () => defly.deflyDisconnect(),
        wakeTransport: () => defly.deflyWakeTransportBurst(),
        appLaunchLink: () => defly.deflyAppLaunchLink(),
      }
    }
    case 'lute': {
      const lute = await import('./lute')
      return {
        id: 'lute',
        connect: () => lute.luteConnect(),
        signLoginProof: (addr, challenge) => lute.luteSignLoginProof(addr, challenge),
        disconnect: () => lute.luteDisconnect(),
        // Lute is popup-based, not WalletConnect — no bridge to revive/relaunch.
        wakeTransport: () => {},
        appLaunchLink: () => null,
      }
    }
  }
}
