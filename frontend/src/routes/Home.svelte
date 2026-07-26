<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import LeadStory from '../components/LeadStory.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import ByTheNumbers from '../components/ByTheNumbers.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_NAME, SITE_TAGLINE, ogLocaleFor } from '../lib/seo'

  let items: ArticleItem[] = $state([])
  let hot: ArticleItem[] = $state([])
  let price = $state<Record<string, unknown> | null>(null)
  let history: Array<{ epoch: number; price: number }> = $state([])
  let error = $state<string | null>(null)
  let loading = $state(true)

  const lead = $derived(items[0])
  const secondary = $derived(items.slice(1, 5))
  const more = $derived(items.slice(5, 18))

  onMount(() => {
    void (async () => {
      try {
        const feed = await newsApi.fetchFeedPage({ limit: 30, lang: $activeLocale })
        items = feed.items
        loading = false
        const [hotItems, priceRes, histRes] = await Promise.all([
          newsApi.fetchHot(6, 'hot', $activeLocale).catch(() => []),
          newsApi.fetchPrice().catch(() => null),
          newsApi.fetchPriceHistory().catch(() => null),
        ])
        hot = hotItems
        if (priceRes?.available) price = priceRes
        const pts = Array.isArray(histRes?.points) ? histRes.points : []
        history = pts
          .map((p: Record<string, unknown>) => ({
            epoch: Number(p.epoch ?? 0),
            price: Number(p.price_usd ?? 0),
          }))
          .filter((p: { epoch: number; price: number }) => p.epoch > 0 && p.price > 0)
      } catch (e) {
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
        loading = false
      }
    })()
  })
</script>

<PageMeta
  title={SITE_NAME}
  description={t($messages, 'appTagline') || SITE_TAGLINE}
  path="/"
  ogLocale={ogLocaleFor($activeLocale)}
/>

<div class="page stack front">
  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !lead}
    <p class="muted">{t($messages, 'newsEmptyTitle')}</p>
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
  .err {
    color: var(--danger);
  }
</style>
