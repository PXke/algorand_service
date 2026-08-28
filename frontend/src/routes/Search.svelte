<script lang="ts">
  import { onMount } from 'svelte'
  import { get } from 'svelte/store'
  import { searchApi } from '../lib/api/search'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { articleHref } from '../lib/paths'
  import { route, navigate } from '../lib/router'
  import { ApiException } from '../lib/api/client'
  import { LatestOnly } from '../lib/asyncGuard'
  import Icon from '../components/Icon.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'
  import { staggerMs } from '../lib/motion'

  let q = $state($route.query.get('q') ?? '')
  let items: Array<Record<string, unknown>> = $state([])
  let engine = $state('')
  let loading = $state(false)
  let searched = $state(false)
  let error = $state<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined
  let inputEl: HTMLInputElement | undefined = $state()
  // Search-as-you-type fires one request per debounced keystroke; the
  // network can resolve them out of order, so a stale response for an
  // earlier (shorter) query must not clobber a newer one's results.
  const inflight = new LatestOnly()

  /** Typesense wraps matches in `<mark>`; keep only that markup. */
  function highlightHtml(raw: string): string {
    const escaped = raw
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
    return escaped
      .replaceAll('&lt;mark&gt;', '<mark>')
      .replaceAll('&lt;/mark&gt;', '</mark>')
  }

  function titleOf(item: Record<string, unknown>): string {
    const hi = String(item.title_highlight ?? '').trim()
    return hi || String(item.title ?? '')
  }

  function excerptOf(item: Record<string, unknown>): string {
    const sn = String(item.snippet ?? '').trim()
    const raw = sn || String(item.summary ?? '')
    /* Typesense sometimes returns markdown still in the snippet. Show the
       link text, not the raw [label](url) source. */
    return raw.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
  }

  async function run(query = q) {
    const trimmed = query.trim()
    if (!trimmed) return
    const { signal, stale } = inflight.next()
    loading = true
    error = null
    searched = true
    try {
      const res = await searchApi.search(trimmed, 20, undefined, get(activeLocale), signal)
      if (stale()) return
      items = res.items
      engine = res.engine
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      items = []
    } finally {
      if (!stale()) loading = false
    }
  }

  function syncUrl(query: string) {
    const trimmed = query.trim()
    const next = trimmed ? `/search?q=${encodeURIComponent(trimmed)}` : '/search'
    navigate(next, true)
  }

  function onInput() {
    clearTimeout(timer)
    if (!q.trim()) {
      inflight.cancel()
      items = []
      engine = ''
      error = null
      searched = false
      syncUrl('')
      return
    }
    timer = setTimeout(() => {
      syncUrl(q)
      void run(q)
    }, 450)
  }

  function onSubmit(e: Event) {
    e.preventDefault()
    clearTimeout(timer)
    syncUrl(q)
    void run(q)
  }

  function clearQuery() {
    clearTimeout(timer)
    inflight.cancel()
    q = ''
    items = []
    engine = ''
    error = null
    searched = false
    syncUrl('')
    inputEl?.focus()
  }

  onMount(() => {
    if (q.trim()) void run(q)
    // Don't pop the soft keyboard on phones when opening Search empty.
    const fine = window.matchMedia('(pointer: fine)').matches
    if (fine || q.trim()) {
      queueMicrotask(() => inputEl?.focus())
    }
  })
</script>

<PageMeta
  title={t($messages, 'pageTitleSearch')}
  description={t($messages, 'searchSubtitle') || SITE_TAGLINE}
  path="/search"
  noindex
/>

<div class="page stack search-page">
  <header class="hero">
    <span class="accent-slug"></span>
    <h1>{t($messages, 'searchTitle')}</h1>
    <p class="lead muted">{t($messages, 'searchSubtitle')}</p>
  </header>

  <form class="search-card" onsubmit={onSubmit}>
    <label class="query-field">
      <span class="query-label">{t($messages, 'searchQueryLabel')}</span>
      <span class="query-shell">
        <span class="query-prompt" aria-hidden="true">›</span>
        <input
          bind:this={inputEl}
          bind:value={q}
          oninput={onInput}
          type="search"
          autocomplete="off"
          enterkeyhint="search"
          spellcheck="false"
          placeholder={t($messages, 'searchQueryHint')}
        />
        {#if q}
          <button class="clear" type="button" title="Clear" onclick={clearQuery}>
            <Icon name="close" size={18} />
          </button>
        {/if}
      </span>
    </label>
    <button class="btn btn-outlined go" type="submit" disabled={loading || !q.trim()}>
      {t($messages, 'searchAction')}
    </button>
  </form>

  {#if loading}
    <div class="loading-strip" aria-hidden="true"></div>
  {/if}

  {#if error}
    <p class="err banner">{error}</p>
  {:else if engine === 'error'}
    <p class="muted">{t($messages, 'searchErrorBackend')}</p>
  {/if}

  {#if searched && !loading && !error && items.length === 0}
    <div class="empty">
      <Icon name="search_off" size={36} />
      <h2>{t($messages, 'searchEmptyTitle')}</h2>
      <p class="muted">{t($messages, 'searchEmptyMessage')}</p>
    </div>
  {/if}

  {#if searched && !loading && !error && items.length}
    <p class="hit-count motion-results">
      {items.length}
      {#if engine}<span class="sep" aria-hidden="true">·</span><span>{engine}</span>{/if}
    </p>
  {/if}

  {#each items as item, i (String(item.article_id ?? item.title ?? ''))}
    {@const id = String(item.article_id ?? '')}
    {@const slug = item.slug ? String(item.slug) : null}
    <a
      class="hit enter"
      style="--enter-delay: {staggerMs(i)}ms"
      href={id ? articleHref(id, undefined, slug) : undefined}
      onclick={(e) => {
        if (!id) return
        e.preventDefault()
        navigate(articleHref(id, undefined, slug))
      }}
    >
      <span class="hit-copy">
        <strong class="hit-title">{@html highlightHtml(titleOf(item))}</strong>
        {#if excerptOf(item)}
          <p class="hit-excerpt">{@html highlightHtml(excerptOf(item))}</p>
        {/if}
      </span>
    </a>
  {/each}
</div>

<style>
  .search-page {
    gap: 16px;
  }
  .hero h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 40rem;
  }
  .search-card {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: flex-end;
    padding: 0 0 18px;
    border: 0;
    border-bottom: 1px solid var(--border);
    background: transparent;
    border-radius: 0;
  }
  .query-field {
    flex: 1 1 220px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .query-label {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .query-shell {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 48px;
    padding: 0 8px 0 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--surface);
  }
  .query-shell:focus-within {
    border-color: var(--accent);
  }
  .query-prompt {
    font-family: var(--font-mono);
    font-size: 18px;
    font-weight: 600;
    color: var(--accent);
    line-height: 1;
    flex-shrink: 0;
  }
  .query-shell input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 15px;
    padding: 12px 4px;
    outline: none;
  }
  .query-shell input::-webkit-search-cancel-button {
    -webkit-appearance: none;
    appearance: none;
  }
  .clear {
    border: 0;
    background: transparent;
    color: var(--subtle);
    width: 36px;
    height: 36px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    flex-shrink: 0;
  }
  .clear:hover {
    background: color-mix(in srgb, var(--on-surface) 8%, transparent);
    color: var(--on-surface);
  }
  .go {
    flex: 0 0 auto;
    height: 48px;
    padding: 0 18px;
  }
  .go:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .loading-strip {
    height: 3px;
    border-radius: 2px;
    background: linear-gradient(
      90deg,
      transparent,
      color-mix(in srgb, var(--primary) 55%, transparent),
      transparent
    );
    background-size: 200% 100%;
    animation: sweep 1.1s linear infinite;
  }
  @keyframes sweep {
    from {
      background-position: 100% 0;
    }
    to {
      background-position: -100% 0;
    }
  }
  .banner {
    margin: 0;
    padding: 12px 14px;
    border-radius: 12px;
    background: color-mix(in srgb, var(--danger) 12%, var(--panel));
    border: 1px solid color-mix(in srgb, var(--danger) 35%, var(--border));
  }
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    text-align: center;
    gap: 6px;
    padding: 36px 16px;
    color: var(--subtle);
  }
  .empty h2 {
    margin: 8px 0 0;
    font-size: 1.25rem;
  }
  .empty p {
    margin: 0;
    max-width: 28rem;
  }
  .hit-count {
    margin: 4px 0 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  :global(.motion-results) {
    animation: rise-in 0.32s cubic-bezier(0.22, 1, 0.36, 1) both;
  }
  .hit-count .sep {
    margin-inline: 6px;
    color: var(--subtle);
  }
  .hit {
    display: block;
    padding: 16px 0;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  @media (prefers-reduced-motion: no-preference) {
    .hit.enter {
      animation: rise-in 0.4s cubic-bezier(0.22, 1, 0.36, 1) both;
      animation-delay: var(--enter-delay, 0ms);
    }
  }
  .hit:hover {
    text-decoration: none;
  }
  .hit:hover .hit-title {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .hit-copy {
    min-width: 0;
  }
  .hit-title {
    display: block;
    font-family: var(--font-display);
    font-size: 18px;
    font-weight: 700;
    line-height: 1.3;
    letter-spacing: -0.2px;
  }
  .hit-title :global(mark) {
    background: color-mix(in srgb, var(--primary) 18%, transparent);
    color: inherit;
    font-weight: 700;
    padding: 0 0.1em;
    border-radius: 2px;
  }
  .hit-excerpt {
    margin: 8px 0 0;
    font-size: 0.95rem;
    line-height: 1.5;
    color: var(--muted);
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .hit-excerpt :global(mark) {
    background: color-mix(in srgb, var(--primary) 18%, transparent);
    color: inherit;
    font-weight: 600;
    padding: 0 0.1em;
    border-radius: 2px;
  }
  .err {
    color: var(--danger);
  }
  @media (max-width: 519px) {
    .search-card {
      padding: 0 0 14px;
      gap: 10px;
    }
    .go {
      flex: 1 1 100%;
      width: 100%;
    }
    .hit {
      padding: 14px 0;
    }
    .hit-title {
      font-size: 17px;
    }
    .hit-excerpt {
      -webkit-line-clamp: 3;
      line-clamp: 3;
    }
  }
</style>
