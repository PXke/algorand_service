<script lang="ts">
  import { onMount } from 'svelte'
  import { newsApi } from '../lib/api/news'
  import { messages, t, tPlural } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'
  import {
    displayTagLabel,
    isMetaTag,
    readerDesk,
    deskMessageKey,
    type ReaderDesk,
  } from '../lib/tags'
  import { staggerMs } from '../lib/motion'

  let tags: Array<{ tag: string; count: number; views?: number }> = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  let query = $state('')

  type Beat = {
    tag: string
    count: number
    views: number
    label: string
  }

  const desks = $derived.by(() => {
    const q = query.trim().toLowerCase()
    const beats: Beat[] = tags
      .filter(
        (x) =>
          !q ||
          x.tag.toLowerCase().includes(q) ||
          displayTagLabel(x.tag).toLowerCase().includes(q),
      )
      .map((x) => ({
        tag: x.tag,
        count: x.count ?? 0,
        views: x.views ?? 0,
        label: displayTagLabel(x.tag),
      }))
      .sort((a, b) => b.count - a.count || b.views - a.views)

    const buckets: Record<ReaderDesk, Beat[]> = {
      markets: [],
      protocol: [],
      assets: [],
      people: [],
      wire: [],
    }
    for (const beat of beats) {
      buckets[readerDesk([beat.tag])].push(beat)
    }
    const columns = (['markets', 'assets', 'protocol', 'people'] as ReaderDesk[])
      .filter((id) => buckets[id].length > 0)
      .map((id) => ({ id, items: buckets[id] }))
    return { columns, extra: buckets.wire }
  })

  function deskLabel(id: ReaderDesk): string {
    return t($messages, deskMessageKey(id))
  }

  function goTopic(tag: string) {
    navigate(`/topic/${encodeURIComponent(tag)}`)
  }

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

<div class="page page-wide stack index">
  {#snippet beatList(items: Beat[])}
    {#key query}
      <ol class="beats">
        {#each items as item, i (item.tag)}
          <li style="--enter-delay: {staggerMs(i)}ms">
            <a
              class="beat enter"
            href={`/topic/${encodeURIComponent(item.tag)}`}
            onclick={(e) => {
              e.preventDefault()
              goTopic(item.tag)
            }}
          >
            <strong class="name">{item.label}</strong>
            <span class="counts">
              {tPlural($messages, 'storiesCount', item.count)}
              <span class="dot" aria-hidden="true">·</span>
              {tPlural($messages, 'readsCount', item.views)}
            </span>
          </a>
        </li>
      {/each}
    </ol>
    {/key}
  {/snippet}

  <header class="edition-head folio">
    <div class="copy">
      <p class="kicker">{t($messages, 'desksKicker')}</p>
      <h1 class="edition-date">{t($messages, 'topicsTitle')}</h1>
      <p class="lead muted">{t($messages, 'topicsLead')}</p>
    </div>
    {#if !loading && !error}
      <label class="find">
        <span class="sr-only">{t($messages, 'navSearch')}</span>
        <span class="query-shell">
          <span class="query-prompt" aria-hidden="true">›</span>
          <input
            type="search"
            bind:value={query}
            placeholder={t($messages, 'navSearch')}
            autocomplete="off"
            spellcheck="false"
          />
        </span>
      </label>
    {/if}
  </header>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !desks.columns.length && !desks.extra.length}
    <p class="muted">{t($messages, 'searchEmptyTitle')}</p>
  {:else}
    {#if desks.columns.length}
      <div class="desks">
        {#each desks.columns as desk (desk.id)}
          <section class="desk">
            <h2 class="desk-head">{deskLabel(desk.id)}</h2>
            {@render beatList(desk.items)}
          </section>
        {/each}
      </div>
    {/if}
    {#if desks.extra.length}
      <section class="also">
        <h2 class="desk-head">{t($messages, 'deskWire')}</h2>
        {@render beatList(desks.extra)}
      </section>
    {/if}
  {/if}
</div>

<style>
  .index {
    gap: 28px;
  }
  .folio {
    gap: 16px;
  }
  @media (min-width: 700px) {
    .folio {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(220px, 280px);
      align-items: end;
      column-gap: 32px;
    }
  }
  .copy {
    display: flex;
    flex-direction: column;
    gap: 6px;
    min-width: 0;
  }
  .lead {
    margin: 2px 0 0;
    max-width: 42rem;
  }
  .find {
    display: block;
    width: 100%;
    max-width: none;
  }
  .query-shell {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: 0;
    background: var(--surface);
  }
  .query-shell:focus-within {
    border-color: var(--accent);
  }
  .query-prompt {
    font-family: var(--font-mono);
    font-size: 16px;
    font-weight: 600;
    color: var(--accent);
    line-height: 1;
  }
  .find input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: transparent;
    color: var(--on-surface);
    font-family: var(--font-mono);
    font-size: 13px;
    padding: 10px 0;
    outline: none;
  }
  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
  /* Two newspaper columns — the global 4-col crumples beat names into
     274px towers and stretches short desks into empty wells. */
  .desks {
    grid-template-columns: 1fr;
    gap: 32px 0;
    align-items: start;
  }
  @media (min-width: 700px) {
    .desks {
      grid-template-columns: repeat(2, minmax(0, 1fr));
      column-gap: 0;
    }
    .desks > :global(.desk:not(:last-child)) {
      border-inline-end: 0;
      padding-inline-end: 0;
      margin-inline-end: 0;
    }
    .desks > :global(.desk:nth-child(odd)) {
      border-inline-end: 1px solid var(--border);
      padding-inline-end: 28px;
      margin-inline-end: 28px;
    }
  }
  @media (min-width: 1080px) {
    .desks {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
  .beats {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .beat {
    display: flex;
    flex-direction: row;
    align-items: baseline;
    justify-content: space-between;
    gap: 16px;
    padding: 9px 0;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  @media (prefers-reduced-motion: no-preference) {
    .beat.enter {
      animation: rise-in 0.36s cubic-bezier(0.22, 1, 0.36, 1) both;
      animation-delay: var(--enter-delay, 0ms);
    }
  }
  .beat:hover {
    text-decoration: none;
  }
  .beat:hover .name {
    color: var(--accent);
  }
  .desk .beats li:last-child .beat {
    border-bottom: 0;
  }
  .name {
    font-family: var(--font-display);
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.2px;
    min-width: 0;
  }
  .counts {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .dot {
    margin-inline: 4px;
    color: var(--subtle);
  }
  .also {
    width: 100%;
  }
  @media (min-width: 700px) {
    .also .beats {
      display: grid;
      grid-template-columns: 1fr 1fr;
      column-gap: 36px;
    }
    .also .beats li:nth-last-child(-n + 2) .beat {
      border-bottom: 0;
    }
  }
  .err {
    color: var(--danger);
  }
</style>
