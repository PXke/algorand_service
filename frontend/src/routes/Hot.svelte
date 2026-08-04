<script lang="ts">
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import StoryRow from '../components/StoryRow.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import FeedSkeleton from '../components/FeedSkeleton.svelte'
  import { ApiException } from '../lib/api/client'
  import { ogLocaleFor } from '../lib/seo'

  let { rank = 'hot' }: { rank?: 'hot' | 'top' } = $props()

  let items: ArticleItem[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

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

  const pageTitle = $derived(rank === 'top' ? t($messages, 'navTop') : t($messages, 'hotTitle'))
  const pagePath = $derived(rank === 'top' ? '/top' : '/hot')
</script>

<PageMeta
  title={pageTitle}
  description={rank === 'top' ? t($messages, 'hotTabAllTime') : t($messages, 'hotLead')}
  path={pagePath}
  ogLocale={ogLocaleFor($activeLocale)}
/>

<div class="page stack">
  <header>
    <span class="accent-slug"></span>
    <h1>{pageTitle}</h1>
    <p class="lead muted">
      {rank === 'top' ? t($messages, 'hotTabAllTime') : t($messages, 'hotLead')}
    </p>
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
    <div class="ledger">
      {#each items as article, i}
        <StoryRow {article} dense rank={i + 1} />
      {/each}
    </div>
  {/if}
</div>

<style>
  h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 42rem;
  }
  .ledger {
    display: flex;
    flex-direction: column;
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
