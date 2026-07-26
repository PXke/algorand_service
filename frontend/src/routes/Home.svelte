<script lang="ts">
  import { get } from 'svelte/store'
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
  let marketsLoaded = $state(false)

  const lead = $derived(items[0])
  const secondary = $derived(items.slice(1, 5))
  const more = $derived(items.slice(5, 18))

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
  $effect(() => {
    const lang = $activeLocale
    let cancelled = false
    loading = items.length === 0
    error = null
    void (async () => {
      try {
        const feed = await newsApi.fetchFeedPage({ limit: 30, lang })
        if (cancelled) return
        items = feed.items
        loading = false
        const hotItems = await newsApi.fetchHot(6, 'hot', lang).catch(() => [])
        if (cancelled) return
        hot = hotItems
      } catch (e) {
        if (cancelled) return
        if (!items.length) {
          error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        }
        loading = false
      }
    })()
    return () => {
      cancelled = true
    }
  })

  // Markets data is language-agnostic — load once.
  $effect(() => {
    if (marketsLoaded) return
    let cancelled = false
    void (async () => {
      const [priceRes, histRes] = await Promise.all([
        newsApi.fetchPrice().catch(() => null),
        newsApi.fetchPriceHistory().catch(() => null),
      ])
      if (cancelled) return
      marketsLoaded = true
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

<div class="page stack front">
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
    <LeadStory article={lead} />

    {#if secondary.length}
      <hr class="hairline" />
      <div class="story-grid cols-2">
        {#each secondary as article}
          <StoryRow {article} dense />
        {/each}
      </div>
    {/if}

    {#if price}
      <ByTheNumbers {price} {history} />
    {/if}

    {#if hot.length}
      <SectionRule label={t($messages, 'hotTitle')} href="/hot" />
      <div class="story-grid cols-2">
        {#each hot as article, i}
          <StoryRow {article} dense rank={i + 1} />
        {/each}
      </div>
    {/if}

    {#if more.length}
      <SectionRule label={t($messages, 'newsFeedTitle')} href="/news" />
      <div class="stack">
        {#each more as article}
          <StoryRow {article} />
        {/each}
      </div>
      <button class="btn btn-outlined more" type="button" onclick={() => navigate('/news')}>
        {t($messages, 'navLatest')} →
      </button>
    {/if}
  {/if}
</div>

<style>
  .front {
    gap: 20px;
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
