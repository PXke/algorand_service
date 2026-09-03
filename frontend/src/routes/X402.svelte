<script lang="ts">
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { config } from '../lib/config'
  import { ApiException } from '../lib/api/client'
  import {
    x402Api,
    X402_PATHS,
    X402_CATALOG_URL,
    X402_CATEGORIES,
    type X402Listing,
    type X402Placement,
    type X402FeatureRequest,
    type X402GradedEndpoint,
    type X402NewsItem,
  } from '../lib/api/x402'
  import { formatDispatchStamp } from '../lib/liveClock'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'
  import { isHttp } from '../lib/sanitizeHtml'

  type X402Tab = 'directory' | 'board' | 'requests' | 'grades' | 'news'

  let { tab }: { tab: X402Tab } = $props()

  let listings: X402Listing[] = $state([])
  let placements: X402Placement[] = $state([])
  let requests: X402FeatureRequest[] = $state([])
  let graded: X402GradedEndpoint[] = $state([])
  let newsItems: X402NewsItem[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let tag = $state('')
  let category = $state('')
  // The directory fetch keys on the debounced value, not the raw input, so
  // typing a tag does not fire one request per keystroke.
  let tagQuery = $state('')

  const TABS: X402Tab[] = ['directory', 'board', 'requests', 'grades', 'news']

  const tabLabel: Record<X402Tab, string> = {
    directory: 'x402TabDirectory',
    board: 'x402TabBoard',
    requests: 'x402TabRequests',
    grades: 'x402TabGrades',
    news: 'x402TabNews',
  }

  const apiBase = $derived(config.apiBaseUrl.replace(/\/$/, '') || window.location.origin)
  const endpoints = $derived([
    { key: 'x402TabDirectory', url: `${apiBase}${X402_PATHS.search}?tag=` },
    { key: 'x402TabBoard', url: `${apiBase}${X402_PATHS.board}` },
    { key: 'x402TabRequests', url: `${apiBase}${X402_PATHS.features}` },
    { key: 'x402TabGrades', url: `${apiBase}${X402_PATHS.grades}` },
    { key: 'x402TabNews', url: `${apiBase}${X402_PATHS.news}` },
  ])
  const curlExample = $derived(
    `curl -X POST ${apiBase}${X402_PATHS.features} -H 'Content-Type: application/json' -d '{"title": "...", "description": "..."}'`,
  )

  const pricing = $derived([
    { what: t($messages, 'x402PriceListLabel'), price: t($messages, 'x402PriceListValue') },
    { what: t($messages, 'x402PriceBoardLabel'), price: t($messages, 'x402PriceBoardValue') },
    { what: t($messages, 'x402PriceRequestLabel'), price: t($messages, 'x402PriceRequestValue') },
    { what: t($messages, 'x402PriceVoteLabel'), price: t($messages, 'x402PriceVoteValue') },
    { what: t($messages, 'x402PriceDemandLabel'), price: t($messages, 'x402PriceDemandValue') },
    { what: t($messages, 'x402PriceGradeLabel'), price: t($messages, 'x402PriceGradeValue') },
    { what: t($messages, 'x402PriceScoreLabel'), price: t($messages, 'x402PriceScoreValue') },
    { what: t($messages, 'x402PriceArticleLabel'), price: t($messages, 'x402PriceArticleValue') },
    {
      what: t($messages, 'x402PriceNewsSearchLabel'),
      price: t($messages, 'x402PriceNewsSearchValue'),
    },
  ])

  const count = $derived.by(() => {
    switch (tab) {
      case 'directory':
        return listings.length
      case 'board':
        return placements.length
      case 'requests':
        return requests.length
      case 'grades':
        return graded.length
      case 'news':
        return newsItems.length
    }
  })

  $effect(() => {
    const value = tag
    const id = setTimeout(() => {
      tagQuery = value.trim()
    }, 250)
    return () => clearTimeout(id)
  })

  // One AbortController per reactive run: a tab switch or a new tag/category
  // query cancels the request still in flight, so a slow earlier response
  // can never overwrite the list the reader is now looking at.
  $effect(() => {
    const which = tab
    const q = tagQuery
    const cat = category
    const ac = new AbortController()
    loading = true
    error = null
    void (async () => {
      try {
        const opts = { signal: ac.signal }
        if (which === 'directory') listings = await x402Api.search(q, cat, opts)
        else if (which === 'board') placements = await x402Api.board(opts)
        else if (which === 'requests') requests = await x402Api.features(opts)
        else if (which === 'grades') graded = await x402Api.grades(opts)
        else newsItems = await x402Api.news(opts)
        if (ac.signal.aborted) return
        loading = false
      } catch (e) {
        if (ac.signal.aborted) return
        error =
          e instanceof ApiException
            ? e.userMessage
            : untrack(() => t($messages, 'errorGeneric'))
        loading = false
      }
    })()
    return () => ac.abort()
  })

  function stamp(epoch: number | undefined | null): string {
    if (!epoch || !Number.isFinite(epoch)) return ''
    return formatDispatchStamp(epoch, $activeLocale)
  }

  function hostOf(url: string): string {
    try {
      return new URL(url).host
    } catch {
      return url
    }
  }

  function shortAddr(a: string): string {
    return a.length > 12 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a
  }

  function go(href: string, e: MouseEvent) {
    e.preventDefault()
    navigate(href)
  }
</script>

<PageMeta
  title={t($messages, 'x402Title')}
  description={t($messages, 'x402Lead') || SITE_TAGLINE}
  path={tab === 'directory' ? '/x402' : `/x402/${tab}`}
/>

<div class="page stack x402">
  <header>
    <span class="accent-slug"></span>
    <p class="kicker">{t($messages, 'x402Kicker')}</p>
    <h1>{t($messages, 'x402Title')}</h1>
    <p class="lead muted">{t($messages, 'x402Lead')}</p>
  </header>

  <section class="intro">
    <div class="pricing">
      <h2>{t($messages, 'x402PricingHeading')}</h2>
      <dl>
        {#each pricing as row (row.what)}
          <div class="price-row">
            <dt>{row.what}</dt>
            <dd>{row.price}</dd>
          </div>
        {/each}
      </dl>
    </div>

    <aside class="agents" aria-label={t($messages, 'x402ForAgentsHeading')}>
      <h2>{t($messages, 'x402ForAgentsHeading')}</h2>
      <p class="muted">{t($messages, 'x402ForAgentsBody')}</p>
      <ul class="endpoints">
        {#each endpoints as ep (ep.key)}
          <li>
            <span class="ep-label">{t($messages, ep.key)}</span>
            <code>GET {ep.url}</code>
          </li>
        {/each}
      </ul>
      <p class="curl-label">{t($messages, 'x402CatalogLabel')}</p>
      <p class="muted">{t($messages, 'x402CatalogBody')}</p>
      <pre class="curl"><code>GET {X402_CATALOG_URL}</code></pre>
      <p class="curl-label">{t($messages, 'x402ForAgentsCurlLabel')}</p>
      <pre class="curl"><code>{curlExample}</code></pre>
    </aside>
  </section>

  <nav class="tabs" aria-label={t($messages, 'x402Title')}>
    {#each TABS as item (item)}
      {@const href = item === 'directory' ? '/x402' : `/x402/${item}`}
      <a class="tab" class:active={tab === item} {href} onclick={(e) => go(href, e)}>
        {t($messages, tabLabel[item])}
      </a>
    {/each}
  </nav>

  {#if tab === 'directory'}
    <div class="filters">
      <label class="find find-tag">
        <span class="sr-only">{t($messages, 'x402TagFilter')}</span>
        <span class="query-shell">
          <span class="query-prompt" aria-hidden="true">#</span>
          <input
            type="search"
            bind:value={tag}
            placeholder={t($messages, 'x402TagFilter')}
            autocomplete="off"
            spellcheck="false"
          />
        </span>
      </label>
      <label class="find find-category">
        <span class="sr-only">{t($messages, 'x402CategoryFilter')}</span>
        <span class="query-shell">
          <select bind:value={category}>
            <option value="">{t($messages, 'x402CategoryFilter')}</option>
            {#each X402_CATEGORIES as c (c)}
              <option value={c}>{c}</option>
            {/each}
          </select>
        </span>
      </label>
    </div>
  {/if}

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if count === 0}
    <p class="muted empty">{t($messages, 'x402Empty')}</p>
  {:else}
    <p class="hit-count">
      {count}<span class="sep" aria-hidden="true">·</span>{t($messages, tabLabel[tab])}
    </p>

    {#if tab === 'directory'}
      <ul class="rows">
        {#each listings as item, i (`${item.url}-${i}`)}
          <li class="row">
            <div class="row-head">
              {#if isHttp(item.url)}
                <a class="name" href={item.url} target="_blank" rel="noopener noreferrer nofollow"
                  >{item.url}</a
                >
              {:else}
                <span class="name">{item.url}</span>
              {/if}
              <span class="price">{item.price}</span>
            </div>
            {#if item.description}
              <p class="desc">{item.description}</p>
            {/if}
            <p class="meta">
              {#if item.verified_wallet}
                <span class="badge verified">{t($messages, 'x402Verified')}</span>
              {/if}
              {#if item.category}
                <span class="badge">{item.category}</span>
              {/if}
              {#each item.assets ?? [] as asset (asset)}
                <span class="badge">{asset}</span>
              {/each}
              {#each item.tags ?? [] as tg (tg)}
                <button type="button" class="tagchip" onclick={() => (tag = tg)}>#{tg}</button>
              {/each}
              {#if item.payer}
                <span class="stamp">{t($messages, 'x402Payer')} {shortAddr(item.payer)}</span>
              {/if}
              {#if stamp(item.term_end_epoch)}
                <span class="stamp"
                  >{t($messages, 'x402ListedUntil')} {stamp(item.term_end_epoch)}</span
                >
              {/if}
            </p>
          </li>
        {/each}
      </ul>
    {:else if tab === 'board'}
      <ul class="rows">
        {#each placements as item, i (`${item.link}-${i}`)}
          <li class="row">
            <div class="row-head">
              {#if isHttp(item.link)}
                <a class="name" href={item.link} target="_blank" rel="noopener noreferrer nofollow"
                  >{item.name || hostOf(item.link)}</a
                >
              {:else}
                <span class="name">{item.name || item.link}</span>
              {/if}
              {#if typeof item.clicks === 'number'}
                <span class="price">{t($messages, 'x402Clicks', { count: item.clicks })}</span>
              {/if}
            </div>
            {#if item.pitch}
              <p class="desc">{item.pitch}</p>
            {/if}
            <p class="meta">
              <span class="stamp">{hostOf(item.link)}</span>
              {#if stamp(item.term_end_epoch)}
                <span class="stamp"
                  >{t($messages, 'x402ListedUntil')} {stamp(item.term_end_epoch)}</span
                >
              {/if}
            </p>
          </li>
        {/each}
      </ul>
    {:else if tab === 'requests'}
      <ul class="rows">
        {#each requests as item, i (item.request_id ?? `${item.title}-${i}`)}
          <li class="row">
            <div class="row-head">
              <span class="name">{item.title}</span>
              {#if typeof item.vote_total === 'number'}
                <span class="price">{t($messages, 'x402Votes', { count: item.vote_total })}</span>
              {/if}
            </div>
            {#if item.description}
              <p class="desc">{item.description}</p>
            {/if}
            <p class="meta">
              {#if stamp(item.created_at_epoch)}
                <span class="stamp">{t($messages, 'x402Filed')} {stamp(item.created_at_epoch)}</span>
              {/if}
              {#if typeof item.claims_count === 'number' && item.claims_count > 0}
                <span class="badge">{t($messages, 'x402Claims', { count: item.claims_count })}</span>
              {/if}
              {#if item.latest_claimer}
                <span class="stamp"
                  >{t($messages, 'x402Building')} {shortAddr(item.latest_claimer)}</span
                >
              {/if}
            </p>
          </li>
        {/each}
      </ul>
    {:else if tab === 'grades'}
      <ul class="rows">
        {#each graded as item, i (`${item.url}-${i}`)}
          <li class="row">
            <div class="row-head">
              {#if isHttp(item.url)}
                <a class="name" href={item.url} target="_blank" rel="noopener noreferrer nofollow"
                  >{item.url}</a
                >
              {:else}
                <span class="name">{item.url}</span>
              {/if}
            </div>
            <p class="meta">
              {#if stamp(item.last_graded_at_epoch)}
                <span class="stamp"
                  >{t($messages, 'x402LastGraded')} {stamp(item.last_graded_at_epoch)}</span
                >
              {/if}
              <span class="stamp">{t($messages, 'x402ScorePaidHint')}</span>
            </p>
          </li>
        {/each}
      </ul>
    {:else}
      <ul class="rows">
        {#each newsItems as item, i (`${item.article_id}-${i}`)}
          <li class="row">
            <div class="row-head">
              {#if isHttp(item.url)}
                <a class="name" href={item.url} target="_blank" rel="noopener noreferrer nofollow"
                  >{item.title}</a
                >
              {:else}
                <span class="name">{item.title}</span>
              {/if}
            </div>
            {#if item.summary}
              <p class="desc">{item.summary}</p>
            {/if}
            <p class="meta">
              {#each item.tags ?? [] as tg (tg)}
                <span class="badge">#{tg}</span>
              {/each}
              {#if stamp(item.published_at_epoch)}
                <span class="stamp">{stamp(item.published_at_epoch)}</span>
              {/if}
            </p>
          </li>
        {/each}
      </ul>
    {/if}
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
  .intro {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr);
    gap: 24px;
    margin-top: 8px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .intro h2,
  .curl-label {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .intro h2::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 6%;
  }
  .pricing dl {
    margin: 10px 0 0;
  }
  .price-row {
    display: flex;
    justify-content: space-between;
    gap: 16px;
    padding: 7px 0;
    border-bottom: 1px solid var(--border);
    font-family: var(--font-serif);
    font-size: 0.95rem;
  }
  .price-row dt {
    margin: 0;
  }
  .price-row dd {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--accent);
    white-space: nowrap;
  }
  .agents {
    padding: 14px 16px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--surface);
    min-width: 0;
  }
  .agents p {
    margin: 8px 0 0;
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.5;
  }
  .endpoints {
    list-style: none;
    margin: 10px 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .endpoints li {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .ep-label {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .endpoints code,
  .curl code {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--on-surface);
    word-break: break-all;
  }
  /* `.agents p` (serif body, one class + one type) outranked the bare
     `.curl-label` class above, so these sub-heads rendered as bold serif
     paragraphs instead of the mono stamps every other label on this page
     uses. Restate the machine voice at higher specificity. */
  .agents p.curl-label {
    margin: 14px 0 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .curl {
    margin: 6px 0 0;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--accent-soft);
    overflow-x: auto;
    white-space: pre-wrap;
  }
  .tabs {
    display: flex;
    flex-wrap: wrap;
    gap: 0 18px;
    margin-top: 12px;
    border-bottom: 1px solid var(--border);
  }
  .tab {
    position: relative;
    padding: 10px 0;
    font-family: var(--font-mono);
    font-size: 11.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
    text-decoration: none;
  }
  .tab:hover {
    color: var(--accent);
    text-decoration: none;
  }
  .tab.active {
    color: var(--on-surface);
  }
  .tab.active::after {
    content: '';
    position: absolute;
    left: 0;
    right: 0;
    bottom: -1px;
    height: 2px;
    background: var(--accent);
  }
  /* Tag and category share one row — stacked, the two lone 420px boxes read
     as a form, not a query bar. */
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
  .hit-count {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .hit-count .sep {
    margin-inline: 6px;
    color: var(--subtle);
  }
  .empty {
    padding: 24px 0;
    font-family: var(--font-serif);
  }
  .rows {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .row {
    padding: 14px 0;
    border-bottom: 1px solid var(--border);
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
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
    text-decoration: none;
    overflow-wrap: anywhere;
    min-width: 0;
  }
  a.name:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .price {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--accent);
  }
  .desc {
    margin: 6px 0 0;
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.5;
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
  .tagchip,
  .stamp {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .badge {
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
  }
  .badge.verified {
    color: var(--accent);
    border-color: var(--accent);
  }
  .tagchip {
    border: 0;
    padding: 0;
    background: none;
    color: var(--accent);
    cursor: pointer;
    text-transform: none;
  }
  .tagchip:hover {
    text-decoration: underline;
  }
  .err {
    color: var(--danger);
  }
  @media (max-width: 759px) {
    .intro {
      grid-template-columns: 1fr;
    }
  }
</style>
