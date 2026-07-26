<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi, type ArticleItem } from '../lib/api/news'
  import { messages, t, activeLocale } from '../lib/i18n'
  import StoryRow from '../components/StoryRow.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { ogLocaleFor } from '../lib/seo'

  let { rank = 'hot' }: { rank?: 'hot' | 'top' } = $props()

  let items: ArticleItem[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  async function load() {
    loading = true
    error = null
    try {
      items = await newsApi.fetchHot(30, rank, $activeLocale)
    } catch (e) {
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
    } finally {
      loading = false
    }
  }

  onMount(() => {
    void load()
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
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !items.length}
    <p class="muted">{t($messages, 'sectionEmptyMessage')}</p>
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
  .err {
    color: var(--danger);
  }
</style>
