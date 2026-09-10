<script lang="ts">
  /**
   * "Try it from your wallet" on /scan: pay one scan from a connected Pera
   * wallet and read the report here. The challenge/build/sign/resettle
   * sequence is lib/x402/pay.ts's payWithWallet (shared, mainnet-proven);
   * this panel only owns the wallet connection and the result rendering.
   * The report is rendered as text in a <pre>, never as markup.
   */
  import { config, explorerTxUrl } from '../../lib/config'
  import { messages, t } from '../../lib/i18n'
  import { getAlgodClient } from '../../lib/auth/arc0025'
  import { getPera, peraConnect, peraDisconnect, peraWakeTransportBurst } from '../../lib/auth/pera'
  import {
    payWithWallet,
    X402PaymentError,
    type PayStep,
    type WalletSignerTransaction,
  } from '../../lib/x402/pay'
  import { X402_PATHS, X402_API_ORIGIN } from '../../lib/api/x402'
  import { x402Json, x402ShortAddr } from '../../lib/x402/format'

  let { price = null }: { price?: string | null } = $props()

  const scanUrl = $derived(`${config.apiBaseUrl.replace(/\/$/, '') || X402_API_ORIGIN}${X402_PATHS.scanUrl}`)

  let target = $state('')
  let walletAddress = $state<string | null>(null)
  let walletConnecting = $state(false)
  let walletError = $state<string | null>(null)

  // payWithWallet has no single abortable moment (a wallet prompt cannot be
  // cancelled once shown), so a generation counter plus an unmount flag
  // guard every continuation instead of one AbortController (CLAUDE.md §5).
  let destroyed = false
  let attemptSeq = 0
  $effect(() => () => {
    destroyed = true
  })

  type Report = { one_line_summary?: string; risk?: { verdict?: string; malicious?: boolean | null; score?: number } }
  type UiState =
    | { kind: 'idle' }
    | { kind: PayStep }
    | { kind: 'success'; settlementTxId: string; report: Report }
    | { kind: 'error'; message: string; settled: boolean; settlementTxId: string | null }

  let ui = $state<UiState>({ kind: 'idle' })
  const busy = $derived(!['idle', 'success', 'error'].includes(ui.kind))
  const validUrl = $derived(/^https?:\/\/\S+$/i.test(target.trim()))

  const STEP_KEYS: Record<PayStep, string> = {
    challenging: 'x402TryStepChallenging',
    checking_balance: 'x402TryStepBalance',
    building: 'x402TryStepBuilding',
    awaiting_signature: 'x402TryStepSign',
    submitting: 'x402TryStepSubmitting',
  }

  async function connect(): Promise<string | null> {
    walletError = null
    walletConnecting = true
    try {
      const addr = await peraConnect()
      if (destroyed) return null
      walletAddress = addr
      return addr
    } catch (e) {
      if (destroyed) return null
      walletError = e instanceof Error ? e.message : t($messages, 'errorGeneric')
      return null
    } finally {
      if (!destroyed) walletConnecting = false
    }
  }

  async function disconnect() {
    await peraDisconnect()
    if (destroyed) return
    walletAddress = null
    ui = { kind: 'idle' }
  }

  async function signWithPera(group: WalletSignerTransaction[]): Promise<(Uint8Array | null)[]> {
    const pera = await getPera()
    peraWakeTransportBurst()
    return pera.signTransaction([group.map(({ txn, signers }) => ({ txn, signers }))])
  }

  async function payAndScan() {
    if (!validUrl) {
      walletError = t($messages, 'x402TryInvalidUrl')
      return
    }
    const attempt = ++attemptSeq
    walletError = null
    let addr = walletAddress
    if (!addr) {
      addr = await connect()
      if (!addr || destroyed || attempt !== attemptSeq) return
    }
    ui = { kind: 'challenging' }
    try {
      const algod = await getAlgodClient()
      const result = await payWithWallet({
        url: scanUrl,
        body: { url: target.trim() },
        payerAddress: addr,
        algod,
        signGroup: signWithPera,
        onStep: (step) => {
          if (destroyed || attempt !== attemptSeq) return
          ui = { kind: step }
        },
      })
      if (destroyed || attempt !== attemptSeq) return
      ui = { kind: 'success', settlementTxId: result.settlementTxId, report: result.body as Report }
    } catch (e) {
      if (destroyed || attempt !== attemptSeq) return
      if (e instanceof X402PaymentError) {
        ui = { kind: 'error', message: e.message, settled: e.settled, settlementTxId: e.settlementTxId }
      } else {
        ui = {
          kind: 'error',
          message: e instanceof Error ? e.message : t($messages, 'errorGeneric'),
          settled: false,
          settlementTxId: null,
        }
      }
    }
  }
</script>

<section class="x402-block try" aria-labelledby="try-heading">
  <h2 id="try-heading">{t($messages, 'x402TryHeading')}</h2>
  <p>{t($messages, 'x402TryBody')}{#if price}{' '}{t($messages, 'x402TryPrice', { price })}{/if}</p>

  <form
    class="form"
    onsubmit={(e) => {
      e.preventDefault()
      void payAndScan()
    }}
  >
    <label class="field">
      <span>{t($messages, 'x402TryUrlLabel')}</span>
      <input
        type="url"
        bind:value={target}
        placeholder="https://example.com/download.zip"
        autocomplete="off"
        spellcheck="false"
        disabled={busy}
      />
    </label>
    <div class="actions">
      <button type="submit" class="pay" disabled={busy || walletConnecting || (!validUrl && !!walletAddress)}>
        {#if busy && ui.kind in STEP_KEYS}
          {t($messages, STEP_KEYS[ui.kind as PayStep])}
        {:else if walletConnecting}
          {t($messages, 'x402TryConnecting')}
        {:else if walletAddress}
          {t($messages, 'x402TryPay')}
        {:else}
          {t($messages, 'x402TryConnect')}
        {/if}
      </button>
      {#if walletAddress}
        <span class="connected">
          {t($messages, 'x402TryConnectedAs', { address: x402ShortAddr(walletAddress) })}
          <button type="button" class="link" disabled={busy} onclick={() => void disconnect()}>
            {t($messages, 'x402TryDisconnect')}
          </button>
        </span>
      {/if}
    </div>
  </form>

  {#if walletError}
    <p class="x402-err" role="alert">{walletError}</p>
  {/if}

  {#if ui.kind === 'success'}
    <div class="result" role="status">
      <p class="verdict">
        <strong>{t($messages, 'x402TryDone')}</strong>
        {#if ui.report.one_line_summary}<span>{ui.report.one_line_summary}</span>{/if}
      </p>
      {#if ui.settlementTxId}
        <p class="tx">
          <span>{ui.settlementTxId}</span>
          <a href={explorerTxUrl(ui.settlementTxId)} target="_blank" rel="noopener noreferrer">{t($messages, 'x402TryViewTx')}</a>
        </p>
      {/if}
      <details>
        <summary>{t($messages, 'x402ReturnsHeading')}</summary>
        <pre class="x402-curl report">{x402Json(ui.report)}</pre>
      </details>
    </div>
  {:else if ui.kind === 'error'}
    <div class="result error" role="alert">
      <p class="verdict">
        <strong>{ui.settled ? t($messages, 'x402TryFailedSettled') : t($messages, 'x402TryFailed')}</strong>
        <span>{ui.message}</span>
      </p>
      {#if ui.settled && ui.settlementTxId}
        <p class="tx">
          <span>{ui.settlementTxId}</span>
          <a href={explorerTxUrl(ui.settlementTxId)} target="_blank" rel="noopener noreferrer">{t($messages, 'x402TryViewTx')}</a>
        </p>
      {/if}
    </div>
  {/if}
</section>

<style>
  .form {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-width: 40rem;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 5px;
    font-size: 14px;
    color: var(--muted);
  }
  .field input {
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--panel);
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 13.5px;
  }
  .field input:focus {
    outline: none;
    border-color: var(--x402-accent);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 18px;
  }
  .pay {
    appearance: none;
    min-height: 44px;
    padding: 0 18px;
    border: 0;
    background: var(--x402-accent);
    color: #fff;
    font-family: var(--font-sans);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
  }
  .pay:disabled {
    opacity: 0.55;
    cursor: default;
  }
  .pay:focus-visible,
  .link:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .connected {
    font-size: 13px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .link {
    appearance: none;
    border: 0;
    background: none;
    padding: 0 0 0 8px;
    color: var(--accent);
    font-size: 13px;
    cursor: pointer;
  }
  .result {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 14px 16px;
    border-inline-start: 3px solid var(--x402-accent);
    background: var(--panel);
  }
  .result.error {
    border-inline-start-color: var(--danger);
  }
  .verdict {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    font-size: 15px;
    line-height: 1.5;
  }
  .tx {
    margin: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    font-family: var(--font-mono);
    font-size: 12.5px;
    overflow-wrap: anywhere;
  }
  .tx a {
    color: var(--accent);
    font-family: var(--font-sans);
  }
  details summary {
    cursor: pointer;
    font-size: 14px;
    color: var(--muted);
  }
  .report {
    margin-top: 8px;
    max-height: 24rem;
    overflow: auto;
    white-space: pre;
  }
</style>
