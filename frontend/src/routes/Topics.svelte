<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi } from '../lib/api/news'
  import { messages, t, tPlural } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'

  let tags: Array<{ tag: string; count: number; views?: number }> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  onMount(() => {
    void (async () => {
      try {
        const res = await newsApi.fetchTags()
        tags = res.tags.filter((x) => (x.count ?? 0) >= 2)
      } catch (e) {
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      } finally {
        loading = false
      }
    })()
  })
</script>

<PageMeta
  title={t($messages, 'topicsTitle')}
  description={t($messages, 'topicsLead') || SITE_TAGLINE}
  path="/topics"
/>

<div class="page stack">
  <header>
    <span class="accent-slug"></span>
    <h1>{t($messages, 'topicsTitle')}</h1>
    <p class="lead muted">{t($messages, 'topicsLead')}</p>
  </header>
  <SectionRule label={t($messages, 'navTopics')} />

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else}
    <div class="topics">
      {#each tags as item}
        <a
          class="topic"
          href={`/topic/${encodeURIComponent(item.tag)}`}
          onclick={(e) => {
            e.preventDefault()
            navigate(`/topic/${encodeURIComponent(item.tag)}`)
          }}
        >
          <strong>#{item.tag}</strong>
          <span class="subtle"
            >{tPlural($messages, 'storiesCount', item.count)} · {tPlural(
              $messages,
              'readsCount',
              item.views ?? 0,
            )}</span
          >
        </a>
      {/each}
    </div>
  {/if}
</div>

<style>
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 42rem;
  }
  .topics {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 10px;
  }
  @media (min-width: 500px) {
    .topics {
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
    }
  }
  .topic {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    padding: 14px 16px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    color: inherit;
    text-decoration: none;
    font-weight: 600;
  }
  .topic .subtle {
    font-size: 12px;
    font-weight: 500;
  }
  .topic:hover {
    border-color: color-mix(in srgb, var(--primary) 45%, var(--border));
    box-shadow: 0 8px 18px var(--card-hover-shadow);
    text-decoration: none;
  }
  .err {
    color: var(--danger);
  }
</style>
