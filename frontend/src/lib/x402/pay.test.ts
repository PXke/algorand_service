import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import algosdk from 'algosdk'
import {
  buildPaymentGroup,
  decodePaymentRequiredHeader,
  encodePaymentPayload,
  fetchAssetHolding,
  payWithWallet,
  selectRequirement,
  X402PaymentError,
  type AlgodLike,
  type X402PaymentRequired,
  type X402PaymentRequirement,
  type WalletSignerTransaction,
} from './pay'

// Real (locally generated, unfunded) addresses -- only the checksum needs to
// be valid for algosdk's transaction builders to accept them; no network
// call is made anywhere in this suite (no-network guard, CLAUDE.md §6).
const PAYER = algosdk.generateAccount().addr.toString()
const PAY_TO = algosdk.generateAccount().addr.toString()
const FEE_PAYER = algosdk.generateAccount().addr.toString()

function b64Utf8(obj: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(obj))
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

const REQUIREMENT: X402PaymentRequirement = {
  scheme: 'exact',
  network: 'algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73ktiC1qzkkit8=',
  asset: '31566704',
  amount: '20000',
  payTo: PAY_TO,
  maxTimeoutSeconds: 60,
  extra: { decimals: 6 },
}

const REQUIRED: X402PaymentRequired = {
  x402Version: 2,
  error: null,
  resource: { url: 'https://algorand-api.pxke.me/api/v1/x402/list', description: 'list', mimeType: 'application/json' },
  accepts: [REQUIREMENT],
  extensions: null,
}

const SUGGESTED_PARAMS = {
  fee: 0n,
  minFee: 1000n,
  flatFee: false,
  firstValid: 1000n,
  lastValid: 2000n,
  genesisID: 'mainnet-v1.0',
  genesisHash: new Uint8Array(32).fill(7),
}

describe('decodePaymentRequiredHeader', () => {
  it('round-trips a well-formed v2 offer', () => {
    const header = b64Utf8(REQUIRED)
    const decoded = decodePaymentRequiredHeader(header)
    expect(decoded.accepts).toHaveLength(1)
    expect(decoded.accepts[0]?.payTo).toBe(PAY_TO)
    expect(decoded.resource?.url).toBe(REQUIRED.resource?.url)
  })

  it('throws X402PaymentError on invalid base64/JSON', () => {
    expect(() => decodePaymentRequiredHeader('not-base64!!')).toThrow(X402PaymentError)
  })

  it('throws X402PaymentError when accepts[] is missing', () => {
    const header = b64Utf8({ x402Version: 2 })
    expect(() => decodePaymentRequiredHeader(header)).toThrow(X402PaymentError)
  })
})

describe('selectRequirement', () => {
  it('picks the first offered requirement (USDC-first ordering is a server contract)', () => {
    const other: X402PaymentRequirement = { ...REQUIREMENT, asset: '999' }
    expect(selectRequirement([REQUIREMENT, other])).toBe(REQUIREMENT)
  })

  it('throws when accepts[] is empty', () => {
    expect(() => selectRequirement([])).toThrow(X402PaymentError)
  })
})

describe('buildPaymentGroup', () => {
  it('builds a single fee-paying ASA transfer when there is no feePayer', () => {
    const { transactions, paymentIndex } = buildPaymentGroup(
      algosdk,
      REQUIREMENT,
      PAYER,
      SUGGESTED_PARAMS,
    )
    expect(transactions).toHaveLength(1)
    expect(paymentIndex).toBe(0)
    const txn = transactions[0]!
    expect(txn.type).toBe('axfer')
    expect(txn.sender.toString()).toBe(PAYER)
    expect(txn.assetTransfer?.receiver.toString()).toBe(PAY_TO)
    expect(txn.assetTransfer?.amount).toBe(20000n)
    expect(txn.assetTransfer?.assetIndex).toBe(31566704n)
  })

  it('builds a 2-txn gasless group when the offer carries extra.feePayer', () => {
    const requirement: X402PaymentRequirement = {
      ...REQUIREMENT,
      extra: { decimals: 6, feePayer: FEE_PAYER },
    }
    const { transactions, paymentIndex } = buildPaymentGroup(
      algosdk,
      requirement,
      PAYER,
      SUGGESTED_PARAMS,
    )
    expect(transactions).toHaveLength(2)
    expect(paymentIndex).toBe(1)

    const feePayerTxn = transactions[0]!
    expect(feePayerTxn.type).toBe('pay')
    expect(feePayerTxn.sender.toString()).toBe(FEE_PAYER)
    expect(feePayerTxn.payment?.receiver.toString()).toBe(FEE_PAYER)
    expect(feePayerTxn.payment?.amount).toBe(0n)
    expect(feePayerTxn.fee).toBe(2000n) // pooled: minFee(1000) * 2 txns

    const paymentTxn = transactions[1]!
    expect(paymentTxn.type).toBe('axfer')
    expect(paymentTxn.sender.toString()).toBe(PAYER)
    expect(paymentTxn.fee).toBe(0n)

    // Both carry the same assigned group id.
    expect(feePayerTxn.group).toBeDefined()
    expect(paymentTxn.group).toBeDefined()
    expect(Array.from(feePayerTxn.group!)).toEqual(Array.from(paymentTxn.group!))
  })
})

describe('encodePaymentPayload', () => {
  it('wraps the signed group as a v2 PaymentPayload and base64-encodes it', () => {
    const header = encodePaymentPayload({
      paymentRequired: REQUIRED,
      accepted: REQUIREMENT,
      paymentGroup: ['AAA', 'BBB'],
      paymentIndex: 1,
    })
    const decoded = JSON.parse(atob(header)) as Record<string, unknown>
    expect(decoded.x402Version).toBe(2)
    expect(decoded.payload).toEqual({ paymentGroup: ['AAA', 'BBB'], paymentIndex: 1 })
    expect(decoded.accepted).toEqual(REQUIREMENT)
    expect(decoded.resource).toEqual(REQUIRED.resource)
  })

  it('omits resource/extensions when the offer carried none', () => {
    const header = encodePaymentPayload({
      paymentRequired: { ...REQUIRED, resource: null, extensions: null },
      accepted: REQUIREMENT,
      paymentGroup: ['AAA'],
      paymentIndex: 0,
    })
    const decoded = JSON.parse(atob(header)) as Record<string, unknown>
    expect(decoded.resource).toBeUndefined()
    expect(decoded.extensions).toBeUndefined()
  })
})

function fakeAlgod(assets: Array<{ assetId: bigint; amount: bigint }>): AlgodLike {
  return {
    getTransactionParams: () => ({ do: async () => SUGGESTED_PARAMS }),
    accountInformation: () => ({ do: async () => ({ assets }) }),
  }
}

describe('fetchAssetHolding', () => {
  it('returns the held amount when opted in', async () => {
    const algod = fakeAlgod([{ assetId: 31566704n, amount: 500000n }])
    await expect(fetchAssetHolding(algod, PAYER, 31566704)).resolves.toEqual({ amount: 500000n })
  })

  it('returns null when not opted into the asset', async () => {
    const algod = fakeAlgod([{ assetId: 999n, amount: 1n }])
    await expect(fetchAssetHolding(algod, PAYER, 31566704)).resolves.toBeNull()
  })
})

describe('payWithWallet', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function challengeResponse(): Response {
    return new Response('{}', {
      status: 402,
      headers: { 'PAYMENT-REQUIRED': b64Utf8(REQUIRED) },
    })
  }

  it('runs the full challenge -> sign -> resettle round-trip on success', async () => {
    fetchMock
      .mockResolvedValueOnce(challengeResponse())
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ listing: { url: 'x' }, settlement_tx_id: 'TX123', term_days: 30 }), {
          status: 200,
          headers: { 'PAYMENT-RESPONSE': 'irrelevant' },
        }),
      )

    const algod = fakeAlgod([{ assetId: 31566704n, amount: 1_000_000n }])
    const signGroup = vi.fn(async (group: WalletSignerTransaction[]) =>
      group.map((_, i) => (i === 0 ? new Uint8Array([1, 2, 3]) : null)),
    )
    const steps: string[] = []

    const result = await payWithWallet({
      url: 'https://algorand-api.pxke.me/api/v1/x402/list',
      body: { url: 'https://example.com', price: '$0.01' },
      payerAddress: PAYER,
      algod,
      signGroup,
      onStep: (s) => steps.push(s),
    })

    expect(result.settlementTxId).toBe('TX123')
    expect(result.body.settlement_tx_id).toBe('TX123')
    expect(steps).toEqual(['challenging', 'checking_balance', 'building', 'awaiting_signature', 'submitting'])
    expect(fetchMock).toHaveBeenCalledTimes(2)
    const secondCallHeaders = fetchMock.mock.calls[1]![1].headers as Record<string, string>
    expect(secondCallHeaders['PAYMENT-SIGNATURE']).toBeTruthy()
    expect(signGroup).toHaveBeenCalledTimes(1)
  })

  it('throws when the first response is not a 402', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: 'temporarily_disabled', message: 'nope' } }), {
        status: 503,
      }),
    )
    const algod = fakeAlgod([])
    await expect(
      payWithWallet({
        url: 'https://x/list',
        body: {},
        payerAddress: PAYER,
        algod,
        signGroup: vi.fn(),
      }),
    ).rejects.toMatchObject({ code: 'temporarily_disabled' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('throws when the 402 carries no PAYMENT-REQUIRED header', async () => {
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 402 }))
    const algod = fakeAlgod([])
    await expect(
      payWithWallet({ url: 'https://x/list', body: {}, payerAddress: PAYER, algod, signGroup: vi.fn() }),
    ).rejects.toThrow(/PAYMENT-REQUIRED/)
  })

  it('blocks before signing when the wallet is not opted into the offered asset', async () => {
    fetchMock.mockResolvedValueOnce(challengeResponse())
    const algod = fakeAlgod([]) // no holdings at all -> not opted in
    const signGroup = vi.fn()
    await expect(
      payWithWallet({ url: 'https://x/list', body: {}, payerAddress: PAYER, algod, signGroup }),
    ).rejects.toMatchObject({ code: 'not_opted_in' })
    expect(signGroup).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(1) // never reached the resettle POST
  })

  it('blocks before signing on insufficient balance', async () => {
    fetchMock.mockResolvedValueOnce(challengeResponse())
    const algod = fakeAlgod([{ assetId: 31566704n, amount: 1n }]) // far below the 20000 required
    const signGroup = vi.fn()
    await expect(
      payWithWallet({ url: 'https://x/list', body: {}, payerAddress: PAYER, algod, signGroup }),
    ).rejects.toMatchObject({ code: 'insufficient_balance' })
    expect(signGroup).not.toHaveBeenCalled()
  })

  it('fails open (does not block payment) when the balance read itself errors', async () => {
    fetchMock
      .mockResolvedValueOnce(challengeResponse())
      .mockResolvedValueOnce(new Response(JSON.stringify({ settlement_tx_id: 'TX9' }), { status: 200 }))
    const algod: AlgodLike = {
      getTransactionParams: () => ({ do: async () => SUGGESTED_PARAMS }),
      accountInformation: () => ({
        do: async () => {
          throw new Error('algod hiccup')
        },
      }),
    }
    const signGroup = vi.fn(async (group: WalletSignerTransaction[]) =>
      group.map(() => new Uint8Array([9])),
    )
    const result = await payWithWallet({
      url: 'https://x/list',
      body: {},
      payerAddress: PAYER,
      algod,
      signGroup,
    })
    expect(result.settlementTxId).toBe('TX9')
  })

  it('surfaces a wallet signing failure without submitting a second request', async () => {
    fetchMock.mockResolvedValueOnce(challengeResponse())
    const algod = fakeAlgod([{ assetId: 31566704n, amount: 1_000_000n }])
    const signGroup = vi.fn(async () => {
      throw new Error('user rejected')
    })
    await expect(
      payWithWallet({ url: 'https://x/list', body: {}, payerAddress: PAYER, algod, signGroup }),
    ).rejects.toMatchObject({ code: 'wallet_signing_failed' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('marks the error as settled when the resettle response carries PAYMENT-RESPONSE but a non-200 status', async () => {
    fetchMock
      .mockResolvedValueOnce(challengeResponse())
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: 'url_already_listed', message: 'owned by someone else' },
            settlement_tx_id: 'TX_SETTLED',
          }),
          { status: 409, headers: { 'PAYMENT-RESPONSE': 'irrelevant' } },
        ),
      )
    const algod = fakeAlgod([{ assetId: 31566704n, amount: 1_000_000n }])
    const signGroup = vi.fn(async (group: WalletSignerTransaction[]) =>
      group.map(() => new Uint8Array([9])),
    )
    const error = await payWithWallet({
      url: 'https://x/list',
      body: {},
      payerAddress: PAYER,
      algod,
      signGroup,
    }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(X402PaymentError)
    const err = error as X402PaymentError
    expect(err.settled).toBe(true)
    expect(err.settlementTxId).toBe('TX_SETTLED')
    expect(err.code).toBe('url_already_listed')
  })
})
