<script lang="ts">
  import { onMount } from 'svelte'
  import { ecosystemApi, ECOSYSTEM_CATEGORIES, type EcosystemEntry } from '../lib/api/ecosystem'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { LatestOnly } from '../lib/asyncGuard'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'

  let entries: EcosystemEntry[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)
  let query = $state('')
  let activeCategory = $state<string | null>(null)

  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase()
    return entries.filter(
      (e) =>
        !q ||
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        e.tags.some((tag) => tag.toLowerCase().includes(q)),
    )
  })

  const inflight = new LatestOnly()

  async function load() {
    const { signal, stale } = inflight.next()
    loading = true
    error = null
    try {
      const items = await ecosystemApi.fetchList({
        category: activeCategory ?? undefined,
        signal,
      })
      if (stale()) return
      entries = items
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
    } finally {
      if (!stale()) loading = false
    }
  }

  function selectCategory(category: string | null) {
    activeCategory = activeCategory === category ? null : category
    void load()
  }

  onMount(() => {
    void load()
  })
</script>

<PageMeta title="Algorand Open Registry" description={SITE_TAGLINE} path="/registry" />

<div class="page stack">
  <header class="compact-head">
    <p class="kicker">Registry</p>
    <h1>Algorand Open Registry</h1>
    <p class="lead muted">
      A free, human-reviewed directory of Algorand projects — wallets, DeFi, NFTs, developer
      tools and more.
    </p>
    <a
      class="btn btn-outlined submit-cta"
      href="/registry/submit"
      onclick={(e) => {
        e.preventDefault()
        navigate('/registry/submit')
      }}
    >
      Submit a project
    </a>
  </header>

  <nav class="chips" aria-label="Categories">
    <button type="button" class="chip" class:active={activeCategory === null} onclick={() => selectCategory(null)}>
      All
    </button>
    {#each ECOSYSTEM_CATEGORIES as category (category)}
      <button
        type="button"
        class="chip"
        class:active={activeCategory === category}
        onclick={() => selectCategory(category)}
      >
        {category}
      </button>
    {/each}
  </nav>

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

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="err">{error}</p>
  {:else if !filtered.length}
    <p class="muted">{t($messages, 'searchEmptyTitle')}</p>
  {:else}
    <p class="hit-count">
      {filtered.length}<span class="sep" aria-hidden="true">·</span>projects
    </p>
    <ul class="entry-list">
      {#each filtered as entry (entry.slug)}
        <li>
          <a
            class="entry"
            href={`/registry/${encodeURIComponent(entry.slug)}`}
            onclick={(e) => {
              e.preventDefault()
              navigate(`/registry/${encodeURIComponent(entry.slug)}`)
            }}
          >
            <span class="entry-head">
              <strong class="name">{entry.name}</strong>
              {#if entry.editor_pick}<span class="pick-badge">Editor's pick</span>{/if}
              <span class="live-dot" class:on={entry.reachable === true} class:off={entry.reachable === false} title={entry.reachable === false ? 'Currently unreachable' : 'Reachable'}></span>
            </span>
            <span class="def muted">{entry.description}</span>
            {#if entry.tags.length}
              <span class="tags">{entry.tags.join(' · ')}</span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  .submit-cta {
    margin-top: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    overflow-x: auto;
    padding-bottom: 2px;
  }
  .chip {
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--muted);
    font-size: 12px;
    font-weight: 600;
    padding: 6px 12px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .chip.active {
    background: var(--accent-soft);
    color: var(--primary);
    border-color: color-mix(in srgb, var(--primary) 35%, var(--border));
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
  .entry-list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .entry {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 14px 0;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .entry:hover {
    text-decoration: none;
    background: color-mix(in srgb, var(--accent) 6%, transparent);
    margin-inline: -10px;
    padding-inline: 10px;
  }
  .entry:hover .name {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .entry-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .name {
    font-family: var(--font-display);
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
  }
  .pick-badge {
    font-size: 10.5px;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 6px;
    background: color-mix(in srgb, var(--primary) 12%, var(--panel));
    color: var(--primary);
  }
  .live-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--subtle);
    flex-shrink: 0;
  }
  .live-dot.on {
    background: var(--gain);
  }
  .live-dot.off {
    background: var(--danger);
  }
  .def {
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.5;
  }
  .tags {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--subtle);
  }
  .err {
    color: var(--danger);
  }
</style>
