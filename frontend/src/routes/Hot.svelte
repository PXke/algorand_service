<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import StoryRow from '../components/StoryRow.svelte'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import { ogLocaleFor } from '../lib/seo'
  import { feedEnterIndex, markFeedEnterAll } from '../lib/motion'

  let { rank = 'hot' }: { rank?: 'hot' | 'top' } = $props()

  let items: ArticleItem[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let enterAt = $state<Map<string, number>>(new Map())

  $effect(() => {
    const lang = $activeLocale
    const r = rank
    const ac = new AbortController()
    loading = true
    error = null
    void (async () => {
      try {
        const next = await newsApi.fetchHot(30, r, lang, ac.signal)
        if (ac.signal.aborted) return
        items = next
        enterAt = markFeedEnterAll(next)
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

  const isTop = $derived(rank === 'top')
  const kicker = $derived(isTop ? t($messages, 'navTop') : t($messages, 'navHot'))
  const pageTitle = $derived(isTop ? t($messages, 'navTop') : t($messages, 'hotTitle'))
  const lead = $derived(isTop ? t($messages, 'hotTabAllTime') : t($messages, 'hotLead'))
  const ruleLabel = $derived(isTop ? t($messages, 'hotTabAllTime') : t($messages, 'hotTabHot'))
  const pagePath = $derived(isTop ? '/top' : '/hot')
</script>

<PageMeta title={pageTitle} description={lead} path={pagePath} ogLocale={ogLocaleFor($activeLocale)} />

<div class="page stack">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">{kicker}</p>
    <h1>{pageTitle}</h1>
    <p class="lead muted">{lead}</p>
  </header>

  {#if loading}
    <FeedSkeleton rows={10} />
  {:else if error}
    <p class="err">{error}</p>
  {:else if !items.length}
    <div class="empty">
      <h2>{t($messages, 'sectionEmptyTitle')}</h2>
      <p class="muted">{t($messages, 'sectionEmptyMessage')}</p>
      <div class="empty-actions">
        <button class="btn btn-primary" type="button" onclick={() => navigate('/news')}>
          {t($messages, 'emptyBrowseLatest')}
        </button>
        <button class="btn" type="button" onclick={() => navigate('/topics')}>
          {t($messages, 'emptyBrowseTopics')}
        </button>
      </div>
    </div>
  {:else}
    <SectionRule label={ruleLabel} />
    <!-- Same rows as /news, plus the rank column: the ledger is the feed
         re-sorted by read tally, not a different kind of page. -->
    <div class="feed">
      {#each items as article, i (article.article_id)}
        <StoryRow {article} rank={i + 1} enterIndex={feedEnterIndex(enterAt, article.article_id)} />
      {/each}
    </div>
  {/if}
</div>

<style>
  .feed :global(.row:last-child) {
    border-bottom: 0;
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
  .err {
    color: var(--danger);
  }
</style>
