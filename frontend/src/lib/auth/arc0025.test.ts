import { beforeEach, describe, expect, it, vi } from 'vitest'

// Fake the parts of algosdk our code touches, so this exercises OUR
// glue logic (what fields we pass to the txn builder, and how we merge
// algod's suggested params with the fee override) without needing a real
// algod endpoint or modeling algosdk's Transaction internals.
const suggestedParamsFromAlgod = {
  fee: 1000,
  firstValid: 100,
  lastValid: 1100,
  genesisID: 'testnet-v1.0',
  genesisHash: 'fake-hash==',
}

const getTransactionParamsDo = vi.fn(async () => suggestedParamsFromAlgod)
const getTransactionParams = vi.fn(() => ({ do: getTransactionParamsDo }))
const makePaymentTxnWithSuggestedParamsFromObject = vi.fn((args: unknown) => ({
  __fakeTxn: true,
  args,
}))

class FakeAlgodv2 {
  token: string
  server: string
  port: string
  constructor(token: string, server: string, port: string) {
    this.token = token
    this.server = server
    this.port = port
  }
  getTransactionParams = getTransactionParams
}

vi.mock('algosdk', () => ({
  default: {
    Algodv2: FakeAlgodv2,
    makePaymentTxnWithSuggestedParamsFromObject,
  },
}))

vi.mock('../config', () => ({
  config: { algodApiUrl: 'https://algod.example.test' },
}))

const { buildAuthPaymentTxn, getAlgodClient, bytesToBase64 } = await import('./arc0025')

describe('buildAuthPaymentTxn', () => {
  beforeEach(() => {
    makePaymentTxnWithSuggestedParamsFromObject.mockClear()
    getTransactionParams.mockClear()
    getTransactionParamsDo.mockClear()
  })

  it('builds a 0-ALGO self-payment carrying the signing message as note (Pera + Defly both sign this)', async () => {
    await buildAuthPaymentTxn('WALLETADDR', 'sign this message')

    expect(makePaymentTxnWithSuggestedParamsFromObject).toHaveBeenCalledTimes(1)
    const args = makePaymentTxnWithSuggestedParamsFromObject.mock.calls[0]![0] as {
      sender: string
      receiver: string
      amount: number
      note: Uint8Array
      suggestedParams: Record<string, unknown>
    }

    expect(args.sender).toBe('WALLETADDR')
    expect(args.receiver).toBe('WALLETADDR')
    expect(args.amount).toBe(0)
    expect(new TextDecoder().decode(args.note)).toBe('sign this message')

    // Fee is force-zeroed/flat — the txn is never broadcast, only signed.
    expect(args.suggestedParams.fee).toBe(0)
    expect(args.suggestedParams.flatFee).toBe(true)
    // Everything else from algod's suggested params passes through untouched.
    expect(args.suggestedParams.firstValid).toBe(100)
    expect(args.suggestedParams.lastValid).toBe(1100)
    expect(args.suggestedParams.genesisID).toBe('testnet-v1.0')
  })

  it('points the algod client at config.algodApiUrl when it is an absolute URL', async () => {
    const algod = await getAlgodClient()
    expect((algod as unknown as FakeAlgodv2).server).toBe('https://algod.example.test')
    expect((algod as unknown as FakeAlgodv2).token).toBe('')
  })
})

describe('bytesToBase64', () => {
  it('round-trips arbitrary bytes through base64', () => {
    const bytes = new Uint8Array([0, 1, 2, 253, 254, 255])
    const b64 = bytesToBase64(bytes)
    const decoded = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
    expect(Array.from(decoded)).toEqual(Array.from(bytes))
  })
})
