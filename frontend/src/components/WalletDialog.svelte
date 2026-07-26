<script lang="ts">
  import { onDestroy, onMount } from 'svelte'
  import { messages, t } from '../lib/i18n'
  import {
    authBusy,
    authError,
    cancelWalletSignIn,
    completeSignIn,
    signInWithWalletConnect,
    startChallenge,
    wakeWalletTransport,
    walletFlow,
  } from '../lib/auth/session'

  let { onclose }: { onclose: () => void } = $props()

  function isMobileWalletClient(): boolean {
    if (typeof navigator === 'undefined') return false
    return /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent)
  }

  function walletDeepLink(wcUri: string): string {
    const isIOS =
      typeof navigator !== 'undefined' &&
      (/iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1))
    if (isIOS) {
      return `perawallet-wc://wc?uri=${encodeURIComponent(wcUri)}`
    }
    return wcUri
  }

  let mode = $state<'wallet' | 'manual'>('wallet')
  let address = $state('')
  let nonce = $state('')
  let signingMessage = $state('')
  let signature = $state('')
  let step = $state<'address' | 'sign'>('address')
  let qrDataUrl = $state<string | null>(null)
  let showQr = $state(!isMobileWalletClient())
  let launchFailed = $state(false)
  let copied = $state(false)
  let started = $state(false)

  let hiddenAt: number | null = null

  $effect(() => {
    const uri = $walletFlow.uri
    if (!uri) {
      qrDataUrl = null
      return
    }
    let cancelled = false
    void import('qrcode').then((QRCode) =>
      QRCode.toDataURL(uri, {
        width: 280,
        margin: 2,
        color: { dark: '#111827', light: '#ffffff' },
      }).then((url) => {
        if (!cancelled) qrDataUrl = url
      }),
    )
    return () => {
      cancelled = true
    }
  })

  function onVisibility() {
    if (document.visibilityState === 'hidden') {
      hiddenAt = Date.now()
      return
    }
    if (document.visibilityState !== 'visible' || hiddenAt == null) return
    const elapsed = Date.now() - hiddenAt
    hiddenAt = null
    if (elapsed > 2000) wakeWalletTransport()
  }

  onMount(() => {
    document.addEventListener('visibilitychange', onVisibility)
    if (mode === 'wallet' && !started) {
      started = true
      void beginWallet()
    }
  })

  onDestroy(() => {
    document.removeEventListener('visibilitychange', onVisibility)
  })

  async function beginWallet() {
    try {
      await signInWithWalletConnect()
      onclose()
    } catch {
      /* error surfaced via walletFlow / authError */
    }
  }

  async function retryWallet() {
    started = true
    await beginWallet()
  }

  async function closeDialog() {
    await cancelWalletSignIn()
    onclose()
  }

  function openWallet(uri: string, sameTab: boolean) {
    launchFailed = false
    const link = walletDeepLink(uri)
    try {
      if (sameTab) {
        window.location.href = link
        return
      }
      const w = window.open(link, '_blank', 'noopener,noreferrer')
      if (!w) launchFailed = true
    } catch {
      launchFailed = true
    }
  }

  async function copyUri(uri: string) {
    try {
      await navigator.clipboard.writeText(uri)
      copied = true
      setTimeout(() => (copied = false), 2000)
    } catch {
      /* ignore */
    }
  }

  async function getChallenge() {
    const res = await startChallenge(address.trim())
    nonce = String(res.nonce ?? '')
    signingMessage = String(res.signing_message ?? '')
    step = 'sign'
  }

  async function finishManual() {
    await completeSignIn({
      walletAddress: address.trim(),
      nonce,
      signatureB64: signature.trim(),
      proofMethod: 'legacy_message',
    })
    onclose()
  }

  const phase = $derived($walletFlow.phase)
  const uri = $derived($walletFlow.uri)
  const pairedAddress = $derived($walletFlow.walletAddress)
  const flowError = $derived($walletFlow.error ?? $authError)
  const mobile = isMobileWalletClient()
</script>

<div class="backdrop" role="presentation" onclick={() => closeDialog()}>
  <div
    class="dialog panel"
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    aria-labelledby="wallet-title"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
  >
    <div class="title-row">
      <div class="title-icon" aria-hidden="true">
        {#if phase === 'error'}
          !
        {:else if phase === 'signing'}
          ✓
        {:else}
          ⌘
        {/if}
      </div>
      <h2 id="wallet-title">
        {#if mode === 'manual'}
          {t($messages, 'walletSignInTitle')}
        {:else if phase === 'error'}
          {t($messages, 'walletErrorTitle')}
        {:else if phase === 'signing'}
          {t($messages, 'walletAwaitingApprovalTitle')}
        {:else}
          {t($messages, 'walletDialogTitle')}
        {/if}
      </h2>
    </div>

    <div class="modes">
      <button
        class="mode"
        class:on={mode === 'wallet'}
        type="button"
        onclick={() => {
          mode = 'wallet'
          if (!started) {
            started = true
            void beginWallet()
          }
        }}>{t($messages, 'walletConnect')}</button
      >
      <button
        class="mode"
        class:on={mode === 'manual'}
        type="button"
        onclick={() => (mode = 'manual')}>Paste signature</button
      >
    </div>

    {#if mode === 'wallet'}
      {#if phase === 'error'}
        <p class="err">{flowError ?? t($messages, 'walletErrorGeneric')}</p>
        <div class="row">
          <button class="btn" type="button" onclick={() => closeDialog()}
            >{t($messages, 'walletCancel')}</button
          >
          <button class="btn btn-primary" type="button" disabled={$authBusy} onclick={() => retryWallet()}
            >{t($messages, 'walletRetry')}</button
          >
        </div>
      {:else if phase === 'signing'}
        <p class="hint muted">{t($messages, 'walletAwaitingApproval')}</p>
        <p class="hint muted">{t($messages, 'walletSignExplainer')}</p>
        {#if pairedAddress}
          <p class="addr mono">{pairedAddress}</p>
        {/if}
        <div class="row">
          <button class="btn" type="button" onclick={() => closeDialog()}
            >{t($messages, 'walletCancel')}</button
          >
          {#if uri}
            <button
              class="btn btn-primary"
              type="button"
              onclick={() => openWallet(uri.split('?')[0] ?? uri, true)}
              >{t($messages, 'walletOpenWallet')}</button
            >
          {/if}
        </div>
      {:else}
        <p class="hint muted">
          {mobile ? t($messages, 'walletMobileHint') : t($messages, 'walletDialogBody')}
        </p>

        {#if mobile}
          <button
            class="btn btn-primary open-main"
            type="button"
            disabled={!uri}
            onclick={() => uri && openWallet(uri, true)}>{t($messages, 'walletOpenWallet')}</button
          >
          {#if launchFailed}
            <p class="err">{t($messages, 'walletOpenFailed')}</p>
            <button class="btn" type="button" disabled={!uri} onclick={() => uri && copyUri(uri)}
              >{t($messages, 'walletCopyUri')}</button
            >
          {/if}
          <button class="linkish" type="button" onclick={() => (showQr = !showQr)}>
            {showQr ? 'Hide QR' : t($messages, 'walletShowQr')}
          </button>
        {/if}

        {#if showQr}
          <div class="qr-wrap">
            {#if qrDataUrl}
              <img class="qr" src={qrDataUrl} alt="WalletConnect QR code" width="280" height="280" />
            {:else}
              <div class="qr skeleton" aria-hidden="true"></div>
            {/if}
          </div>
        {/if}

        <div class="actions">
          <button class="btn" type="button" disabled={!uri} onclick={() => uri && copyUri(uri)}>
            {copied ? t($messages, 'walletUriCopied') : t($messages, 'walletCopyUri')}
          </button>
          {#if !mobile}
            <button
              class="btn btn-primary"
              type="button"
              disabled={!uri}
              onclick={() => uri && openWallet(uri, false)}>{t($messages, 'walletOpenWallet')}</button
            >
          {/if}
        </div>

        <div class="row">
          <button class="btn" type="button" onclick={() => closeDialog()}
            >{t($messages, 'walletCancel')}</button
          >
        </div>
      {/if}
    {:else if step === 'address'}
      <label class="field">
        <span>{t($messages, 'walletAddressLabel')}</span>
        <input bind:value={address} placeholder="ALGORAND…" autocomplete="off" />
      </label>
      <div class="row">
        <button class="btn" type="button" onclick={() => closeDialog()}
          >{t($messages, 'walletCancel')}</button
        >
        <button
          class="btn btn-primary"
          type="button"
          disabled={$authBusy || address.trim().length < 50}
          onclick={() => getChallenge()}>{t($messages, 'walletGetChallenge')}</button
        >
      </div>
    {:else}
      <p class="muted">{t($messages, 'walletSigningMessage')}</p>
      <pre class="msg">{signingMessage}</pre>
      <label class="field">
        <span>{t($messages, 'walletPasteSignature')}</span>
        <textarea rows="3" bind:value={signature}></textarea>
      </label>
      <div class="row">
        <button class="btn" type="button" onclick={() => (step = 'address')}
          >{t($messages, 'walletRetry')}</button
        >
        <button
          class="btn btn-primary"
          type="button"
          disabled={$authBusy || !signature.trim()}
          onclick={() => finishManual()}>{t($messages, 'walletComplete')}</button
        >
      </div>
    {/if}

    {#if $authError && mode === 'manual'}
      <p class="err">{$authError}</p>
    {/if}
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(0, 0, 0, 0.45);
    display: grid;
    place-items: center;
    padding: 1rem;
  }
  .dialog {
    width: min(420px, 100%);
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .title-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }
  .title-icon {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    display: grid;
    place-items: center;
    background: var(--accent-soft);
    color: var(--primary);
    font-weight: 700;
    flex-shrink: 0;
  }
  h2 {
    margin: 0;
    font-size: 1.05rem;
  }
  .modes {
    display: flex;
    gap: 6px;
  }
  .mode {
    flex: 1;
    border: 1px solid var(--border);
    background: var(--panel);
    border-radius: 10px;
    padding: 8px 10px;
    font-weight: 600;
    font-size: 13px;
  }
  .mode.on {
    border-color: var(--primary);
    background: var(--accent-soft);
    color: var(--primary);
  }
  .hint {
    margin: 0;
    font-size: 0.92rem;
  }
  .qr-wrap {
    display: grid;
    place-items: center;
    padding: 0.5rem 0;
  }
  .qr {
    width: min(280px, 100%);
    height: auto;
    border-radius: 12px;
    border: 1px solid var(--border);
    background: #fff;
  }
  .qr.skeleton {
    width: min(280px, 100%);
    aspect-ratio: 1;
    background: var(--callout);
    animation: pulse 1.2s ease-in-out infinite;
  }
  @keyframes pulse {
    50% {
      opacity: 0.55;
    }
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .open-main {
    width: 100%;
  }
  .linkish {
    border: 0;
    background: transparent;
    color: var(--primary);
    font-weight: 600;
    font-size: 0.9rem;
    padding: 0;
    text-align: left;
    cursor: pointer;
  }
  .addr {
    margin: 0;
    font-size: 0.75rem;
    word-break: break-all;
    background: var(--callout);
    padding: 0.5rem 0.65rem;
    border-radius: 8px;
  }
  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }
  .row {
    display: flex;
    gap: 0.5rem;
    justify-content: flex-end;
  }
  .msg {
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--callout);
    padding: 0.75rem;
    border-radius: 8px;
    font-size: 0.8rem;
    max-height: 160px;
    overflow: auto;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
</style>
