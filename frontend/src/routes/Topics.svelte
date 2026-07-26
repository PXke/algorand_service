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
    gap: 12px;
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
    gap: 8px;
    padding: 16px 16px 14px;
    background:
      linear-gradient(
        160deg,
        color-mix(in srgb, var(--accent) 8%, var(--panel)) 0%,
        var(--panel) 48%
      );
    border: 1px solid var(--border);
    border-radius: 12px;
    color: inherit;
    text-decoration: none;
    transition:
      border-color 0.2s ease,
      transform 0.25s cubic-bezier(0.22, 1, 0.36, 1),
      box-shadow 0.25s ease;
  }
  .topic:hover {
    text-decoration: none;
    border-color: color-mix(in srgb, var(--accent) 40%, var(--border));
    transform: translateY(-2px);
    box-shadow: 0 10px 24px var(--card-shadow);
  }
  .topic strong {
    font-family: var(--font-display);
    font-size: 1.05rem;
    letter-spacing: -0.2px;
  }
  .topic .subtle {
    font-size: 0.82rem;
    line-height: 1.35;
  }
  @media (prefers-reduced-motion: reduce) {
    .topic {
      transition: none;
    }
    .topic:hover {
      transform: none;
    }
  }
  .err {
    color: var(--danger);
  }
</style>
