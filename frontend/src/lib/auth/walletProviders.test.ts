import { describe, expect, it, vi } from 'vitest'

vi.mock('./pera', () => ({
  peraConnect: vi.fn(async () => 'PERA_ADDR'),
  peraSignLoginProof: vi.fn(async (_addr: string, _msg: string) => ({
    proofMethod: 'signed_bytes',
    signatureB64: 'pera-sig',
  })),
  peraDisconnect: vi.fn(async () => undefined),
  peraWakeTransportBurst: vi.fn(),
}))

vi.mock('./defly', () => ({
  deflyConnect: vi.fn(async () => 'DEFLY_ADDR'),
  deflySignLoginProof: vi.fn(async (_addr: string, _msg: string) => ({
    proofMethod: 'arc0025_txn',
    signedTxnB64: 'defly-txn',
  })),
  deflyDisconnect: vi.fn(async () => undefined),
  deflyWakeTransportBurst: vi.fn(),
  deflyAppLaunchLink: vi.fn(() => 'defly-wc://'),
}))

vi.mock('./lute', () => ({
  luteConnect: vi.fn(async () => 'LUTE_ADDR'),
  luteSignLoginProof: vi.fn(async () => ({
    proofMethod: 'arc0060',
    arc0060: {
      data_b64: 'd',
      signature_b64: 's',
      authenticator_data_b64: 'a',
      domain: 'example.test',
    },
  })),
  luteDisconnect: vi.fn(async () => undefined),
}))

vi.mock('./walletconnect', () => ({
  walletAppLaunchLink: vi.fn(() => 'perawallet-wc://'),
}))

const { WALLET_OPTIONS, isWalletId, loadWalletAdapter } = await import('./walletProviders')
const pera = await import('./pera')
const defly = await import('./defly')
const lute = await import('./lute')

describe('WALLET_OPTIONS', () => {
  it('lists exactly pera, defly, lute with distinct labels', () => {
    expect(WALLET_OPTIONS.map((w) => w.id)).toEqual(['pera', 'defly', 'lute'])
    const labels = WALLET_OPTIONS.map((w) => w.label)
    expect(new Set(labels).size).toBe(labels.length)
  })
})

describe('isWalletId', () => {
  it('accepts only the known wallet ids', () => {
    expect(isWalletId('pera')).toBe(true)
    expect(isWalletId('defly')).toBe(true)
    expect(isWalletId('lute')).toBe(true)
    expect(isWalletId('metamask')).toBe(false)
    expect(isWalletId('')).toBe(false)
  })
})

describe('loadWalletAdapter', () => {
  const challenge = { nonce: 'n', signingMessage: 'm', caip122: { domain: 'example.test' } }

  it("routes 'pera' to pera.ts's own unmodified functions (signed_bytes/arc0025_txn cascade)", async () => {
    const adapter = await loadWalletAdapter('pera')
    expect(adapter.id).toBe('pera')

    await expect(adapter.connect()).resolves.toBe('PERA_ADDR')
    expect(pera.peraConnect).toHaveBeenCalledTimes(1)

    const proof = await adapter.signLoginProof('ADDR', challenge)
    expect(proof).toEqual({ proofMethod: 'signed_bytes', signatureB64: 'pera-sig' })
    expect(pera.peraSignLoginProof).toHaveBeenCalledWith('ADDR', challenge.signingMessage)

    expect(adapter.appLaunchLink()).toBe('perawallet-wc://')

    await adapter.disconnect()
    expect(pera.peraDisconnect).toHaveBeenCalledTimes(1)

    adapter.wakeTransport()
    expect(pera.peraWakeTransportBurst).toHaveBeenCalledTimes(1)
  })

  it("routes 'defly' to the ARC-0025 txn signing path (no signData in Defly's SDK)", async () => {
    const adapter = await loadWalletAdapter('defly')
    expect(adapter.id).toBe('defly')

    await expect(adapter.connect()).resolves.toBe('DEFLY_ADDR')
    const proof = await adapter.signLoginProof('ADDR', challenge)
    expect(proof).toEqual({ proofMethod: 'arc0025_txn', signedTxnB64: 'defly-txn' })
    expect(defly.deflySignLoginProof).toHaveBeenCalledWith('ADDR', challenge.signingMessage)
    expect(adapter.appLaunchLink()).toBe('defly-wc://')

    await adapter.disconnect()
    expect(defly.deflyDisconnect).toHaveBeenCalledTimes(1)
  })

  it("routes 'lute' to the native ARC-0060 signData path with no deep link (popup-based, not WalletConnect)", async () => {
    const adapter = await loadWalletAdapter('lute')
    expect(adapter.id).toBe('lute')

    await expect(adapter.connect()).resolves.toBe('LUTE_ADDR')
    const proof = await adapter.signLoginProof('ADDR', challenge)
    expect(proof.proofMethod).toBe('arc0060')
    expect(lute.luteSignLoginProof).toHaveBeenCalledWith('ADDR', challenge)

    expect(adapter.appLaunchLink()).toBeNull()
    // No bridge to revive — must be a safe no-op, not a throw.
    expect(() => adapter.wakeTransport()).not.toThrow()

    await adapter.disconnect()
    expect(lute.luteDisconnect).toHaveBeenCalledTimes(1)
  })
})
