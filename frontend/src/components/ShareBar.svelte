<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import { absoluteUrl } from '../lib/seo'
  import Icon from './Icon.svelte'

  let {
    url,
    title,
  }: {
    url: string
    title: string
  } = $props()

  let open = $state(false)
  let copied = $state(false)
  let rootEl: HTMLDivElement | undefined = $state()

  const abs = $derived(absoluteUrl(url))

  function close() {
    open = false
  }

  function onDoc(e: MouseEvent) {
    if (!rootEl) return
    if (!rootEl.contains(e.target as Node)) close()
  }

  $effect(() => {
    if (!open) return
    document.addEventListener('click', onDoc)
    return () => document.removeEventListener('click', onDoc)
  })

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(abs)
      copied = true
      window.setTimeout(() => (copied = false), 1800)
    } catch {
      /* ignore */
    }
  }

  async function nativeShare() {
    if (!navigator.share) {
      open = !open
      return
    }
    try {
      await navigator.share({ title, url: abs })
    } catch {
      /* user cancelled */
    }
  }

  function intent(kind: 'x' | 'bluesky' | 'telegram') {
    const u = encodeURIComponent(abs)
    const text = encodeURIComponent(title)
    const href =
      kind === 'x'
        ? `https://twitter.com/intent/tweet?url=${u}&text=${text}`
        : kind === 'bluesky'
          ? `https://bsky.app/intent/compose?text=${text}%20${u}`
          : `https://t.me/share/url?url=${u}&text=${text}`
    window.open(href, '_blank', 'noopener,noreferrer')
    close()
  }
</script>

<div class="share" bind:this={rootEl}>
  <button
    class="btn share-btn"
    type="button"
    aria-expanded={open}
    aria-haspopup="menu"
    onclick={() => {
      if (typeof navigator.share === 'function' && !window.matchMedia('(min-width: 860px)').matches) {
        void nativeShare()
      } else {
        open = !open
      }
    }}
  >
    <Icon name="share" size={16} />
    {t($messages, 'articleShare')}
  </button>
  {#if open}
    <div class="menu" role="menu">
      <button type="button" role="menuitem" onclick={() => void copyLink()}>
        {copied ? t($messages, 'articleLinkCopied') : t($messages, 'articleShareCopyLink')}
      </button>
      <button type="button" role="menuitem" onclick={() => intent('x')}>X</button>
      <button type="button" role="menuitem" onclick={() => intent('bluesky')}>Bluesky</button>
      <button type="button" role="menuitem" onclick={() => intent('telegram')}>Telegram</button>
      {#if typeof navigator.share === 'function'}
        <button type="button" role="menuitem" onclick={() => void nativeShare()}>
          {t($messages, 'articleShareMore')}
        </button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .share {
    position: relative;
    display: inline-flex;
  }
  .share-btn {
    height: 34px;
    padding: 0 12px;
    font-size: 12.5px;
    gap: 6px;
  }
  .menu {
    position: absolute;
    top: calc(100% + 6px);
    inset-inline-start: 0;
    z-index: 20;
    min-width: 168px;
    padding: 6px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--panel);
    box-shadow: 0 12px 28px var(--card-hover-shadow);
    display: flex;
    flex-direction: column;
    gap: 2px;
    animation: pop 0.18s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  .menu button {
    border: 0;
    background: transparent;
    text-align: start;
    padding: 9px 10px;
    border-radius: 8px;
    color: var(--on-surface);
    font-size: 13px;
    font-weight: 600;
  }
  .menu button:hover,
  .menu button:focus-visible {
    background: var(--accent-soft);
    color: var(--primary);
    outline: none;
  }
  @keyframes pop {
    from {
      opacity: 0;
      transform: translateY(-4px);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .menu {
      animation: none;
    }
  }
</style>
