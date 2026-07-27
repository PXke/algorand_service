<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import LeadStory from '../components/LeadStory.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE, ogLocaleFor } from '../lib/seo'

  let { tag }: { tag: string } = $props()

  let items: ArticleItem[] = $state([])
  let cursor: string | null = $state(null)
  let loading = $state(true)
  let loadingMore = $state(false)
  let error = $state<string | null>(null)

  async function loadMore(lang: string) {
    if (!cursor || loadingMore) return
    loadingMore = true
    try {
      const page = await newsApi.fetchFeedPage({
        limit: 30,
        cursor,
        tag,
        lang,
      })
      items = [...items, ...page.items]
      cursor = page.next_cursor
    } catch (e) {
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
    } finally {
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

  const lead = $derived(items[0])
  const rest = $derived(items.slice(1))
  const pageTitle = $derived(`#${tag}`)
  const pagePath = $derived(`/topic/${encodeURIComponent(tag)}`)
  const intro = $derived(t($messages, 'topicHubLead', { tag }))
</script>

<PageMeta
  title={pageTitle}
  description={intro || SITE_TAGLINE}
  path={pagePath}
  ogLocale={ogLocaleFor($activeLocale)}
/>

<div class="page stack hub">
  <header>
    <span class="accent-slug"></span>
    <p class="eyebrow subtle">{t($messages, 'navTopics')}</p>
    <h1>{pageTitle}</h1>
    <p class="lead muted">{intro}</p>
  </header>

  {#if loading}
    <FeedSkeleton lead rows={6} />
  {:else if error}
    <p class="err">{error}</p>
    <button class="btn" type="button" onclick={() => navigate('/topics')}>
      {t($messages, 'emptyBrowseTopics')}
    </button>
  {:else if !lead}
    <div class="empty">
      <h2>{t($messages, 'sectionEmptyTitle')}</h2>
      <p class="muted">{t($messages, 'sectionEmptyMessage')}</p>
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

    {#if rest.length}
      <SectionRule label={t($messages, 'navLatest')} />
      <div class="feed">
        {#each rest as article}
          <StoryRow {article} />
        {/each}
      </div>
      {#if cursor}
        <button
          class="btn btn-outlined"
          type="button"
          disabled={loadingMore}
          onclick={() => loadMore($activeLocale)}
        >
          {t($messages, 'newsLoadMore')}
        </button>
      {/if}
    {/if}
  {/if}
</div>

<style>
  .hub {
    gap: 20px;
  }
  header {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .eyebrow {
    margin: 0;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.9px;
    text-transform: uppercase;
  }
  h1 {
    margin: 0;
    font-size: clamp(28px, 4vw, 36px);
    line-height: 1.12;
  }
  .lead {
    margin: 0;
    max-width: 40rem;
    font-size: 1.05rem;
    line-height: 1.55;
  }
  .feed :global(.row:last-child) {
    border-bottom: 0;
  }
  .feed + .btn {
    margin-top: 8px;
  }
  @media (max-width: 519px) {
    .feed + .btn {
      width: 100%;
    }
  }
  .empty {
    padding: 12px 0 28px;
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
  .err {
    color: var(--danger);
  }
</style>
