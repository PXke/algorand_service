<script lang="ts">
  import { get } from 'svelte/store'
  import { onMount, untrack } from 'svelte'
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import LeadStory from '../components/LeadStory.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import MarketsTape from '../components/MarketsTape.svelte'
  import ChainPulse from '../components/ChainPulse.svelte'
  import ContinueReading from '../components/ContinueReading.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_NAME, SITE_TAGLINE, ogLocaleFor } from '../lib/seo'
  import { proxiedImageUrl, looksLikeFaviconUrl } from '../lib/images'
  import { takeSsrFeed } from '../lib/ssrFeed'
  import { readerDesk, READER_DESKS, deskMessageKey, type ReaderDesk } from '../lib/tags'
  import { feedEnterIndex } from '../lib/motion'

  function seedFromSsr(): ArticleItem[] {
    const data = takeSsrFeed<{ items?: ArticleItem[] }>()
    // SSR homepage feed is English-only — skip seed for other locales.
    if (get(activeLocale) !== 'en') return []
    const items = data?.items
    return Array.isArray(items) ? items : []
  }

  const seeded = seedFromSsr()
  let items: ArticleItem[] = $state(seeded)
  let hot: ArticleItem[] = $state([])
  let price = $state<Record<string, unknown> | null>(null)
  let history: Array<{ epoch: number; price: number }> = $state([])
  let error = $state<string | null>(null)
  let loading = $state(seeded.length === 0)
  let enterAt = $state<Map<string, number>>(new Map())
  const entered = new Set<string>()

  const lead = $derived(items[0])
  const leadHasMedia = $derived.by(() => {
    const url = lead?.image_url?.trim()
    return Boolean(url && !looksLikeFaviconUrl(url))
  })
  const RAIL_STORY_COUNT = 5
  const briefs = $derived(items.slice(1, 1 + RAIL_STORY_COUNT))
  const desks = $derived.by(() => {
    const buckets: Record<ReaderDesk, ArticleItem[]> = {
      markets: [],
      protocol: [],
      assets: [],
      people: [],
      wire: [],
    }
    const MAX = 4
    for (const article of items.slice(1 + RAIL_STORY_COUNT)) {
      const desk = readerDesk(article.tags)
      if (desk !== 'wire' && buckets[desk].length >= MAX) {
        buckets.wire.push(article)
      } else {
        buckets[desk].push(article)
      }
    }
    const columns = READER_DESKS.filter((id) => id !== 'wire' && buckets[id].length > 0).map(
      (id) => ({ id, items: buckets[id] }),
    )
    const extra = buckets.wire.slice(0, 6)
    return { columns, extra }
  })
  /* The rail carries most-read; the main column shouldn't repeat it. */
  const railHot = $derived(
    hot.filter((a) => a.article_id !== lead?.article_id).slice(0, RAIL_STORY_COUNT),
  )

  function deskLabel(id: ReaderDesk): string {
    return t($messages, deskMessageKey(id))
  }

  $effect(() => {
    $activeLocale
    entered.clear()
    enterAt = new Map()
  })

  $effect(() => {
    if (loading) return
    const rows = [
      ...briefs,
      ...railHot,
      ...desks.columns.flatMap((col) => col.items),
      ...desks.extra,
    ]
    const stamp = new Map<string, number>()
    let idx = 0
    for (const row of rows) {
      if (entered.has(row.article_id)) continue
      entered.add(row.article_id)
      stamp.set(row.article_id, idx++)
    }
    if (stamp.size) enterAt = new Map([...enterAt, ...stamp])
  })

  $effect(() => {
    const raw = lead?.image_url?.trim()
    if (!raw) return
    const href = proxiedImageUrl(raw)
    if (!href || document.querySelector(`link[rel="preload"][as="image"][href="${href.replace(/"/g, '')}"]`))
      return
    const link = document.createElement('link')
    link.rel = 'preload'
    link.as = 'image'
    link.href = href
    link.dataset.lcp = href
    document.head.appendChild(link)
  })

  // Re-fetch feed/hot whenever the active language changes.
  //
  // `items` must be read through untrack(): this effect also *writes* items,
  // so a tracked read makes the response retrigger the effect, which cancels
  // the in-flight run before `hot` commits and immediately refetches. That
  // loop ran at ~120 req/s per visitor and left the rail permanently empty.
  //
  // Soft refresh: when SSR (or a prior visit) already seeded the page, keep
  // showing it and swap in the network response without a skeleton flash.
  $effect(() => {
    const lang = $activeLocale
    const ac = new AbortController()
    loading = untrack(() => items.length) === 0
    error = null
    void (async () => {
      try {
        // Independent requests, and the rail is above the fold now — running
        // them in parallel takes a full round trip off first paint.
        // limit matches the edition (lead + the rest filed by desk).
        const hotPromise = newsApi.fetchHot(RAIL_STORY_COUNT, 'hot', lang, ac.signal).catch(() => [])
        const feed = await newsApi.fetchFeedPage({ limit: 36, lang, signal: ac.signal })
        if (ac.signal.aborted) return
        items = feed.items
        loading = false
        const hotItems = await hotPromise
        if (ac.signal.aborted) return
        hot = hotItems
      } catch (e) {
        if (ac.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        if (!untrack(() => items.length)) {
          error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        }
        loading = false
      }
    })()
    return () => {
      ac.abort()
    }
  })

  // Markets data is language-agnostic — load once. onMount, not $effect: the
  // effect form read `marketsLoaded` and then wrote it, so it invalidated
  // itself and only the guard stopped a re-run.
  onMount(() => {
    const ac = new AbortController()
    void (async () => {
      const [priceRes, histRes] = await Promise.all([
        newsApi.fetchPrice(ac.signal).catch(() => null),
        newsApi.fetchPriceHistory(ac.signal).catch(() => null),
      ])
      if (ac.signal.aborted) return
      if (priceRes?.available) price = priceRes
      const pts = Array.isArray(histRes?.points) ? histRes.points : []
      history = pts
        .map((p: Record<string, unknown>) => ({
          epoch: Number(p.epoch ?? 0),
          price: Number(p.price_usd ?? 0),
        }))
        .filter((p: { epoch: number; price: number }) => p.epoch > 0 && p.price > 0)
    })()
    return () => {
      ac.abort()
    }
  })
</script>

<PageMeta
  title={SITE_NAME}
  description={t($messages, 'appTagline') || SITE_TAGLINE}
  path="/"
  ogLocale={ogLocaleFor($activeLocale)}
/>

<div class="page page-wide stack front">
  <ContinueReading />
  {#if loading}
    <FeedSkeleton lead rows={6} />
  {:else if error}
    <p class="err">{error}</p>
  {:else if !lead}
    <div class="empty">
      <h2>{t($messages, 'newsEmptyTitle')}</h2>
      <p class="muted">{t($messages, 'newsEmptyMessage')}</p>
      <div class="empty-actions">
        <button class="btn btn-primary" type="button" onclick={() => navigate('/topics')}>
          {t($messages, 'emptyBrowseTopics')}
        </button>
        <button class="btn" type="button" onclick={() => navigate('/news')}>
          {t($messages, 'emptyBrowseLatest')}
        </button>
      </div>
    </div>
  {:else}
    <!-- Type-only lead sits beside the chain pulse. A plated lead is copy |
         photo, and the pulse files with the tape. -->
    {#snippet pulse()}
      <ChainPulse />
    {/snippet}
    <div class="banner" class:with-pulse={!leadHasMedia}>
      <LeadStory article={lead} />
      {#if !leadHasMedia}
        {@render pulse()}
      {/if}
    </div>
    {#if price}
      <div class="markets-row" class:split={leadHasMedia}>
        <MarketsTape {price} {history} />
        {#if leadHasMedia}
          {@render pulse()}
        {/if}
      </div>
    {:else if leadHasMedia}
      {@render pulse()}
    {/if}
    {#if briefs.length || railHot.length}
      <div class="editorial">
        {#if briefs.length}
          <section class="pack rail-module">
            <h2 class="rail-head">{t($messages, 'homePackTitle')}</h2>
            {#each briefs as article (article.article_id)}
              <StoryRow
                {article}
                showReads={false}
                showWhen={false}
                enterIndex={feedEnterIndex(enterAt, article.article_id)}
              />
            {/each}
            <a
              class="rail-more"
              href="/news"
              onclick={(e) => {
                e.preventDefault()
                navigate('/news')
              }}>{t($messages, 'navLatest')} →</a
            >
          </section>
        {/if}
        {#if railHot.length}
          <aside class="rail">
            <section class="rail-module">
              <h2 class="rail-head">{t($messages, 'hotTitle')}</h2>
              {#each railHot as article, i (article.article_id)}
                <StoryRow
                  {article}
                  dense
                  rank={i + 1}
                  showWhen={false}
                  enterIndex={feedEnterIndex(enterAt, article.article_id)}
                />
              {/each}
              <a
                class="rail-more"
                href="/hot"
                onclick={(e) => {
                  e.preventDefault()
                  navigate('/hot')
                }}>{t($messages, 'hotTitle')} →</a
              >
            </section>
          </aside>
        {/if}
      </div>
    {/if}

    {#if desks.columns.length}
      <div class="desks">
        {#each desks.columns as desk (desk.id)}
          <section class="desk">
            <h2 class="desk-head">{deskLabel(desk.id)}</h2>
            {#each desk.items as article (article.article_id)}
              <StoryRow
                {article}
                dense
                showReads={false}
                showWhen={false}
                enterIndex={feedEnterIndex(enterAt, article.article_id)}
              />
            {/each}
          </section>
        {/each}
      </div>
    {/if}
    {#if desks.extra.length}
      <section class="also">
        <h2 class="desk-head">{t($messages, 'deskWire')}</h2>
        <div class="also-grid">
          {#each desks.extra as article (article.article_id)}
            <StoryRow
              {article}
              dense
              showReads={false}
              showThumb
              showWhen={false}
              enterIndex={feedEnterIndex(enterAt, article.article_id)}
            />
          {/each}
        </div>
      </section>
    {/if}
    {#if desks.columns.length || desks.extra.length}
      <button class="btn btn-outlined more" type="button" onclick={() => navigate('/news')}>
        {t($messages, 'navLatest')} →
      </button>
    {/if}
  {/if}
</div>

<style>
  .front {
    gap: 28px;
  }
  @media (min-width: 1080px) {
    .banner.with-pulse,
    .markets-row.split {
      display: grid;
      grid-template-columns: minmax(20rem, 1.15fr) minmax(260px, 0.85fr);
      column-gap: 0;
      align-items: start;
    }
    .banner.with-pulse {
      border-bottom: 1px solid var(--border);
      align-items: stretch;
    }
    .banner.with-pulse :global(.lead) {
      max-width: none;
    }
    .banner.with-pulse :global(.pulse),
    .markets-row.split :global(.pulse) {
      border-inline-start: 1px solid var(--border);
      padding-inline-start: 28px;
      margin-inline-start: 28px;
    }
  }
  .pack {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
  }
  .pack :global(.title) {
    font-size: var(--fs-story);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .pack :global(.row:last-of-type) {
    border-bottom: 0;
  }
  .pack :global(.thumb) {
    width: 128px;
    height: 84px;
  }
  /* Two desks across — four 274px columns crumpled every headline into a
     tower. A pair of columns is a newspaper; a spreadsheet is not. */
  .desks {
    grid-template-columns: 1fr;
    gap: 32px 0;
  }
  @media (min-width: 700px) {
    .desks {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      column-gap: 0;
    }
    .desks > :global(.desk:not(:last-child)) {
      border-inline-end: 0;
      padding-inline-end: 0;
      margin-inline-end: 0;
    }
    .desks > :global(.desk:nth-child(odd)) {
      border-inline-end: 1px solid var(--border);
      padding-inline-end: 28px;
      margin-inline-end: 28px;
    }
  }
  @media (min-width: 1080px) {
    .desks {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .desk :global(.title) {
    font-size: var(--fs-story);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .also {
    width: 100%;
  }
  .also-grid {
    display: grid;
    gap: 0 28px;
  }
  @media (min-width: 700px) {
    .also-grid {
      grid-template-columns: 1fr 1fr;
    }
    .also-grid :global(.row:nth-last-child(-n + 2)) {
      border-bottom: 0;
    }
  }
  .also :global(.title) {
    font-size: var(--fs-story);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .rail-more {
    margin-top: auto;
    padding-top: 12px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .rail-more:hover {
    color: var(--primary);
  }
  /* Rail rows are marginalia: no art, tighter rhythm. */
  .rail :global(.thumb) {
    display: none;
  }
  .rail :global(.row) {
    padding: 11px 0;
  }
  .rail :global(.row:last-of-type) {
    border-bottom: 0;
  }
  .more {
    align-self: flex-start;
    margin-top: 4px;
  }
  .empty-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 16px;
  }
  .err {
    color: var(--danger);
  }
</style>
