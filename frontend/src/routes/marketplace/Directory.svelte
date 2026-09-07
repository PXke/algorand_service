<script lang="ts">
  /** Endpoint directory (x402.pxke.me/directory). Same search/filter/list
   * behaviour the old X402.svelte "directory" tab had, minus the page-switch
   * pills, pricing accordion and register-as-tab chrome that move to the
   * Overview/Developers/List pages under the redesign -- and every row now
   * links to a real detail page instead of being a dead end (the audit's
   * §2.3 "lists with no depth" finding).
   */
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../../lib/i18n'
  import { navigate } from '../../lib/router'
  import { ApiException } from '../../lib/api/client'
  import { x402Api, X402_CATEGORIES, type X402Listing } from '../../lib/api/x402'
  import { x402Stamp, x402ShortAddr } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import PageMeta from '../../components/PageMeta.svelte'

  let listings: X402Listing[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let tag = $state('')
  let category = $state('')
  let tagQuery = $state('')

  $effect(() => {
    const value = tag
    const id = setTimeout(() => {
      tagQuery = value.trim()
    }, 250)
    return () => clearTimeout(id)
  })

  $effect(() => {
    const q = tagQuery
    const cat = category
    const ac = new AbortController()
    loading = true
    error = null
    void (async () => {
      try {
        listings = await x402Api.search(q, cat, { signal: ac.signal })
        if (ac.signal.aborted) return
        loading = false
      } catch (e) {
        if (ac.signal.aborted) return
        error = e instanceof ApiException ? e.userMessage : untrack(() => t($messages, 'errorGeneric'))
        loading = false
      }
    })()
    return () => ac.abort()
  })

  function openListing(url: string, e: MouseEvent) {
    e.preventDefault()
    navigate(`/listing?url=${encodeURIComponent(url)}`)
  }
</script>

<PageMeta
  title="Directory"
  description="Every live x402 endpoint listing, searchable by tag and category."
  path="/directory"
/>

<div class="page stack x402">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">Get found</p>
    <h1>Directory</h1>
    <p class="lead muted">
      List an x402 endpoint for other agents to find, or search what other operators have
      already listed. Stays listed for as long as it keeps passing health probes.
    </p>
  </header>

  <div class="filters">
    <label class="find find-tag">
      <span class="sr-only">Filter by tag</span>
      <span class="query-shell">
        <span class="query-prompt" aria-hidden="true">#</span>
        <input
          type="search"
          bind:value={tag}
          placeholder="Filter by tag"
          autocomplete="off"
          spellcheck="false"
        />
      </span>
    </label>
    <label class="find find-category">
      <span class="sr-only">Filter by category</span>
      <span class="query-shell">
        <select bind:value={category}>
          <option value="">Filter by category</option>
          {#each X402_CATEGORIES as c (c)}
            <option value={c}>{c}</option>
          {/each}
        </select>
      </span>
    </label>
  </div>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="x402-err">{error}</p>
  {:else if listings.length === 0}
    <p class="muted x402-empty">{t($messages, 'x402Empty')}</p>
  {:else}
    <p class="x402-hit-count">{listings.length}<span class="sep" aria-hidden="true">·</span>listed</p>
    <ul class="x402-rows">
      {#each listings as item, i (`${item.url}-${i}`)}
        <li class="x402-row">
          <div class="x402-row-head">
            <a
              class="x402-row-name"
              href={`/listing?url=${encodeURIComponent(item.url)}`}
              onclick={(e) => openListing(item.url, e)}
            >
              {item.url}
            </a>
            <span class="x402-row-price">{item.price}</span>
          </div>
          {#if item.description}
            <p class="x402-row-desc">{item.description}</p>
          {/if}
          <p class="x402-row-meta">
            {#if item.verified_wallet}
              <span class="x402-badge verified">{t($messages, 'x402Verified')}</span>
            {/if}
            {#if item.category}
              <span class="x402-badge">{item.category}</span>
            {/if}
            {#each item.assets ?? [] as asset (asset)}
              <span class="x402-badge">{asset}</span>
            {/each}
            {#each item.tags ?? [] as tg (tg)}
              <button type="button" class="x402-tagchip" onclick={() => (tag = tg)}>#{tg}</button>
            {/each}
            {#if item.payer}
              <span class="x402-stamp">Listed by {x402ShortAddr(item.payer)}</span>
            {/if}
            {#if x402Stamp(item.term_end_epoch, $activeLocale)}
              <span class="x402-stamp">Until {x402Stamp(item.term_end_epoch, $activeLocale)}</span>
            {/if}
          </p>
          {#if isHttp(item.url)}
            <p class="x402-row-meta">
              <a class="visit" href={item.url} target="_blank" rel="noopener noreferrer nofollow"
                >Visit endpoint ↗</a
              >
            </p>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 46rem;
    font-family: var(--font-serif);
    font-size: 17px;
    line-height: 1.55;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .find {
    display: block;
  }
  .find-tag {
    flex: 1 1 240px;
    max-width: 420px;
  }
  .find-category {
    flex: 0 1 240px;
  }
  .query-shell {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--surface);
  }
  .query-shell:focus-within {
    border-color: var(--accent);
  }
  .query-prompt {
    font-family: var(--font-mono);
    font-size: 16px;
    font-weight: 600;
    color: var(--accent);
    line-height: 1;
  }
  .find input,
  .find select {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 13px;
    padding: 10px 0;
    outline: none;
  }
  .find select {
    cursor: pointer;
    align-self: stretch;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  .visit {
    color: var(--accent);
    font-family: var(--font-mono);
    font-size: 11px;
    text-decoration: none;
  }
  .visit:hover {
    text-decoration: underline;
  }
</style>
