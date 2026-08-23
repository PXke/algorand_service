<script lang="ts">
  import { messages, t } from '../lib/i18n'
  import { absoluteUrl } from '../lib/seo'
  import Icon from './Icon.svelte'

  let {
    url,
    title,
    compact = false,
  }: {
    url: string
    title: string
    compact?: boolean
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

<div class="share" class:compact bind:this={rootEl}>
  <button
    class="btn share-btn"
    type="button"
    aria-expanded={open}
    aria-haspopup="menu"
    aria-label={t($messages, 'articleShare')}
    title={t($messages, 'articleShare')}
    onclick={() => {
      if (typeof navigator.share === 'function' && !window.matchMedia('(min-width: 860px)').matches) {
        void nativeShare()
      } else {
        open = !open
      }
    }}
  >
    {#if !compact}
      <Icon name="share" size={16} />
    {/if}
    {t($messages, 'articleShare')}
  </button>
  {#if open}
    <div class="menu motion-menu" role="menu">
      <button
        type="button"
        role="menuitem"
        class:copied
        onclick={() => void copyLink()}
      >
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
    height: 36px;
    padding: 0 12px;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    gap: 8px;
    background: transparent;
    box-shadow: none;
  }
  .share.compact .share-btn {
    height: 28px;
    padding: 0;
    border: 0;
    border-radius: 0;
    color: var(--muted);
    background: transparent;
  }
  .share.compact .share-btn:hover {
    color: var(--accent);
    background: transparent;
  }
  .menu {
    position: absolute;
    top: calc(100% + 6px);
    inset-inline-start: 0;
    z-index: 20;
    min-width: 168px;
    padding: 4px;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
    box-shadow: none;
    display: flex;
    flex-direction: column;
    gap: 0;
    animation: menu-pop 0.2s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  .share.compact .menu {
    inset-inline-start: auto;
    inset-inline-end: 0;
  }
  .menu button {
    border: 0;
    background: transparent;
    text-align: start;
    padding: 9px 10px;
    border-radius: 0;
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.2px;
  }
  .menu button:hover,
  .menu button:focus-visible {
    background: color-mix(in srgb, var(--accent) 8%, transparent);
    color: var(--on-surface);
    outline: none;
  }
  @media (prefers-reduced-motion: no-preference) {
    .menu button.copied {
      animation: copied-pulse 0.45s ease both;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .menu {
      animation: none;
    }
  }
</style>
