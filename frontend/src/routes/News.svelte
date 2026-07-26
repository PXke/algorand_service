<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
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

  async function load(reset: boolean, lang: string) {
    if (reset) {
      loading = true
      error = null
    } else loadingMore = true
    try {
      const page = await newsApi.fetchFeedPage({
        limit: 30,
        cursor: reset ? null : cursor,
        tag,
        lang,
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

  $effect(() => {
    const lang = $activeLocale
    const _tag = tag
    let cancelled = false
    void (async () => {
      loading = true
      error = null
      try {
        const page = await newsApi.fetchFeedPage({
          limit: 30,
          cursor: null,
          tag: _tag,
          lang,
        })
        if (cancelled) return
        items = page.items
        cursor = page.next_cursor
      } catch (e) {
        if (cancelled) return
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      } finally {
        if (!cancelled) loading = false
      }
    })()
    return () => {
      cancelled = true
    }
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
    <FeedSkeleton rows={8} />
  {:else if error}
    <p class="err">{error}</p>
    <button class="btn" type="button" onclick={() => load(true, $activeLocale)}>{t($messages, 'retry')}</button>
  {:else if items.length === 0}
    <div class="empty">
      <h2>{t($messages, 'newsEmptyTitle')}</h2>
      <p class="muted">{t($messages, 'newsEmptyMessage')}</p>
      <div class="empty-actions">
        <button class="btn btn-primary" type="button" onclick={() => navigate('/topics')}>
          {t($messages, 'emptyBrowseTopics')}
        </button>
        {#if tag}
          <button class="btn" type="button" onclick={() => navigate('/news')}>
            {t($messages, 'emptyBrowseLatest')}
          </button>
        {/if}
      </div>
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
      <button
        class="btn btn-outlined"
        type="button"
        disabled={loadingMore}
        onclick={() => load(false, $activeLocale)}
      >
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
  .empty {
    padding: 28px 0;
    text-align: start;
  }
  .empty h2 {
    margin: 0 0 8px;
    font-size: 1.35rem;
  }
  .empty-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 16px;
  }
  .feed :global(.row:last-child) {
    border-bottom: 0;
  }
  .err {
    color: var(--danger);
  }
</style>
