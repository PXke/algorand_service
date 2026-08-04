<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi } from '../lib/api/news'
  import { messages, t, tPlural } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import SectionRule from '../components/SectionRule.svelte'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'
  import { displayTagLabel, isMetaTag, topicColor } from '../lib/tags'

  let tags: Array<{ tag: string; count: number; views?: number }> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  let query = $state('')

  /* One ranked column, not a four-column card grid. The grid encoded coverage
     as type size and reads as a background wash, which meant the eye had to
     decode two visual channels to answer "which topic is biggest" — and with
     ~30 cards across four columns there was no reading order at all. A list
     sorted by coverage answers it directly, and the numbers stay legible
     because they are printed rather than encoded. */
  const ranked = $derived.by(() => {
    const q = query.trim().toLowerCase()
    return tags
      .filter((x) => !q || x.tag.toLowerCase().includes(q) || displayTagLabel(x.tag).toLowerCase().includes(q))
      .slice()
      .sort((a, b) => (b.count ?? 0) - (a.count ?? 0) || (b.views ?? 0) - (a.views ?? 0))
      .map((x, i) => ({ ...x, rank: i + 1, tone: topicColor(x.tag), label: displayTagLabel(x.tag) }))
  })

  onMount(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        const res = await newsApi.fetchTags(ac.signal)
        if (ac.signal.aborted) return
        tags = res.tags.filter((x) => (x.count ?? 0) >= 2 && !isMetaTag(x.tag))
      } catch (e) {
        if (ac.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      } finally {
        if (!ac.signal.aborted) loading = false
      }
    })()
    return () => ac.abort()
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
    <label class="find">
      <span class="sr-only">{t($messages, 'navSearch')}</span>
      <input
        type="search"
        bind:value={query}
        placeholder={t($messages, 'navSearch')}
        autocomplete="off"
        spellcheck="false"
      />
    </label>

    {#if !ranked.length}
      <p class="muted">{t($messages, 'searchEmptyTitle')}</p>
    {:else}
      <ol class="rank-list">
        {#each ranked as item (item.tag)}
          <li>
            <a
              class="topic"
              style="--tone:{item.tone}"
              href={`/topic/${encodeURIComponent(item.tag)}`}
              onclick={(e) => {
                e.preventDefault()
                navigate(`/topic/${encodeURIComponent(item.tag)}`)
              }}
            >
              <span class="pos">{item.rank}</span>
              <strong class="name">{item.label}</strong>
              <span class="counts subtle">
                {tPlural($messages, 'storiesCount', item.count)}
                <span class="dot" aria-hidden="true">·</span>
                {tPlural($messages, 'readsCount', item.views ?? 0)}
              </span>
            </a>
          </li>
        {/each}
      </ol>
    {/if}
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
  .find {
    display: block;
    max-width: 320px;
  }
  .find input {
    width: 100%;
    padding: 9px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--panel);
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 13px;
  }
  .find input:focus-visible {
    outline: 2px solid var(--primary);
    outline-offset: 1px;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  .rank-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  /* Ruled rows, like the Most Read module — the rule is the structure, so the
     row itself needs no card chrome. */
  .topic {
    display: grid;
    grid-template-columns: 2.4rem 1fr auto;
    align-items: baseline;
    gap: 12px;
    padding: 11px 4px;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .topic:hover {
    text-decoration: none;
    background: var(--callout);
  }
  .topic:hover .name {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .pos {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 600;
    color: var(--subtle);
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  /* The tone stays a marker beside the name rather than a wash behind it:
     thirty tinted rows read as noise, one coloured tick reads as a label. */
  .name {
    font-family: var(--font-display);
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
    border-inline-start: 3px solid var(--tone);
    padding-inline-start: 10px;
    min-width: 0;
    overflow-wrap: anywhere;
  }
  .counts {
    font-family: var(--font-mono);
    font-size: 11.5px;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
  }
  .dot {
    color: var(--border);
  }
  @media (max-width: 519px) {
    .topic {
      grid-template-columns: 2rem 1fr;
      row-gap: 4px;
    }
    .counts {
      grid-column: 2;
    }
  }
  .err {
    color: var(--danger);
  }
</style>
