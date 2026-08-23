<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import LeadStory from '../components/LeadStory.svelte'
  import StoryRow from '../components/StoryRow.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE, ogLocaleFor } from '../lib/seo'
  import { displayTagLabel, topicColor, readerDesk, deskMessageKey } from '../lib/tags'
  import { feedEnterIndex, markFeedEnter, markFeedEnterAll } from '../lib/motion'

  let { tag }: { tag: string } = $props()

  let items: ArticleItem[] = $state([])
  let cursor: string | null = $state(null)
  let loading = $state(true)
  let loadingMore = $state(false)
  let error = $state<string | null>(null)
  let enterAt = $state<Map<string, number>>(new Map())

  async function loadMore(lang: string) {
    if (!cursor || loadingMore) return
    loadingMore = true
    const prevLen = items.length
    try {
      const page = await newsApi.fetchFeedPage({
        limit: 30,
        cursor,
        tag,
        lang,
      })
      items = [...items, ...page.items]
      enterAt = markFeedEnter(items, prevLen)
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
    const ac = new AbortController()
    void (async () => {
      loading = true
      error = null
      try {
        const page = await newsApi.fetchFeedPage({
          limit: 30,
          cursor: null,
          tag: _tag,
          lang,
          signal: ac.signal,
        })
        if (ac.signal.aborted) return
        items = page.items
        enterAt = markFeedEnterAll(page.items)
        cursor = page.next_cursor
      } catch (e) {
        if (ac.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      } finally {
        if (!ac.signal.aborted) loading = false
      }
    })()
    return () => {
      ac.abort()
    }
  })

  const lead = $derived(items[0])
  const rest = $derived(items.slice(1))
  const label = $derived(displayTagLabel(tag))
  const tone = $derived(topicColor(tag))
  const deskId = $derived(readerDesk([tag]))
  const deskLabel = $derived(t($messages, deskMessageKey(deskId)))
  const pageTitle = $derived(label)
  const pagePath = $derived(`/topic/${encodeURIComponent(tag)}`)
  const intro = $derived(t($messages, 'topicHubLead', { tag: label }))
</script>

<PageMeta
  title={pageTitle}
  description={intro || SITE_TAGLINE}
  path={pagePath}
  ogLocale={ogLocaleFor($activeLocale)}
/>

<div class="page stack hub">
  <header class="edition-head" style="--tone:{tone}">
    <p class="kicker">{deskLabel}</p>
    <h1 class="edition-date">{label}</h1>
    <p class="folio wire-stamp">
      <a
        href="/topics"
        onclick={(e) => {
          e.preventDefault()
          navigate('/topics')
        }}>{t($messages, 'navTopics')}</a
      >
    </p>
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
      <h2 class="desk-head">{t($messages, 'navLatest')}</h2>
      <div class="feed">
        {#each rest as article (article.article_id)}
          <StoryRow
            {article}
            dense
            enterIndex={feedEnterIndex(enterAt, article.article_id)}
          />
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
  .folio a {
    color: var(--accent);
    text-decoration: none;
  }
  .folio a:hover {
    text-decoration: underline;
    text-underline-offset: 2px;
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
