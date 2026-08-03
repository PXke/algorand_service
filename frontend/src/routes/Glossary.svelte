<script lang="ts">
  import { onMount } from 'svelte'
  import { glossaryApi, type GlossaryTerm } from '../lib/api/glossary'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import SectionRule from '../components/SectionRule.svelte'
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
      .filter((t) => !q || t.term.toLowerCase().includes(q) || t.definition.toLowerCase().includes(q))
      .slice()
      .sort((a, b) => a.term.localeCompare(b.term))
  })

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
    <h1>Glossary</h1>
    <p class="lead muted">Plain-language definitions for terms used across our coverage.</p>
  </header>
  <SectionRule label="Glossary" />

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

    {#if !filtered.length}
      <p class="muted">{t($messages, 'searchEmptyTitle')}</p>
    {:else}
      <ul class="term-list">
        {#each filtered as item (item.slug)}
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
  .term-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .term {
    display: block;
    padding: 12px 4px;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .term:hover {
    background: var(--callout);
  }
  .term:hover .name {
    text-decoration: underline;
    text-underline-offset: 2px;
  }
  .name {
    display: block;
    font-family: var(--font-display);
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
  }
  .def {
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    font-size: 0.9rem;
    margin-top: 4px;
  }
  .err {
    color: var(--danger);
  }
</style>
