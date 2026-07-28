<script lang="ts">
  import { get } from 'svelte/store'
  import { onMount, untrack } from 'svelte'
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import LeadStory from '../components/LeadStory.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import ByTheNumbers from '../components/ByTheNumbers.svelte'
  import ContinueReading from '../components/ContinueReading.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_NAME, SITE_TAGLINE, ogLocaleFor } from '../lib/seo'
  import { proxiedImageUrl } from '../lib/images'
  import { takeSsrFeed } from '../lib/ssrFeed'

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

  const lead = $derived(items[0])
  const secondary = $derived(items.slice(1, 5))
  const more = $derived(items.slice(5, 18))
  /* The rail carries most-read; the main column shouldn't repeat it. */
  const RAIL_HOT_COUNT = 5
  const railHot = $derived(hot.slice(0, RAIL_HOT_COUNT))

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
  $effect(() => {
    const lang = $activeLocale
    let cancelled = false
    loading = untrack(() => items.length) === 0
    error = null
    void (async () => {
      try {
        // Independent requests, and the rail is above the fold now — running
        // them in parallel takes a full round trip off first paint.
        const hotPromise = newsApi.fetchHot(RAIL_HOT_COUNT, 'hot', lang).catch(() => [])
        const feed = await newsApi.fetchFeedPage({ limit: 30, lang })
        if (cancelled) return
        items = feed.items
        loading = false
        const hotItems = await hotPromise
        if (cancelled) return
        hot = hotItems
      } catch (e) {
        if (cancelled) return
        if (!untrack(() => items.length)) {
          error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        }
        loading = false
      }
    })()
    return () => {
      cancelled = true
    }
  })

  // Markets data is language-agnostic — load once. onMount, not $effect: the
  // effect form read `marketsLoaded` and then wrote it, so it invalidated
  // itself and only the guard stopped a re-run.
  onMount(() => {
    let cancelled = false
    void (async () => {
      const [priceRes, histRes] = await Promise.all([
        newsApi.fetchPrice().catch(() => null),
        newsApi.fetchPriceHistory().catch(() => null),
      ])
      if (cancelled) return
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
      cancelled = true
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
    <!-- Hero block: lead + seconds, with most-read as marginalia. -->
    <div class="editorial">
      <div class="main">
        <LeadStory article={lead} />

        {#if secondary.length}
          <hr class="hairline" />
          <div class="story-grid cols-2">
            {#each secondary as article}
              <StoryRow {article} dense />
            {/each}
          </div>
        {/if}
      </div>

      {#if railHot.length}
        <aside class="rail">
          <section class="rail-module">
            <h2 class="rail-head">{t($messages, 'hotTitle')}</h2>
            {#each railHot as article, i}
              <StoryRow {article} dense rank={i + 1} />
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

    <!-- The page's one change of register: full-bleed ink band. -->
    {#if price}
      <div class="bleed band numbers-band">
        <ByTheNumbers {price} {history} />
      </div>
    {/if}

    {#if more.length}
      <section class="feed-section">
        <SectionRule label={t($messages, 'newsFeedTitle')} href="/news" />
        <div class="stack">
          {#each more as article}
            <StoryRow {article} />
          {/each}
        </div>
        <button class="btn btn-outlined more" type="button" onclick={() => navigate('/news')}>
          {t($messages, 'navLatest')} →
        </button>
      </section>
    {/if}
  {/if}
</div>

<style>
  .front {
    gap: 20px;
  }
  /* Narrower than the hero block on purpose — a 1240px-wide list of rows
     reads as a spreadsheet. Varying measure is the point of the grid. */
  .feed-section {
    width: 100%;
    max-width: 860px;
    /* .page is a column flex container: without this the narrower feed
       column hugs the left edge of the 1240px page instead of centring. */
    margin-inline: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .numbers-band {
    margin-block: 8px;
  }
  .rail-more {
    margin-top: 12px;
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
    margin-top: 8px;
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
