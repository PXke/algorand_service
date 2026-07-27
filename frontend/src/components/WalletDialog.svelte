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
  import {
    isMobileWalletClient,
    openWalletDeepLink,
    openWalletDeepLinkRobust,
    walletAppLaunchLink,
    walletDeepLink,
  } from '../lib/auth/walletconnect'
  import {
    enableWcDebugFromQuery,
    toggleWcDebug,
    wcDebug,
    wcDebugEnabled,
    wcDebugLog,
  } from '../lib/auth/wcDebug'

  let { onclose }: { onclose: () => void } = $props()

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
  let titlePressTimer: number | null = null
  let reviveTimer: number | null = null
  let lastReviveAt = 0

  let backdropEl: HTMLDivElement | undefined = $state()
  let hiddenAt: number | null = null
  let leftForWallet = false
  let prevOverflow = ''
  let prevPaddingRight = ''

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

  // Pairing: wake bridge when returning from Pera so the session approval
  // can arrive. Signing: soft visibility wake (request already on the wire).
  // Debounce: Firefox fires visibility + focus together (was double close+open).
  function reviveBridge(reason: string) {
    const now = Date.now()
    if (now - lastReviveAt < 900) {
      wcDebug(`revive debounced (${reason})`)
      return
    }
    if (reviveTimer != null) window.clearTimeout(reviveTimer)
    reviveTimer = window.setTimeout(() => {
      reviveTimer = null
      lastReviveAt = Date.now()
      wcDebug(`visibility revive (${reason})`)
      wakeWalletTransport()
    }, 80)
  }

  function onVisibility() {
    if (document.visibilityState === 'hidden') {
      hiddenAt = Date.now()
      leftForWallet = true
      wcDebug('visibility hidden')
      return
    }
    if (document.visibilityState !== 'visible' || hiddenAt == null) return
    const elapsed = Date.now() - hiddenAt
    hiddenAt = null
    // Ignore brief flickers from iframe / target=_blank launches (~100–400ms).
    // Real app switches are usually >500ms.
    if (elapsed < 500) {
      wcDebug(`visibility flicker ${elapsed}ms ignored`)
      return
    }
    if (!leftForWallet) return
    wcDebug(`visibility return after ${elapsed}ms`)
    reviveBridge('visibility')
  }

  function onPageShow(e: PageTransitionEvent) {
    if (e.persisted) reviveBridge('pageshow')
  }

  function onFocus() {
    // Firefox sometimes skips visibilitychange on return.
    if (leftForWallet && hiddenAt == null) {
      reviveBridge('focus')
    }
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') void closeDialog()
  }

  onMount(() => {
    enableWcDebugFromQuery()
    // Escape any transformed ancestors so `position: fixed` tracks the viewport
    // (long articles otherwise pin the modal mid-scroll).
    const node = backdropEl
    if (node && node.parentElement !== document.body) {
      document.body.appendChild(node)
    }
    const sb = window.innerWidth - document.documentElement.clientWidth
    prevOverflow = document.body.style.overflow
    prevPaddingRight = document.body.style.paddingRight
    document.body.style.overflow = 'hidden'
    if (sb > 0) document.body.style.paddingRight = `${sb}px`
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pageshow', onPageShow)
    window.addEventListener('focus', onFocus)
    document.addEventListener('keydown', onKeydown)
    if (mode === 'wallet' && !started) {
      started = true
      void beginWallet()
    }
    return () => {
      document.body.style.overflow = prevOverflow
      document.body.style.paddingRight = prevPaddingRight
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pageshow', onPageShow)
      window.removeEventListener('focus', onFocus)
      document.removeEventListener('keydown', onKeydown)
      node?.remove()
    }
  })

  onDestroy(() => {
    document.removeEventListener('visibilitychange', onVisibility)
    window.removeEventListener('pageshow', onPageShow)
    window.removeEventListener('focus', onFocus)
    document.removeEventListener('keydown', onKeydown)
    if (titlePressTimer != null) window.clearTimeout(titlePressTimer)
    if (reviveTimer != null) window.clearTimeout(reviveTimer)
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

  function openWallet(uri: string) {
    launchFailed = false
    leftForWallet = true
    wcDebug(`user Open wallet → ${walletDeepLink(uri).slice(0, 96)}…`)
    const ok = openWalletDeepLinkRobust(uri)
    if (!ok) launchFailed = true
  }

  function reopenWalletApp() {
    launchFailed = false
    leftForWallet = true
    const ok = openWalletDeepLink(walletAppLaunchLink())
    if (!ok) launchFailed = true
  }

  async function copyUri(uri: string) {
    try {
      await navigator.clipboard.writeText(uri)
      copied = true
      setTimeout(() => (copied = false), 2000)
      wcDebug('uri copied')
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = uri
        ta.setAttribute('readonly', '')
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        ta.remove()
        copied = true
        setTimeout(() => (copied = false), 2000)
        wcDebug('uri copied (fallback)')
      } catch {
        /* ignore */
      }
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

  function onTitlePointerDown() {
    titlePressTimer = window.setTimeout(() => {
      toggleWcDebug()
      wcDebug('debug toggled via long-press')
    }, 900)
  }

  function onTitlePointerUp() {
    if (titlePressTimer != null) {
      window.clearTimeout(titlePressTimer)
      titlePressTimer = null
    }
  }

  const phase = $derived($walletFlow.phase)
  const uri = $derived($walletFlow.uri)
  const pairedAddress = $derived($walletFlow.walletAddress)
  const flowError = $derived($walletFlow.error ?? $authError)
  const mobile = isMobileWalletClient()
</script>

<div class="backdrop" role="presentation" bind:this={backdropEl} onclick={() => closeDialog()}>
  <div
    class="dialog panel"
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    aria-labelledby="wallet-title"
    onclick={(e) => e.stopPropagation()}
    onkeydown={(e) => e.stopPropagation()}
  >
    <div
      class="title-row"
      onpointerdown={onTitlePointerDown}
      onpointerup={onTitlePointerUp}
      onpointerleave={onTitlePointerUp}
      onpointercancel={onTitlePointerUp}
    >
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
          <button class="btn btn-primary" type="button" onclick={() => reopenWalletApp()}
            >{t($messages, 'walletOpenWallet')}</button
          >
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
            onclick={() => uri && openWallet(uri)}>{t($messages, 'walletOpenWallet')}</button
          >

          <div class="actions">
            <button class="btn" type="button" disabled={!uri} onclick={() => uri && copyUri(uri)}>
              {copied ? t($messages, 'walletUriCopied') : t($messages, 'walletCopyUri')}
            </button>
          </div>

          {#if launchFailed}
            <p class="err">{t($messages, 'walletOpenFailed')}</p>
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

        {#if !mobile}
          <div class="actions">
            <button class="btn" type="button" disabled={!uri} onclick={() => uri && copyUri(uri)}>
              {copied ? t($messages, 'walletUriCopied') : t($messages, 'walletCopyUri')}
            </button>
            <button
              class="btn btn-primary"
              type="button"
              disabled={!uri}
              onclick={() => uri && openWallet(uri)}>{t($messages, 'walletOpenWallet')}</button
            >
          </div>
        {/if}

        <div class="row">
          <button class="btn" type="button" onclick={() => closeDialog()}
            >{t($messages, 'walletCancel')}</button
          >
        </div>
      {/if}

      {#if $wcDebugEnabled}
        <pre class="wc-debug mono" aria-live="polite"
          >{#each $wcDebugLog as row}{row.t}ms {row.msg}
{/each}</pre
        >
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
    z-index: 200;
    width: 100%;
    height: 100%;
    height: 100dvh;
    max-height: 100dvh;
    overflow-x: hidden;
    overflow-y: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
    background: rgba(0, 0, 0, 0.45);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: max(1rem, env(safe-area-inset-top, 0px)) 1rem
      max(1rem, env(safe-area-inset-bottom, 0px));
    box-sizing: border-box;
  }
  .dialog {
    width: min(420px, 100%);
    max-height: min(90dvh, calc(100% - 0.5rem));
    overflow-y: auto;
    overscroll-behavior: contain;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    margin: auto;
    flex-shrink: 0;
  }
  .title-row {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    touch-action: manipulation;
    user-select: none;
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
  .wc-debug {
    margin: 0;
    max-height: 140px;
    overflow: auto;
    font-size: 0.65rem;
    line-height: 1.35;
    background: var(--callout);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0.45rem 0.55rem;
    white-space: pre-wrap;
    word-break: break-word;
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
