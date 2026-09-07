/**
 * Browser x402 "exact" AVM payment client -- the shared `payWithWallet`
 * helper docs/x402-marketplace-product-redesign.md §2.2 proposes, so every
 * future paid-write flow (board placement, grading, ...) reuses this rather
 * than a second hand-built payment group (CLAUDE.md §3, "no new copies of
 * existing logic"). First consumer: X402RegisterForm.svelte's "Pay & submit"
 * button.
 *
 * Mirrors, field-for-field, the mainnet-proven Python reference for this
 * exact backend (`x402-client/pxke_x402/client.py`'s `_paid_request` +
 * `signer.py`) and the installed `x402-avm==2.0.2` package's own
 * `ExactAvmScheme.create_payment_payload`
 * (`backend/.venv/.../x402/mechanisms/avm/exact/client.py`):
 *
 * 1. POST the request with no payment header -> 402. The offer is NOT in the
 *    response body (that stays `{}` for this route) -- it is base64 JSON in
 *    the `PAYMENT-REQUIRED` response header (`x402/http/x402_http_server_base
 *    .py:_create_http_response`; CORS exposes it,
 *    `backend/app/core/cors.py:DEFAULT_CORS_EXPOSE_HEADERS`).
 * 2. Pick the first offered `accepts[]` entry (USDC -- the backend orders
 *    multi-asset offers USDC-first specifically so the default "take the
 *    first" selector prefers it, `backend/app/modules/x402/client.py
 *    :build_payment_offer`).
 * 3. If the requirement carries `extra.feePayer` (gasless leg, injected
 *    server-side from the facilitator's `/supported` response), build a
 *    2-txn atomic group: [fee-payer self-payment (pooled fee, UNSIGNED)],
 *    [ASA transfer payer->payTo (fee 0, signed by the connected wallet)].
 *    Otherwise a single ASA transfer at the normal fee, signed by the
 *    wallet. This is exactly `ExactAvmScheme.create_payment_payload`'s
 *    branching in the Python package.
 * 4. Wrap the signed group as a v2 `PaymentPayload` (`x402Version: 2,
 *    payload: {paymentGroup, paymentIndex}, accepted, resource,
 *    extensions`), base64 the JSON, and resend the SAME request with that
 *    value in `PAYMENT-SIGNATURE` (`x402/http/utils.py
 *    :encode_payment_signature_header` -- base64 of `model_dump_json
 *    (by_alias=True)`, i.e. camelCase keys).
 *
 * Wallet-agnostic by design: `signGroup` is injected by the caller (see
 * `WalletSigner` below) rather than this module importing a wallet SDK
 * directly, so a test can fake it and a future caller can pass Defly/Lute's
 * own `signTransaction` the same shape without a second payment-building
 * copy. Today's only wired-up caller uses Pera's `getPera().signTransaction`
 * (`lib/auth/pera.ts`), the same WalletConnect client the login flow uses.
 */

import type { SuggestedParams, Transaction } from 'algosdk'
import { bytesToBase64 } from '../auth/pera'

// `(await import('algosdk')).default` is what every dynamic import site in
// this codebase actually uses at runtime (arc0025.ts, pera.ts) -- its type
// is the package's default export, NOT `typeof import('algosdk')` (the
// whole module namespace, which also carries the `default` property itself
// and is not what gets passed around here).
type AlgosdkModule = (typeof import('algosdk'))['default']

/**
 * PXke's own receive-only x402 payTo address (verified live 2026-09-07
 * against prod's configured X402_PAY_TO_ADDRESS). The default recipient
 * allowlist `payWithWallet` checks every 402 offer against BEFORE building
 * or asking the wallet to sign anything (2026-09-07 security review,
 * finding 3 -- the same fix applied to the Python reference client,
 * x402-client/pxke_x402/client.py's DEFAULT_EXPECTED_PAY_TO): a malicious
 * or compromised offer naming a different recipient must never even reach
 * the wallet's signing prompt, since not every wallet UI surfaces a
 * multi-leg atomic group's real recipient as clearly as a single simple
 * payment would. Pass `expectedPayTo: null` to `payWithWallet` to disable
 * (not recommended), or a different value if this address is ever rotated.
 */
export const DEFAULT_EXPECTED_PAY_TO = 'KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII'

/**
 * Conservative ceiling (atomic units, assuming a 6-decimal/USDC-class
 * asset) on any single payment `payWithWallet` will build -- every route on
 * this marketplace prices well under $0.25 as of this writing, so
 * $1.00-equivalent comfortably covers normal calls while still bounding a
 * single malicious/compromised offer's worst case. Mirrors the Python
 * client's identical DEFAULT_MAX_PAYMENT_ATOMIC. Pass `maxPaymentAtomic:
 * null` to disable (not recommended), or a higher value for a route you
 * know legitimately costs more.
 */
export const DEFAULT_MAX_PAYMENT_ATOMIC = 1_000_000n

export type X402PaymentRequirement = {
  scheme: string
  network: string
  asset: string
  amount: string
  payTo: string
  maxTimeoutSeconds: number
  extra?: Record<string, unknown>
}

export type X402ResourceInfo = {
  url: string
  description?: string | null
  mimeType?: string | null
}

export type X402PaymentRequired = {
  x402Version: number
  error: string | null
  resource: X402ResourceInfo | null
  accepts: X402PaymentRequirement[]
  extensions: Record<string, unknown> | null
}

/** One entry of the group handed to a wallet's `signTransaction`: sign when `signers` names the payer, leave untouched (fee-payer leg) when empty. */
export type WalletSignerTransaction = { txn: Transaction; signers: string[] }

/**
 * Shape every wallet adapter's raw txn-signing call already has (Pera's
 * `PeraWalletConnect.signTransaction`, and the ARC-0001 `signTxns` Defly/Lute
 * expose) -- one flat group in, one array out, same length and order, with a
 * signed transaction's own bytes at the positions this module actually asked
 * to be signed. What is returned at an UNsigned position is never read here
 * (this module always re-encodes the unsigned leg itself from the
 * `Transaction` it built), so it tolerates either `null`/`undefined` or the
 * wallet's own unsigned bytes at those positions.
 */
export type WalletSigner = (group: WalletSignerTransaction[]) => Promise<(Uint8Array | null | undefined)[]>

export class X402PaymentError extends Error {
  readonly code: string
  /** True once a `PAYMENT-RESPONSE` header came back -- the payment settled even though this call is throwing (see run_with_refund's "settles but is refused" contract). Never trust a UI that hides this: the payer's money moved. */
  readonly settled: boolean
  readonly settlementTxId: string | null
  readonly responseBody: Record<string, unknown> | null

  constructor(
    message: string,
    opts: {
      code?: string
      settled?: boolean
      settlementTxId?: string | null
      responseBody?: Record<string, unknown> | null
      cause?: unknown
    } = {},
  ) {
    super(message, opts.cause !== undefined ? { cause: opts.cause } : undefined)
    this.name = 'X402PaymentError'
    this.code = opts.code ?? 'x402_payment_error'
    this.settled = opts.settled ?? false
    this.settlementTxId = opts.settlementTxId ?? null
    this.responseBody = opts.responseBody ?? null
  }
}

export type PayStep =
  | 'challenging'
  | 'checking_balance'
  | 'building'
  | 'awaiting_signature'
  | 'submitting'

export type PayWithWalletParams = {
  /** Absolute URL of the paid route (e.g. `${apiBase}/api/v1/x402/list`). */
  url: string
  /** The exact JSON body to POST both before and after payment -- unread on the unpaid attempt (challenge_if_unpaid runs before body parsing) but sent identically both times, same as the Python reference client. */
  body: Record<string, unknown>
  payerAddress: string
  /** Built by the caller via `getAlgodClient()` (`lib/auth/arc0025.ts`) -- used for the pre-flight balance/opt-in read and to fetch suggested params. */
  algod: AlgodLike
  signGroup: WalletSigner
  signal?: AbortSignal
  onStep?: (step: PayStep) => void
  /** Recipient allowlist checked against the 402 offer before signing (see DEFAULT_EXPECTED_PAY_TO's own doc). Defaults to PXke's own payTo; pass `null` to disable (not recommended). */
  expectedPayTo?: string | string[] | null
  /** Ceiling (atomic units) checked against the offer's amount before signing (see DEFAULT_MAX_PAYMENT_ATOMIC's own doc). Defaults to DEFAULT_MAX_PAYMENT_ATOMIC; pass `null` to disable (not recommended). */
  maxPaymentAtomic?: bigint | null
}

export type PayWithWalletResult = {
  body: Record<string, unknown>
  settlementTxId: string
}

// ── base64 / bytes helpers ──────────────────────────────────────────────

/** The inverse of `bytesToBase64` (pera.ts) for a UTF-8 JSON string, matching x402's own `safe_base64_decode`. */
function base64ToUtf8(b64: string): string {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new TextDecoder().decode(bytes)
}

function utf8ToBase64(text: string): string {
  return bytesToBase64(new TextEncoder().encode(text))
}

function formatAtomic(amount: bigint, decimals: number): string {
  if (decimals <= 0) return amount.toString()
  const digits = amount.toString().padStart(decimals + 1, '0')
  const whole = digits.slice(0, -decimals)
  const frac = digits.slice(-decimals).replace(/0+$/, '')
  return frac ? `${whole}.${frac}` : whole
}

// ── 402 offer ────────────────────────────────────────────────────────────

/** Decode a `PAYMENT-REQUIRED` header value into the offer it carries. Throws X402PaymentError on anything that isn't a well-formed v2 offer. */
export function decodePaymentRequiredHeader(headerValue: string): X402PaymentRequired {
  let data: unknown
  try {
    data = JSON.parse(base64ToUtf8(headerValue))
  } catch (cause) {
    throw new X402PaymentError('Could not read the payment offer from the server.', { cause })
  }
  if (!data || typeof data !== 'object' || !Array.isArray((data as { accepts?: unknown }).accepts)) {
    throw new X402PaymentError('The payment offer from the server was malformed (no accepts[]).')
  }
  const d = data as Partial<X402PaymentRequired>
  return {
    x402Version: typeof d.x402Version === 'number' ? d.x402Version : 2,
    error: typeof d.error === 'string' ? d.error : null,
    resource: (d.resource as X402ResourceInfo | undefined) ?? null,
    accepts: d.accepts as X402PaymentRequirement[],
    extensions: (d.extensions as Record<string, unknown> | undefined) ?? null,
  }
}

/** Which offered asset to pay with. The backend orders `accepts[]` USDC-first specifically so "take the first" is the right default (see module docstring) -- mirrors the Python client's `default_payment_selector`. */
export function selectRequirement(accepts: X402PaymentRequirement[]): X402PaymentRequirement {
  const first = accepts[0]
  if (!first) throw new X402PaymentError('The server offered no way to pay for this request.')
  return first
}

/**
 * Refuse a 402 offer before anything is built or signed if it fails the
 * recipient allowlist or amount cap (see DEFAULT_EXPECTED_PAY_TO/
 * DEFAULT_MAX_PAYMENT_ATOMIC's own docs for why). `undefined` for either
 * param means "use the default"; `null` means "explicitly disabled."
 */
export function validateOffer(
  requirement: X402PaymentRequirement,
  expectedPayTo: string | string[] | null | undefined,
  maxPaymentAtomic: bigint | null | undefined,
): void {
  const allowed =
    expectedPayTo === undefined
      ? [DEFAULT_EXPECTED_PAY_TO]
      : expectedPayTo === null
        ? null
        : Array.isArray(expectedPayTo)
          ? expectedPayTo
          : [expectedPayTo]
  if (allowed && !allowed.includes(requirement.payTo)) {
    throw new X402PaymentError(
      `Refusing to pay: the offer's recipient (${requirement.payTo}) is not the expected one. ` +
        'This could be a malicious or compromised response attempting to redirect your payment.',
      { code: 'unexpected_pay_to' },
    )
  }

  const cap = maxPaymentAtomic === undefined ? DEFAULT_MAX_PAYMENT_ATOMIC : maxPaymentAtomic
  if (cap === null) return
  let amount: bigint
  try {
    amount = BigInt(requirement.amount)
  } catch (cause) {
    throw new X402PaymentError(
      `Refusing to pay: the offer's amount (${requirement.amount}) is not a valid integer.`,
      { code: 'invalid_amount', cause },
    )
  }
  if (amount > cap) {
    throw new X402PaymentError(
      `Refusing to pay: the offer's amount (${amount}) exceeds the expected cap (${cap}).`,
      { code: 'amount_exceeds_cap' },
    )
  }
}

// ── balance / opt-in pre-flight ─────────────────────────────────────────

/**
 * The one slice of `algosdk.Algodv2` this module needs -- a DI seam so a
 * test can fake it without a live algod, and so the caller builds exactly
 * one client (via the existing `getAlgodClient()`, `lib/auth/arc0025.ts`,
 * already used for the login flow's suggested-params read) rather than this
 * module constructing a second one (CLAUDE.md §3).
 */
export type AlgodLike = {
  getTransactionParams(): { do(): Promise<SuggestedParams> }
  accountInformation(
    address: string,
  ): { do(): Promise<{ assets?: Array<{ assetId: bigint | number; amount: bigint | number }> }> }
}

/**
 * Best-effort read of the payer's holding of one ASA. Returns `null` when
 * the wallet is not opted into the asset at all (no entry in `assets[]`),
 * the held amount otherwise. Throws only on a transport/decode failure --
 * the caller decides whether to fail open on that (see `payWithWallet`,
 * which does: one algod hiccup should never block a payment attempt, same
 * policy as every Redis-backed check, CLAUDE.md §2.9).
 */
export async function fetchAssetHolding(
  algod: AlgodLike,
  address: string,
  assetId: number,
): Promise<{ amount: bigint } | null> {
  const info = await algod.accountInformation(address).do()
  const holding = (info.assets ?? []).find((a) => Number(a.assetId) === assetId)
  if (!holding) return null
  return { amount: BigInt(holding.amount) }
}

// ── payment group ────────────────────────────────────────────────────────

/**
 * Build the atomic transaction group for one payment requirement, exactly
 * mirroring `ExactAvmScheme.create_payment_payload` (Python, see module
 * docstring): with `extra.feePayer`, an unsigned pooled-fee self-payment
 * from the fee payer plus a fee-0 ASA transfer from the connected wallet
 * (payment index 1); without it, a single normal-fee ASA transfer from the
 * connected wallet (payment index 0). Pure/sync given already-fetched
 * suggested params, so it is unit-testable without a live algod.
 */
export function buildPaymentGroup(
  algosdk: AlgosdkModule,
  requirement: X402PaymentRequirement,
  payerAddress: string,
  suggestedParams: SuggestedParams,
): { transactions: Transaction[]; paymentIndex: number } {
  const feePayer =
    typeof requirement.extra?.feePayer === 'string' && requirement.extra.feePayer
      ? (requirement.extra.feePayer as string)
      : null
  const assetIndex = Number(requirement.asset)
  const amount = BigInt(requirement.amount)
  const transactions: Transaction[] = []
  let paymentIndex: number

  if (feePayer) {
    const minFee =
      typeof suggestedParams.minFee === 'bigint'
        ? suggestedParams.minFee
        : BigInt(suggestedParams.minFee ?? 1000)
    const pooledFee = minFee * 2n

    transactions.push(
      algosdk.makePaymentTxnWithSuggestedParamsFromObject({
        sender: feePayer,
        receiver: feePayer,
        amount: 0,
        note: new TextEncoder().encode(`x402-fee-payer-${Date.now()}`),
        suggestedParams: { ...suggestedParams, fee: pooledFee, flatFee: true },
      }),
    )
    transactions.push(
      algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({
        sender: payerAddress,
        receiver: requirement.payTo,
        amount,
        assetIndex,
        note: new TextEncoder().encode(`x402-payment-${Date.now()}`),
        suggestedParams: { ...suggestedParams, fee: 0, flatFee: true },
      }),
    )
    paymentIndex = 1
    algosdk.assignGroupID(transactions)
  } else {
    transactions.push(
      algosdk.makeAssetTransferTxnWithSuggestedParamsFromObject({
        sender: payerAddress,
        receiver: requirement.payTo,
        amount,
        assetIndex,
        note: new TextEncoder().encode(`x402-payment-${Date.now()}`),
        suggestedParams,
      }),
    )
    paymentIndex = 0
  }

  return { transactions, paymentIndex }
}

/** Wrap a signed group as a v2 PaymentPayload and base64 it for the `PAYMENT-SIGNATURE` header -- `x402/http/utils.py:encode_payment_signature_header` byte-for-byte (camelCase keys via the package's `by_alias=True`). */
export function encodePaymentPayload(params: {
  paymentRequired: X402PaymentRequired
  accepted: X402PaymentRequirement
  paymentGroup: string[]
  paymentIndex: number
}): string {
  const payload: Record<string, unknown> = {
    x402Version: 2,
    payload: { paymentGroup: params.paymentGroup, paymentIndex: params.paymentIndex },
    accepted: params.accepted,
  }
  if (params.paymentRequired.resource) payload.resource = params.paymentRequired.resource
  if (params.paymentRequired.extensions) payload.extensions = params.paymentRequired.extensions
  return utf8ToBase64(JSON.stringify(payload))
}

// ── response helpers ─────────────────────────────────────────────────────

async function readJsonBody(res: Response): Promise<Record<string, unknown> | null> {
  try {
    const text = await res.text()
    if (!text) return null
    const parsed: unknown = JSON.parse(text)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

async function errorFromResponse(
  res: Response,
  fallbackMessage: string,
  opts: { settled?: boolean; body?: Record<string, unknown> | null } = {},
): Promise<X402PaymentError> {
  const body = opts.body !== undefined ? opts.body : await readJsonBody(res)
  const err = body && body.error && typeof body.error === 'object' ? (body.error as Record<string, unknown>) : null
  const message = err && typeof err.message === 'string' ? err.message : fallbackMessage
  const code = err && typeof err.code === 'string' ? err.code : `http_${res.status}`
  const settlementTxId = typeof body?.settlement_tx_id === 'string' ? body.settlement_tx_id : null
  return new X402PaymentError(message, {
    code,
    settled: Boolean(opts.settled),
    settlementTxId,
    responseBody: body,
  })
}

// ── orchestration ────────────────────────────────────────────────────────

/**
 * Run the full challenge -> sign -> resettle round-trip for one paid POST.
 * Never broadcasts anything itself -- the signed group travels inside
 * `PAYMENT-SIGNATURE` and the facilitator verifies + settles it
 * server-side, same as every other x402 client. Throws `X402PaymentError`
 * on any failure; check `.settled` before assuming no money moved (a
 * "settles but refused" response -- e.g. relisting a URL someone else
 * already owns -- still charges the payer, by design; see
 * `x402_directory/api/routes.py`'s own docstring).
 */
export async function payWithWallet(params: PayWithWalletParams): Promise<PayWithWalletResult> {
  const { url, body, payerAddress, algod, signGroup, signal, onStep, expectedPayTo, maxPaymentAtomic } = params

  onStep?.('challenging')
  const challengeRes = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (challengeRes.status !== 402) {
    throw await errorFromResponse(challengeRes, `Expected a payment offer, got HTTP ${challengeRes.status}.`)
  }
  const headerValue = challengeRes.headers.get('PAYMENT-REQUIRED')
  if (!headerValue) {
    throw new X402PaymentError(
      'The server did not include a PAYMENT-REQUIRED offer on its 402 response.',
    )
  }
  const paymentRequired = decodePaymentRequiredHeader(headerValue)
  const requirement = selectRequirement(paymentRequired.accepts)
  validateOffer(requirement, expectedPayTo, maxPaymentAtomic)

  onStep?.('checking_balance')
  const assetId = Number(requirement.asset)
  const holding = await fetchAssetHolding(algod, payerAddress, assetId).catch(
    // Fails OPEN: one algod hiccup on a diagnostic read must never block a
    // real payment attempt (same policy as every Redis-backed check,
    // CLAUDE.md §2.9) -- `undefined` here means "could not tell," not "ok".
    () => undefined,
  )
  if (holding === null) {
    throw new X402PaymentError(
      `Your connected wallet is not opted into asset ${assetId} yet. Opt in first (send yourself a ` +
        '0-amount transfer of it, e.g. from your wallet app), then try again -- nothing has been charged.',
      { code: 'not_opted_in' },
    )
  }
  if (holding && holding.amount < BigInt(requirement.amount)) {
    const decimals = typeof requirement.extra?.decimals === 'number' ? (requirement.extra.decimals as number) : 6
    throw new X402PaymentError(
      `Your wallet holds ${formatAtomic(holding.amount, decimals)} but this costs ` +
        `${formatAtomic(BigInt(requirement.amount), decimals)} of the same asset. Top up first -- ` +
        'nothing has been charged.',
      { code: 'insufficient_balance' },
    )
  }

  onStep?.('building')
  const algosdk = (await import('algosdk')).default
  const suggestedParams = await algod.getTransactionParams().do()
  const { transactions, paymentIndex } = buildPaymentGroup(algosdk, requirement, payerAddress, suggestedParams)

  onStep?.('awaiting_signature')
  const group: WalletSignerTransaction[] = transactions.map((txn, i) => ({
    txn,
    signers: i === paymentIndex ? [payerAddress] : [],
  }))
  let signed: (Uint8Array | null | undefined)[]
  try {
    signed = await signGroup(group)
  } catch (cause) {
    throw new X402PaymentError(
      cause instanceof Error && cause.message ? cause.message : 'Wallet did not sign the payment.',
      { code: 'wallet_signing_failed', cause },
    )
  }
  const signedPaymentBytes = signed[paymentIndex]
  if (!signedPaymentBytes || signedPaymentBytes.length === 0) {
    throw new X402PaymentError('Wallet returned no signature for the payment transaction.', {
      code: 'wallet_signing_failed',
    })
  }
  const paymentGroup = transactions.map((txn, i) =>
    i === paymentIndex ? bytesToBase64(signedPaymentBytes) : bytesToBase64(algosdk.encodeUnsignedTransaction(txn)),
  )

  const signatureHeader = encodePaymentPayload({
    paymentRequired,
    accepted: requirement,
    paymentGroup,
    paymentIndex,
  })

  onStep?.('submitting')
  const settleRes = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/json',
      'PAYMENT-SIGNATURE': signatureHeader,
    },
    body: JSON.stringify(body),
    signal,
  })
  const settled = settleRes.headers.has('PAYMENT-RESPONSE')
  const responseBody = await readJsonBody(settleRes)
  if (settleRes.status !== 200) {
    throw await errorFromResponse(settleRes, `Payment sent but the request failed (HTTP ${settleRes.status}).`, {
      settled,
      body: responseBody,
    })
  }
  const settlementTxId = typeof responseBody?.settlement_tx_id === 'string' ? responseBody.settlement_tx_id : ''
  return { body: responseBody ?? {}, settlementTxId }
}
