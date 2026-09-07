<script lang="ts">
  /**
   * Human-facing "add my endpoint" form for POST /api/v1/x402/list.
   *
   * Everything on this page works without spending anything EXCEPT the
   * final payment: field entry, client-side validation mirroring the real
   * backend contract, a live JSON preview of exactly what would be sent, a
   * free "is this URL already listed" check, and the real price/payTo/asset
   * facts read from the live catalog.
   *
   * The pay-and-submit step below builds a REAL Algorand mainnet payment:
   * connect a wallet (same Pera/WalletConnect client the login flow uses,
   * `lib/auth/pera.ts`'s `getPera()`), read the live 402 offer, sign the
   * exact-AVM payment group with the connected wallet, and resubmit with
   * `PAYMENT-SIGNATURE`. The actual challenge/build/sign/resettle logic
   * lives in `lib/x402/pay.ts` (`payWithWallet`), shared with any future
   * paid-write flow in this marketplace rather than re-implemented here
   * (CLAUDE.md §3). See that module's own docstring for the exact wire
   * protocol and which parts are mainnet-proven (the Python reference
   * client, `x402-client/pxke_x402/`) versus new browser code that has not
   * yet been exercised against a live wallet -- see this codebase's task
   * notes / commit history for the current verification status.
   */
  import { messages, t } from '../../lib/i18n'
  import { config, explorerTxUrl } from '../../lib/config'
  import { ApiException } from '../../lib/api/client'
  import { LatestOnly } from '../../lib/asyncGuard'
  import { getAlgodClient } from '../../lib/auth/arc0025'
  import { getPera, peraConnect, peraDisconnect, peraWakeTransportBurst } from '../../lib/auth/pera'
  import {
    payWithWallet,
    X402PaymentError,
    type PayStep,
    type WalletSignerTransaction,
  } from '../../lib/x402/pay'
  import {
    x402Api,
    X402_PATHS,
    X402_CATEGORIES,
    X402_LISTING_URL_MIN_LENGTH,
    X402_LISTING_URL_MAX_LENGTH,
    X402_LISTING_PRICE_MAX_LENGTH,
    X402_LISTING_DESCRIPTION_MAX_LENGTH,
    X402_LISTING_ASSETS_MAX_ITEMS,
    X402_LISTING_ASSET_MAX_LENGTH,
    X402_LISTING_TAGS_MAX_ITEMS,
    X402_LISTING_TAG_MAX_LENGTH,
    X402_LISTING_SCHEMA_MAX_BYTES,
    X402_LISTING_CONTACT_MAX_LENGTH,
    normalizeListingUrl,
    validateListingTags,
    normalizeListingAssets,
    jsonByteLength,
    type X402Catalog,
    type X402CatalogRoute,
    type X402ListingDetail,
  } from '../../lib/api/x402'
  import { routePriceText } from '../../lib/x402/catalog'

  let catalog = $state<X402Catalog | null>(null)
  let catalogFailed = $state(false)

  // One-time fetch, same AbortController-per-effect pattern as X402.svelte's
  // own tab-independent catalog load.
  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        const doc = await x402Api.catalog({ signal: ac.signal })
        if (ac.signal.aborted) return
        catalog = doc
      } catch {
        if (ac.signal.aborted) return
        catalogFailed = true
      }
    })()
    return () => ac.abort()
  })

  const listRoute = $derived<X402CatalogRoute | null>(
    catalog?.routes.find(
      (r) => r.method === 'POST' && r.path === X402_PATHS.list,
    ) ?? null,
  )

  // ── Form state ─────────────────────────────────────────────────────────
  let urlInput = $state('')
  let priceInput = $state('')
  let descriptionInput = $state('')
  let categoryInput = $state('other')
  let assetsInput = $state('')
  let tagsInput = $state('')
  let schemaInput = $state('')
  let reimbursesInput = $state(false)
  let contactInput = $state('')

  const urlTrimmed = $derived(urlInput.trim())
  const urlResult = $derived(urlTrimmed ? normalizeListingUrl(urlInput) : null)
  const urlError = $derived(urlResult && 'error' in urlResult ? urlResult.error : null)
  const normalizedUrl = $derived(urlResult && 'url' in urlResult ? urlResult.url : '')

  const priceTrimmed = $derived(priceInput.trim())
  const priceError = $derived(
    priceTrimmed.length > X402_LISTING_PRICE_MAX_LENGTH
      ? `price must be at most ${X402_LISTING_PRICE_MAX_LENGTH} characters`
      : null,
  )

  const descriptionError = $derived(
    descriptionInput.length > X402_LISTING_DESCRIPTION_MAX_LENGTH
      ? `description must be at most ${X402_LISTING_DESCRIPTION_MAX_LENGTH} characters`
      : null,
  )

  const rawAssets = $derived(
    assetsInput
      .split(',')
      .map((a) => a.trim())
      .filter(Boolean),
  )
  const assetsCountError = $derived(
    rawAssets.length > X402_LISTING_ASSETS_MAX_ITEMS
      ? `assets: at most ${X402_LISTING_ASSETS_MAX_ITEMS} allowed`
      : null,
  )
  const assetsLengthError = $derived(
    rawAssets.some((a) => a.length > X402_LISTING_ASSET_MAX_LENGTH)
      ? `each asset must be at most ${X402_LISTING_ASSET_MAX_LENGTH} characters`
      : null,
  )
  const normalizedAssets = $derived(normalizeListingAssets(rawAssets))

  const rawTags = $derived(
    tagsInput
      .split(',')
      .map((t2) => t2.trim())
      .filter(Boolean),
  )
  const tagsCountError = $derived(
    rawTags.length > X402_LISTING_TAGS_MAX_ITEMS
      ? `tags: at most ${X402_LISTING_TAGS_MAX_ITEMS} allowed`
      : null,
  )
  const tagsLengthError = $derived(
    rawTags.some((t2) => t2.length > X402_LISTING_TAG_MAX_LENGTH)
      ? `each tag must be at most ${X402_LISTING_TAG_MAX_LENGTH} characters`
      : null,
  )
  const tagsResult = $derived(validateListingTags(rawTags))
  const tagsReservedError = $derived('error' in tagsResult ? tagsResult.error : null)
  const normalizedTags = $derived('tags' in tagsResult ? tagsResult.tags : [])

  type SchemaResult = { schema: Record<string, unknown> | null; error: string | null }

  const schemaTrimmed = $derived(schemaInput.trim())
  const schemaResult = $derived.by((): SchemaResult => {
    if (!schemaTrimmed) return { schema: null, error: null }
    let parsed: unknown
    try {
      parsed = JSON.parse(schemaTrimmed)
    } catch {
      return { schema: null, error: 'schema must be valid JSON' }
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return { schema: null, error: 'schema must be a JSON object' }
    }
    const bytes = jsonByteLength(parsed)
    if (bytes > X402_LISTING_SCHEMA_MAX_BYTES) {
      return {
        schema: null,
        error: `schema must serialize to at most ${X402_LISTING_SCHEMA_MAX_BYTES} bytes (currently ${bytes})`,
      }
    }
    return { schema: parsed as Record<string, unknown>, error: null }
  })

  const contactTrimmed = $derived(contactInput.trim())
  const contactError = $derived(
    contactTrimmed.length > X402_LISTING_CONTACT_MAX_LENGTH
      ? `contact must be at most ${X402_LISTING_CONTACT_MAX_LENGTH} characters`
      : null,
  )

  const requiredErrors = $derived.by(() => {
    const list: string[] = []
    if (!urlTrimmed) list.push('An endpoint URL is required.')
    if (!priceTrimmed) list.push("Your endpoint's own price is required.")
    return list
  })

  const blockingErrors = $derived(
    [
      ...requiredErrors,
      urlError,
      priceError,
      descriptionError,
      assetsCountError,
      assetsLengthError,
      tagsCountError,
      tagsLengthError,
      tagsReservedError,
      schemaResult.error,
      contactError,
    ].filter((e): e is string => Boolean(e)),
  )

  const isValid = $derived(blockingErrors.length === 0 && Boolean(normalizedUrl) && Boolean(priceTrimmed))

  const requestBody = $derived.by(() => {
    const body: Record<string, unknown> = {
      url: normalizedUrl || urlTrimmed,
      price: priceInput,
      description: descriptionInput,
      assets: normalizedAssets,
      tags: normalizedTags,
      category: categoryInput || 'other',
      reimburses: reimbursesInput,
      contact: contactTrimmed,
    }
    if (schemaResult.schema) body.schema = schemaResult.schema
    return body
  })
  const requestBodyJson = $derived(JSON.stringify(requestBody, null, 2))

  const apiBase = $derived(config.apiBaseUrl.replace(/\/$/, '') || window.location.origin)
  const listUrl = $derived(`${apiBase}${X402_PATHS.list}`)
  const curlChallenge = $derived(`curl -si -X POST "${listUrl}"`)
  const curlSubmit = $derived(
    `curl -si -X POST "${listUrl}" \\\n` +
      `  -H "PAYMENT-SIGNATURE: <your signed payment>" \\\n` +
      `  -H "Content-Type: application/json" \\\n` +
      `  -d '${requestBodyJson.replace(/'/g, `'\\''`)}'`,
  )

  // ── Free "already listed?" check ──────────────────────────────────────
  const checkGuard = new LatestOnly()
  let checkState = $state<'idle' | 'loading' | 'not_listed' | 'listed' | 'error'>('idle')
  let checkResult = $state<X402ListingDetail | null>(null)
  let checkErrorMsg = $state<string | null>(null)

  $effect(() => () => checkGuard.cancel())

  async function checkExisting() {
    if (!normalizedUrl) return
    const { signal, stale } = checkGuard.next()
    checkState = 'loading'
    checkErrorMsg = null
    try {
      const found = await x402Api.listingDetail(normalizedUrl, { signal })
      if (stale()) return
      checkResult = found
      checkState = found ? 'listed' : 'not_listed'
    } catch (e) {
      if (stale()) return
      checkErrorMsg = e instanceof ApiException ? e.userMessage : null
      checkState = 'error'
    }
  }

  function shortAddr(a: string): string {
    return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
  }

  // ── Copy-to-clipboard (ShareBar.svelte's own pattern) ─────────────────
  let copiedBody = $state(false)
  let copiedCurl = $state(false)

  async function copyBody() {
    try {
      await navigator.clipboard.writeText(requestBodyJson)
      copiedBody = true
      window.setTimeout(() => (copiedBody = false), 1800)
    } catch {
      /* ignore */
    }
  }

  async function copyCurl() {
    try {
      await navigator.clipboard.writeText(curlSubmit)
      copiedCurl = true
      window.setTimeout(() => (copiedCurl = false), 1800)
    } catch {
      /* ignore */
    }
  }

  // ── Wallet connect + pay & submit (real mainnet payment) ────────────────
  // Same Pera/WalletConnect client the login flow uses (lib/auth/pera.ts's
  // getPera()); the actual challenge/build/sign/resettle sequence lives in
  // lib/x402/pay.ts's payWithWallet, shared with any future paid-write flow.
  let walletAddress = $state<string | null>(null)
  let walletConnecting = $state(false)
  let walletError = $state<string | null>(null)

  // Guards against a stale async continuation writing state after the
  // component is torn down, or after a newer pay attempt has superseded
  // this one -- payWithWallet has no single AbortSignal-friendly moment
  // (a wallet approval can't be cancelled once shown), so this is a
  // generation counter rather than a single AbortController (CLAUDE.md §5:
  // every async flow needs an out-of-order/unmount guard).
  let destroyed = false
  let payAttempt = 0
  $effect(() => () => {
    destroyed = true
  })

  type PayUiState =
    | { kind: 'idle' }
    | { kind: PayStep }
    | { kind: 'success'; settlementTxId: string; termDays: number }
    | { kind: 'error'; message: string; settled: boolean; settlementTxId: string | null }

  let payState = $state<PayUiState>({ kind: 'idle' })

  async function connectWallet(): Promise<string | null> {
    walletError = null
    walletConnecting = true
    try {
      const addr = await peraConnect()
      if (destroyed) return null
      walletAddress = addr
      return addr
    } catch (e) {
      if (destroyed) return null
      walletError = e instanceof Error ? e.message : 'Could not connect wallet'
      return null
    } finally {
      if (!destroyed) walletConnecting = false
    }
  }

  async function disconnectWallet() {
    await peraDisconnect()
    if (destroyed) return
    walletAddress = null
    payState = { kind: 'idle' }
  }

  /** Wraps Pera's raw signTransaction as the WalletSigner shape payWithWallet expects. */
  async function signWithPera(group: WalletSignerTransaction[]): Promise<(Uint8Array | null)[]> {
    const pera = await getPera()
    peraWakeTransportBurst()
    return pera.signTransaction([group.map(({ txn, signers }) => ({ txn, signers }))])
  }

  async function payAndSubmit() {
    if (!isValid) return
    const attempt = ++payAttempt
    walletError = null

    let addr = walletAddress
    if (!addr) {
      addr = await connectWallet()
      if (!addr || destroyed || attempt !== payAttempt) return
    }

    payState = { kind: 'challenging' }
    try {
      const algod = await getAlgodClient()
      const result = await payWithWallet({
        url: listUrl,
        body: requestBody,
        payerAddress: addr,
        algod,
        signGroup: signWithPera,
        onStep: (step) => {
          if (destroyed || attempt !== payAttempt) return
          payState = { kind: step }
        },
      })
      if (destroyed || attempt !== payAttempt) return
      const listing = result.body as { term_days?: number }
      payState = {
        kind: 'success',
        settlementTxId: result.settlementTxId,
        termDays: typeof listing.term_days === 'number' ? listing.term_days : 0,
      }
      // Refresh the free "already listed?" panel so it reflects the new row.
      void checkExisting()
    } catch (e) {
      if (destroyed || attempt !== payAttempt) return
      if (e instanceof X402PaymentError) {
        payState = {
          kind: 'error',
          message: e.message,
          settled: e.settled,
          settlementTxId: e.settlementTxId,
        }
      } else {
        payState = {
          kind: 'error',
          message: e instanceof Error ? e.message : 'Payment failed for an unknown reason.',
          settled: false,
          settlementTxId: null,
        }
      }
    }
  }

  const payBusy = $derived(
    payState.kind !== 'idle' && payState.kind !== 'success' && payState.kind !== 'error',
  )

  function payStepLabel(kind: PayUiState['kind']): string {
    switch (kind) {
      case 'challenging':
        return t($messages, 'x402RegisterPayStepChallenging')
      case 'checking_balance':
        return t($messages, 'x402RegisterPayStepCheckingBalance')
      case 'building':
        return t($messages, 'x402RegisterPayStepBuilding')
      case 'awaiting_signature':
        return t($messages, 'x402RegisterPayStepAwaitingSignature')
      case 'submitting':
        return t($messages, 'x402RegisterPayStepSubmitting')
      default:
        return ''
    }
  }
</script>

<div class="register">
  <header class="reg-header">
    <p class="kicker">{t($messages, 'x402RegisterKicker')}</p>
    <h2>{t($messages, 'x402RegisterTitle')}</h2>
    <p class="lead muted">{t($messages, 'x402RegisterLead')}</p>
  </header>

  <section class="reg-section">
    <h3>{t($messages, 'x402RegisterStep1Heading')}</h3>
    <div class="grid">
      <label class="field wide">
        <span class="field-label">{t($messages, 'x402RegisterUrlLabel')}</span>
        <input
          type="url"
          bind:value={urlInput}
          placeholder={t($messages, 'x402RegisterUrlPlaceholder')}
          autocomplete="off"
          spellcheck="false"
          class:invalid={Boolean(urlTrimmed) && Boolean(urlError)}
        />
      </label>

      <label class="field">
        <span class="field-label">{t($messages, 'x402RegisterPriceLabel')}</span>
        <input
          type="text"
          bind:value={priceInput}
          placeholder={t($messages, 'x402RegisterPricePlaceholder')}
          autocomplete="off"
          class:invalid={Boolean(priceError)}
        />
        <span class="field-hint">{t($messages, 'x402RegisterPriceHint')}</span>
      </label>

      <label class="field">
        <span class="field-label">{t($messages, 'x402RegisterCategoryLabel')}</span>
        <select bind:value={categoryInput}>
          {#each X402_CATEGORIES as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
      </label>

      <label class="field wide">
        <span class="field-label">{t($messages, 'x402RegisterDescriptionLabel')}</span>
        <textarea
          rows="2"
          bind:value={descriptionInput}
          placeholder={t($messages, 'x402RegisterDescriptionPlaceholder')}
          class:invalid={Boolean(descriptionError)}
        ></textarea>
      </label>

      <label class="field">
        <span class="field-label">{t($messages, 'x402RegisterAssetsLabel')}</span>
        <input
          type="text"
          bind:value={assetsInput}
          placeholder={t($messages, 'x402RegisterAssetsPlaceholder')}
          autocomplete="off"
          class:invalid={Boolean(assetsCountError) || Boolean(assetsLengthError)}
        />
      </label>

      <label class="field">
        <span class="field-label">{t($messages, 'x402RegisterTagsLabel')}</span>
        <input
          type="text"
          bind:value={tagsInput}
          placeholder={t($messages, 'x402RegisterTagsPlaceholder')}
          autocomplete="off"
          class:invalid={Boolean(tagsCountError) || Boolean(tagsLengthError) || Boolean(tagsReservedError)}
        />
      </label>

      <label class="field wide">
        <span class="field-label">{t($messages, 'x402RegisterSchemaLabel')}</span>
        <textarea
          rows="3"
          class="mono"
          bind:value={schemaInput}
          placeholder={t($messages, 'x402RegisterSchemaPlaceholder')}
          class:invalid={Boolean(schemaResult.error)}
        ></textarea>
      </label>

      <label class="field checkbox wide">
        <input type="checkbox" bind:checked={reimbursesInput} />
        <span>{t($messages, 'x402RegisterReimbursesLabel')}</span>
      </label>

      <label class="field">
        <span class="field-label">{t($messages, 'x402RegisterContactLabel')}</span>
        <input
          type="text"
          bind:value={contactInput}
          placeholder={t($messages, 'x402RegisterContactPlaceholder')}
          autocomplete="off"
          class:invalid={Boolean(contactError)}
        />
      </label>
    </div>

    {#if blockingErrors.length > 0}
      <div class="errors">
        <p class="errors-heading">{t($messages, 'x402RegisterErrorsHeading')}</p>
        <ul>
          {#each blockingErrors as err (err)}
            <li>{err}</li>
          {/each}
        </ul>
      </div>
    {/if}
  </section>

  <section class="reg-section">
    <h3>{t($messages, 'x402RegisterStep2Heading')}</h3>

    <p class="preview-label">{t($messages, 'x402RegisterPreviewListingLabel')}</p>
    <div class="row-preview">
      <div class="row-head">
        <span class="name">{normalizedUrl || urlTrimmed || '—'}</span>
        <span class="price">{priceTrimmed || '—'}</span>
      </div>
      {#if descriptionInput}
        <p class="desc">{descriptionInput}</p>
      {/if}
      <p class="meta">
        <span class="badge">{categoryInput || 'other'}</span>
        {#each normalizedAssets as asset (asset)}
          <span class="badge">{asset}</span>
        {/each}
        {#each normalizedTags as tg (tg)}
          <span class="tagchip">#{tg}</span>
        {/each}
        {#if reimbursesInput}
          <span class="badge verified">reimburses</span>
        {/if}
      </p>
    </div>

    <p class="preview-label">{t($messages, 'x402RegisterPreviewBodyLabel')}</p>
    <pre class="code"><code>{requestBodyJson}</code></pre>

    <div class="check-block">
      <p class="preview-label">{t($messages, 'x402RegisterCheckHeading')}</p>
      <div class="check-row">
        <button
          type="button"
          class="btn"
          disabled={!normalizedUrl || checkState === 'loading'}
          onclick={() => void checkExisting()}
        >
          {checkState === 'loading' ? t($messages, 'x402RegisterCheckChecking') : t($messages, 'x402RegisterCheckButton')}
        </button>
        {#if !normalizedUrl}
          <span class="muted small">{t($messages, 'x402RegisterCheckNeedsUrl')}</span>
        {:else if checkState === 'not_listed'}
          <span class="check-result ok">{t($messages, 'x402RegisterCheckNotListed')}</span>
        {:else if checkState === 'listed' && checkResult}
          <span class="check-result" class:warn={Boolean(checkResult.listing.payer)}>
            {checkResult.listing.payer
              ? `${t($messages, 'x402RegisterCheckListedOther')} (${shortAddr(checkResult.listing.payer)})`
              : t($messages, 'x402RegisterCheckListedSelf')}
          </span>
        {:else if checkState === 'error'}
          <span class="check-result warn">{checkErrorMsg ?? t($messages, 'x402RegisterCheckError')}</span>
        {/if}
      </div>
    </div>
  </section>

  <section class="reg-section">
    <h3>{t($messages, 'x402RegisterStep3Heading')}</h3>

    {#if catalogFailed}
      <p class="muted">{t($messages, 'x402CatalogUnavailable')}</p>
    {:else if !catalog}
      <p class="muted">{t($messages, 'loading')}</p>
    {:else}
      <dl class="pay-facts">
        <div>
          <dt>{t($messages, 'x402RegisterPayFeeLabel')}</dt>
          <dd>{(listRoute && routePriceText(listRoute)) ?? '—'}</dd>
        </div>
        <div>
          <dt>{t($messages, 'x402RegisterPayNetworkLabel')}</dt>
          <dd>{catalog.network_name}</dd>
        </div>
        <div>
          <dt>{t($messages, 'x402RegisterPayAssetsLabel')}</dt>
          <dd>{(catalog.assets ?? []).map((a) => a.symbol).join(', ') || '—'}</dd>
        </div>
        <div class="wide">
          <dt>{t($messages, 'x402RegisterPayToLabel')}</dt>
          <dd class="mono-text">{catalog.pay_to}</dd>
        </div>
      </dl>
      {#if listRoute?.description}
        <p class="route-desc">{listRoute.description}</p>
      {/if}
    {/if}

    <ol class="pay-steps">
      <li>
        <p>{t($messages, 'x402RegisterPayStep1')}</p>
        <pre class="code"><code>{curlChallenge}</code></pre>
      </li>
      <li>
        <p>{t($messages, 'x402RegisterPayStep2')}</p>
      </li>
      <li>
        <p>{t($messages, 'x402RegisterPayStep3')}</p>
        <pre class="code"><code>{curlSubmit}</code></pre>
        <div class="copy-row">
          <button type="button" class="btn ghost" onclick={() => void copyBody()}>
            {copiedBody ? t($messages, 'x402RegisterCopied') : t($messages, 'x402RegisterCopyBody')}
          </button>
          <button type="button" class="btn ghost" onclick={() => void copyCurl()}>
            {copiedCurl ? t($messages, 'x402RegisterCopied') : t($messages, 'x402RegisterCopyCurl')}
          </button>
        </div>
      </li>
    </ol>

    <div class="submit-block">
      <p class="preview-label">{t($messages, 'x402RegisterWalletHeading')}</p>

      {#if payState.kind === 'success'}
        <div class="pay-result ok">
          <p class="pay-result-heading">{t($messages, 'x402RegisterPaySuccessHeading')}</p>
          <p>{t($messages, 'x402RegisterPaySuccessBody')}</p>
          {#if payState.settlementTxId}
            <p class="mono-text small">
              {payState.settlementTxId}
              <a href={explorerTxUrl(payState.settlementTxId)} target="_blank" rel="noopener">
                {t($messages, 'x402RegisterPaySuccessExplorer')}
              </a>
            </p>
          {/if}
        </div>
      {:else if payState.kind === 'error'}
        <div class="pay-result warn">
          <p class="pay-result-heading">
            {payState.settled
              ? t($messages, 'x402RegisterPayErrorSettledHeading')
              : t($messages, 'x402RegisterPayErrorHeading')}
          </p>
          <p>{payState.message}</p>
          {#if payState.settled && payState.settlementTxId}
            <p class="mono-text small">
              {payState.settlementTxId}
              <a href={explorerTxUrl(payState.settlementTxId)} target="_blank" rel="noopener">
                {t($messages, 'x402RegisterPaySuccessExplorer')}
              </a>
            </p>
          {/if}
        </div>
      {/if}

      {#if walletAddress && payState.kind !== 'success'}
        <p class="wallet-status muted small">
          {t($messages, 'x402RegisterWalletConnectedAs', { address: shortAddr(walletAddress) })}
          <button
            type="button"
            class="btn ghost tiny"
            disabled={payBusy}
            onclick={() => void disconnectWallet()}
          >
            {t($messages, 'x402RegisterWalletDisconnect')}
          </button>
        </p>
      {/if}

      {#if payState.kind !== 'success'}
        <button
          type="button"
          class="btn primary"
          disabled={!isValid || payBusy || walletConnecting}
          onclick={() => void payAndSubmit()}
        >
          {#if payBusy}
            {payStepLabel(payState.kind)}
          {:else if walletConnecting}
            {t($messages, 'x402RegisterWalletConnecting')}
          {:else if walletAddress}
            {t($messages, 'x402RegisterSubmitButton')}
          {:else}
            {t($messages, 'x402RegisterWalletConnectButton')}
          {/if}
        </button>
      {/if}

      {#if walletError}
        <p class="check-result warn small">{walletError}</p>
      {/if}
      <p class="submit-note muted small">{t($messages, 'x402RegisterSubmitNote')}</p>
    </div>
  </section>
</div>

<style>
  .register {
    display: flex;
    flex-direction: column;
    gap: 28px;
    max-width: 62rem;
  }
  .reg-header h2 {
    margin: 4px 0 0;
    font-size: clamp(22px, 3vw, 26px);
  }
  .reg-header .lead {
    margin: 8px 0 0;
    max-width: 46rem;
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.55;
  }
  .reg-section {
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .reg-section h3 {
    margin: 0 0 12px;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px 16px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 5px;
    min-width: 0;
  }
  .field.wide {
    grid-column: 1 / -1;
  }
  .field-label {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .field-hint {
    font-family: var(--font-serif);
    font-size: 0.78rem;
    color: var(--muted);
  }
  .field input[type='text'],
  .field input[type='url'],
  .field textarea,
  .field select {
    width: 100%;
    padding: 9px 10px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--surface);
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 13px;
  }
  .field textarea.mono {
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .field input:focus,
  .field textarea:focus,
  .field select:focus {
    outline: none;
    border-color: var(--accent);
  }
  .field input.invalid,
  .field textarea.invalid {
    border-color: var(--danger);
  }
  .field.checkbox {
    flex-direction: row;
    align-items: center;
    gap: 8px;
  }
  .field.checkbox span {
    font-family: var(--font-serif);
    font-size: 0.88rem;
  }
  .errors {
    margin-top: 16px;
    padding: 10px 12px;
    border: 1px solid var(--danger);
    border-radius: var(--radius-control);
  }
  .errors-heading {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--danger);
  }
  .errors ul {
    margin: 6px 0 0;
    padding-inline-start: 18px;
    font-family: var(--font-serif);
    font-size: 0.85rem;
    color: var(--on-surface);
  }
  .preview-label {
    margin: 14px 0 6px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .preview-label:first-of-type {
    margin-top: 0;
  }
  .row-preview {
    padding: 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--surface);
    min-width: 0;
  }
  .row-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 16px;
    min-width: 0;
  }
  .name {
    font-family: var(--font-display);
    font-size: 1rem;
    font-weight: 700;
    overflow-wrap: anywhere;
    min-width: 0;
  }
  .price {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    color: var(--accent);
  }
  .desc {
    margin: 6px 0 0;
    font-family: var(--font-serif);
    font-size: 0.9rem;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
    margin: 8px 0 0;
  }
  .badge,
  .tagchip {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
  }
  .badge.verified {
    color: var(--accent);
    border-color: var(--accent);
  }
  .tagchip {
    text-transform: none;
    color: var(--accent);
    border-color: transparent;
    padding: 2px 0;
  }
  .code {
    margin: 0;
    padding: 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--accent-soft);
    overflow-x: auto;
    white-space: pre-wrap;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--on-surface);
  }
  .check-block {
    margin-top: 16px;
  }
  .check-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .check-result {
    font-family: var(--font-serif);
    font-size: 0.85rem;
    color: var(--on-surface);
  }
  .check-result.ok {
    color: var(--accent);
  }
  .check-result.warn {
    color: var(--danger);
  }
  .small {
    font-size: 0.8rem;
  }
  .pay-facts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px 20px;
    margin: 0 0 12px;
  }
  .pay-facts .wide {
    grid-column: 1 / -1;
  }
  .pay-facts dt {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .pay-facts dd {
    margin: 2px 0 0;
    font-family: var(--font-mono);
    font-size: 13px;
    color: var(--on-surface);
    overflow-wrap: anywhere;
  }
  .mono-text {
    font-family: var(--font-mono);
  }
  .route-desc {
    margin: 0 0 14px;
    font-family: var(--font-serif);
    font-size: 0.85rem;
    line-height: 1.5;
    color: var(--muted);
  }
  .pay-steps {
    margin: 8px 0 0;
    padding-inline-start: 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .pay-steps p {
    margin: 0 0 6px;
    font-family: var(--font-serif);
    font-size: 0.9rem;
    line-height: 1.5;
  }
  .copy-row {
    display: flex;
    gap: 8px;
    margin-top: 8px;
  }
  .btn {
    height: 36px;
    padding: 0 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--surface);
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    cursor: pointer;
  }
  .btn:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }
  .btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .btn.ghost {
    background: transparent;
  }
  .btn.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--surface);
  }
  .btn.primary:disabled {
    background: var(--surface);
    color: var(--muted);
    border-color: var(--border);
  }
  .submit-block {
    margin-top: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: flex-start;
  }
  .submit-note {
    max-width: 44rem;
    font-family: var(--font-serif);
    line-height: 1.5;
  }
  .wallet-status {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .btn.ghost.tiny {
    height: 26px;
    padding: 0 10px;
    font-size: 10px;
  }
  .pay-result {
    width: 100%;
    max-width: 44rem;
    padding: 12px 14px;
    border: 1px solid var(--accent);
    border-radius: var(--radius-card);
    background: var(--accent-soft);
  }
  .pay-result.warn {
    border-color: var(--danger);
  }
  .pay-result-heading {
    margin: 0 0 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--accent);
  }
  .pay-result.warn .pay-result-heading {
    color: var(--danger);
  }
  .pay-result p {
    margin: 4px 0 0;
    font-family: var(--font-serif);
    font-size: 0.88rem;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }
  @media (max-width: 700px) {
    .grid {
      grid-template-columns: 1fr;
    }
    .pay-facts {
      grid-template-columns: 1fr;
    }
  }
</style>
