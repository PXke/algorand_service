<script lang="ts">
  import { onMount } from 'svelte'
  import { glossaryApi, type GlossaryTerm } from '../lib/api/glossary'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'

  let terms: GlossaryTerm[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let query = $state('')

  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase()
    return terms
      .filter(
        (t) =>
          !q ||
          t.term.toLowerCase().includes(q) ||
          t.definition.toLowerCase().includes(q) ||
          (t.aliases ?? []).some((alias) => alias.toLowerCase().includes(q)),
      )
      .slice()
      .sort((a, b) => a.term.localeCompare(b.term))
  })

  const grouped = $derived.by(() => {
    const buckets: Record<string, GlossaryTerm[]> = {}
    const order: string[] = []
    for (const item of filtered) {
      const raw = (item.term.trim()[0] ?? '#').toUpperCase()
      const key = /[A-Z]/.test(raw) ? raw : '#'
      if (!buckets[key]) {
        buckets[key] = []
        order.push(key)
      }
      buckets[key].push(item)
    }
    return order.map((letter) => [letter, buckets[letter]] as [string, GlossaryTerm[]])
  })
  const letters = $derived(grouped.map(([letter]) => letter))

  onMount(() => {
    void (async () => {
      try {
        terms = await glossaryApi.fetchList()
      } catch (e) {
        error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
      } finally {
        loading = false
      }
    })()
  })
</script>

<PageMeta
  title="Glossary"
  description={SITE_TAGLINE}
  path="/glossary"
/>

<div class="page stack">
  <header>
    <span class="accent-slug"></span>
    <p class="kicker">Glossary</p>
    <h1>Glossary</h1>
    <p class="lead muted">Plain-language definitions for terms used across our coverage.</p>
  </header>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else}
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

    {#if !filtered.length}
      <p class="muted">{t($messages, 'searchEmptyTitle')}</p>
    {:else}
      <p class="hit-count">
        {filtered.length}<span class="sep" aria-hidden="true">·</span>terms
      </p>
      {#if letters.length > 1}
        <nav class="az" aria-label="Alphabet">
          {#each letters as letter (letter)}
            <a href="#letter-{letter}">{letter}</a>
          {/each}
        </nav>
      {/if}
      {#each grouped as [letter, items] (letter)}
        <h2 class="letter" id="letter-{letter}">{letter}</h2>
        <ul class="term-list">
          {#each items as item (item.slug)}
            <li>
              <a
                class="term"
                href={`/glossary/${encodeURIComponent(item.slug)}`}
                onclick={(e) => {
                  e.preventDefault()
                  navigate(`/glossary/${encodeURIComponent(item.slug)}`)
                }}
              >
                <strong class="name">{item.term}</strong>
                <span class="def muted">{item.definition}</span>
              </a>
            </li>
          {/each}
        </ul>
      {/each}
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
    max-width: 420px;
  }
  .query-shell {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 44px;
    padding: 0 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
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
  .hit-count {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.7px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .hit-count .sep {
    margin-inline: 6px;
    color: var(--subtle);
  }
  .az {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 0;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }
  .az a {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.4px;
    color: var(--muted);
    text-decoration: none;
    min-width: 1.6em;
    text-align: center;
    padding: 4px 2px;
  }
  .az a:hover {
    color: var(--accent);
    text-decoration: none;
  }
  .letter {
    margin: 22px 0 0;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 1.2px;
    color: var(--accent);
    scroll-margin-top: 88px;
  }
  .letter::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 8%;
  }
  .term-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .term {
    display: grid;
    grid-template-columns: minmax(10ch, 16rem) minmax(0, 1fr);
    gap: 16px 28px;
    align-items: baseline;
    padding: 12px 0;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .term:hover {
    text-decoration: none;
    background: color-mix(in srgb, var(--accent) 6%, transparent);
    margin-inline: -10px;
    padding-inline: 10px;
  }
  .term:hover .name {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .name {
    font-family: var(--font-display);
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
  }
  .def {
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  @media (max-width: 639px) {
    .term {
      grid-template-columns: 1fr;
      gap: 4px;
    }
  }
  .err {
    color: var(--danger);
  }
</style>
