<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE, ogLocaleFor } from '../lib/seo'

  let {
    tag = undefined,
    title = undefined,
  }: { tag?: string; title?: string } = $props()

  let items: ArticleItem[] = $state([])
  let cursor: string | null = $state(null)
  let loading = $state(true)
  let loadingMore = $state(false)
  let error = $state<string | null>(null)

  async function load(reset: boolean) {
    if (reset) {
      loading = true
      error = null
    } else loadingMore = true
    try {
      const page = await newsApi.fetchFeedPage({
        limit: 30,
        cursor: reset ? null : cursor,
        tag,
        lang: $activeLocale,
      })
      items = reset ? page.items : [...items, ...page.items]
      cursor = page.next_cursor
    } catch (e) {
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
    } finally {
      loading = false
      loadingMore = false
    }
  }

  onMount(() => {
    void load(true)
  })
  const pageTitle = $derived(title ?? t($messages, 'pageTitleNews'))
  const pagePath = $derived(tag ? `/topic/${encodeURIComponent(tag)}` : '/news')
</script>

<PageMeta
  title={pageTitle}
  description={t($messages, 'homeNewsDescription') || SITE_TAGLINE}
  path={pagePath}
  ogLocale={ogLocaleFor($activeLocale)}
/>

<div class="page stack">
  <header>
    <span class="accent-slug"></span>
    <h1>{title ?? t($messages, 'newsFeedTitle')}</h1>
    <p class="muted">{t($messages, 'newsSubtitleDefault')}</p>
  </header>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
    <button class="btn" type="button" onclick={() => load(true)}>{t($messages, 'retry')}</button>
  {:else if items.length === 0}
    <div class="empty">
      <h2>{t($messages, 'newsEmptyTitle')}</h2>
      <p class="muted">{t($messages, 'newsEmptyMessage')}</p>
    </div>
  {:else}
    {#if !tag}
      <SectionRule label={t($messages, 'navLatest')} />
    {/if}
    <div class="feed">
      {#each items as article}
        <StoryRow {article} />
      {/each}
    </div>
    {#if cursor}
      <button class="btn btn-outlined" type="button" disabled={loadingMore} onclick={() => load(false)}>
        {t($messages, 'newsLoadMore')}
      </button>
    {/if}
  {/if}
</div>

<style>
  header {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  h1 {
    margin: 0;
    font-size: clamp(28px, 4vw, 34px);
    line-height: 1.15;
  }
  .feed :global(.row:last-child) {
    border-bottom: 0;
  }
  .err {
    color: var(--danger);
  }
</style>
