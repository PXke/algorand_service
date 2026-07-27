<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi } from '../lib/api/news'
  import { messages, t, tPlural } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'
  import { isMetaTag, topicColor } from '../lib/tags'

  let tags: Array<{ tag: string; count: number; views?: number }> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  /* The page has always promised "sized by coverage, warmed by reads" and
     always rendered identical cards. Actually derive both now. */
  const scaled = $derived.by(() => {
    const maxCount = Math.max(...tags.map((x) => x.count ?? 0), 1)
    const maxViews = Math.max(...tags.map((x) => x.views ?? 0), 1)
    return tags.map((x) => {
      // sqrt keeps the long tail legible instead of collapsing it to nothing
      const size = Math.sqrt((x.count ?? 0) / maxCount)
      const heat = Math.sqrt((x.views ?? 0) / maxViews)
      return {
        ...x,
        tone: topicColor(x.tag),
        // 1rem → 1.6rem
        fontSize: `${(1 + size * 0.6).toFixed(2)}rem`,
        // wide topics claim two columns
        wide: size > 0.62,
        // 0 → 9% tone wash; the CSS applies this as a percentage directly
        heat: (heat * 9).toFixed(1),
      }
    })
  })

  onMount(() => {
    void (async () => {
      try {
        const res = await newsApi.fetchTags()
        tags = res.tags.filter((x) => (x.count ?? 0) >= 2 && !isMetaTag(x.tag))
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
      {#each scaled as item}
        <a
          class="topic"
          class:wide={item.wide}
          style="--tone:{item.tone}; --heat:{item.heat}; --size:{item.fontSize}"
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
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
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
    justify-content: space-between;
    gap: 8px;
    min-height: 84px;
    padding: 14px;
    /* --heat is the read-warmth the page copy promises, already in percent.
       Kept to a whisper — a grid of thirty saturated cards read as a circus. */
    background: color-mix(in srgb, var(--tone) calc(var(--heat, 0) * 1%), var(--panel));
    border: 1px solid var(--border);
    border-inline-start: 3px solid color-mix(in srgb, var(--tone) 55%, var(--border));
    border-radius: 12px;
    color: inherit;
    text-decoration: none;
    transition:
      border-color 0.2s ease,
      transform 0.25s cubic-bezier(0.22, 1, 0.36, 1),
      box-shadow 0.25s ease;
  }
  .topic.wide {
    grid-column: span 2;
  }
  @media (max-width: 499px) {
    .topic.wide {
      grid-column: span 1;
    }
  }
  .topic:hover {
    text-decoration: none;
    border-color: color-mix(in srgb, var(--tone) 45%, var(--border));
    border-inline-start-color: var(--tone);
    transform: translateY(-2px);
    box-shadow: 0 10px 24px var(--card-shadow);
  }
  /* Sans, not display serif: these are lowercase #hashtag labels, and a
     serif "#" at thirty different sizes looked ragged. Tone stays in the
     rule and the wash so the type itself can be plain ink. */
  .topic strong {
    font-family: var(--font-sans);
    /* --size is the coverage weighting. */
    font-size: var(--size, 1.05rem);
    font-weight: 600;
    line-height: 1.15;
    letter-spacing: -0.2px;
    color: var(--on-surface);
  }
  .topic:hover strong {
    color: var(--tone);
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
