<script lang="ts">
  import { onMount } from 'svelte'
  import { searchApi } from '../lib/api/search'
  import { messages, t } from '../lib/i18n'
  import { articleHref } from '../lib/paths'
  import { route, navigate } from '../lib/router'
  import { ApiException } from '../lib/api/client'
  import Icon from '../components/Icon.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'

  let q = $state($route.query.get('q') ?? '')
  let items: Array<Record<string, unknown>> = $state([])
  let engine = $state('')
  let loading = $state(false)
  let searched = $state(false)
  let error = $state<string | null>(null)
  let timer: ReturnType<typeof setTimeout> | undefined
  let inputEl: HTMLInputElement | undefined = $state()

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
    return sn || String(item.summary ?? '')
  }

  async function run(query = q) {
    const trimmed = query.trim()
    if (!trimmed) return
    loading = true
    error = null
    searched = true
    try {
      const res = await searchApi.search(trimmed)
      items = res.items
      engine = res.engine
    } catch (e) {
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      items = []
    } finally {
      loading = false
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
    queueMicrotask(() => inputEl?.focus())
  })
</script>

<PageMeta
  title={t($messages, 'pageTitleSearch')}
  description={t($messages, 'searchSubtitle') || SITE_TAGLINE}
  path="/search"
/>

<div class="page stack search-page">
  <header class="hero">
    <span class="accent-slug"></span>
    <h1>{t($messages, 'searchTitle')}</h1>
    <p class="lead muted">{t($messages, 'searchSubtitle')}</p>
  </header>

  <form class="search-card panel" onsubmit={onSubmit}>
    <label class="query-field">
      <span class="query-label">{t($messages, 'searchQueryLabel')}</span>
      <span class="query-shell">
        <span class="query-icon" aria-hidden="true">
          <Icon name="search" size={22} />
        </span>
        <input
          bind:this={inputEl}
          bind:value={q}
          oninput={onInput}
          type="search"
          autocomplete="off"
          enterkeyhint="search"
          placeholder={t($messages, 'searchQueryHint')}
        />
        {#if q}
          <button class="clear" type="button" title="Clear" onclick={clearQuery}>
            <Icon name="close" size={18} />
          </button>
        {/if}
      </span>
    </label>
    <button class="btn btn-primary go" type="submit" disabled={loading || !q.trim()}>
      <Icon name="arrow_forward" size={18} />
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

  {#each items as item}
    {@const id = String(item.article_id ?? '')}
    <a
      class="hit panel"
      href={id ? articleHref(id) : undefined}
      onclick={(e) => {
        if (!id) return
        e.preventDefault()
        navigate(articleHref(id))
      }}
    >
      <span class="hit-icon">
        <Icon name="article" size={20} />
      </span>
      <span class="hit-copy">
        <strong class="hit-title">{@html highlightHtml(titleOf(item))}</strong>
        {#if excerptOf(item)}
          <p class="hit-excerpt">{@html highlightHtml(excerptOf(item))}</p>
        {/if}
      </span>
      <span class="hit-chevron" aria-hidden="true">
        <Icon name="chevron_right" size={22} />
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
    padding: 20px;
    border-radius: 18px;
    border-color: color-mix(in srgb, var(--accent) 22%, var(--border));
    background:
      linear-gradient(
        165deg,
        color-mix(in srgb, var(--accent) 7%, var(--panel)) 0%,
        var(--panel) 55%
      );
    box-shadow: 0 6px 16px var(--card-shadow);
  }
  .query-field {
    flex: 1 1 220px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .query-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
  }
  .query-shell {
    display: flex;
    align-items: center;
    gap: 4px;
    min-height: 52px;
    padding: 0 8px 0 12px;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--surface);
  }
  .query-shell:focus-within {
    border-color: var(--primary);
    border-width: 1.6px;
    padding: 0 7.4px 0 11.4px;
  }
  .query-icon {
    display: grid;
    place-items: center;
    color: var(--primary);
    flex-shrink: 0;
  }
  .query-shell input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--on-surface);
    font-size: 17px;
    padding: 14px 8px;
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
    height: 52px;
    padding: 0 20px;
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
  .hit {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    padding: 18px 18px 18px 22px;
    border-radius: 16px;
    color: inherit;
    text-decoration: none;
    transition:
      box-shadow 0.22s ease,
      border-color 0.22s ease,
      transform 0.22s cubic-bezier(0.22, 1, 0.36, 1);
  }
  .hit:hover {
    text-decoration: none;
    border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
    box-shadow: 0 8px 18px var(--card-hover-shadow);
    transform: translateY(-1px);
  }
  .hit-icon {
    width: 40px;
    height: 40px;
    border-radius: 10px;
    display: grid;
    place-items: center;
    flex-shrink: 0;
    background: var(--accent-soft);
    color: var(--accent);
  }
  .hit-copy {
    flex: 1;
    min-width: 0;
  }
  .hit-title {
    display: block;
    font-family: var(--font-display);
    font-size: 19px;
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
  .hit-chevron {
    color: var(--subtle);
    display: grid;
    place-items: center;
    align-self: center;
    flex-shrink: 0;
  }
  .err {
    color: var(--danger);
  }
</style>
