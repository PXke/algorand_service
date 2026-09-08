<script lang="ts">
  import {
    ecosystemApi,
    ecosystemCategoryLabel,
    stripMarkdownToText,
    ECOSYSTEM_CATEGORIES,
    type EcosystemEntry,
  } from '../lib/api/ecosystem'
  import { messages, t } from '../lib/i18n'
  import { navigate, route } from '../lib/router'
  import { LatestOnly } from '../lib/asyncGuard'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'
  import { SITE_TAGLINE } from '../lib/seo'

  // Fetched ONCE, unfiltered -- category/tag/text filtering all happen
  // client-side below. This is what makes "hide empty categories" and a
  // combined category+tag+text filter possible without round-tripping the
  // server per facet. Caps out at the server's own ecosystem_list_max_results
  // (100) -- fine at the registry's current size, but the real fix once it
  // grows past that is server-side (Typesense) search, not raising this
  // limit further.
  let entries: EcosystemEntry[] = $state([])
  let loading = $state(true)
  let error: string | null = $state(null)
  let query = $state('')

  // Category and tag both live in the URL (?category=slug&tag=slug) so a
  // filtered view is a shareable link and the entry page's category link
  // still lands on one.
  const knownCategories = new Set<string>(ECOSYSTEM_CATEGORIES)
  const activeCategory = $derived.by(() => {
    const raw = $route.query.get('category') ?? ''
    return knownCategories.has(raw) ? raw : null
  })
  const activeTag = $derived($route.query.get('tag') || null)

  function withQuery(overrides: { category?: string | null; tag?: string | null }): string {
    const category = 'category' in overrides ? overrides.category : activeCategory
    const tag = 'tag' in overrides ? overrides.tag : activeTag
    const params = new URLSearchParams()
    if (category) params.set('category', category)
    if (tag) params.set('tag', tag)
    const qs = params.toString()
    return qs ? `/registry?${qs}` : '/registry'
  }

  function selectCategory(category: string | null) {
    if (category === activeCategory) return
    // Changing category clears the tag filter -- a tag scoped to the old
    // category may not even apply to entries in the new one.
    navigate(withQuery({ category, tag: null }), false, false)
  }

  function selectTag(tag: string | null) {
    navigate(withQuery({ tag: tag === activeTag ? null : tag }), false, false)
  }

  // Entries within the active category (or all, if none) -- the base every
  // other facet (tags, then text search) narrows further.
  const inCategory = $derived.by(() =>
    activeCategory ? entries.filter((e) => e.category === activeCategory) : entries,
  )

  // Only categories that actually have at least one entry are worth showing
  // in the rail -- an "NFTs" entry that leads to an empty page is dead
  // weight, not a real choice.
  const categoryOptions = $derived.by(() => {
    const counts = new Map<string, number>()
    for (const e of entries) counts.set(e.category, (counts.get(e.category) ?? 0) + 1)
    return ECOSYSTEM_CATEGORIES.filter((c) => (counts.get(c) ?? 0) > 0).map((c) => ({
      slug: c,
      label: ecosystemCategoryLabel(c),
      count: counts.get(c) ?? 0,
    }))
  })

  // Tags are scoped to the active category (switching category reshuffles
  // which tags make sense), but not to the text search -- search stays an
  // orthogonal, always-available refinement.
  const tagOptions = $derived.by(() => {
    const counts = new Map<string, number>()
    for (const e of inCategory) for (const tag of e.tags) counts.set(tag, (counts.get(tag) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  })

  // If the active tag no longer applies under the current category (e.g. a
  // direct link to an incompatible category+tag pair), fall through to the
  // unfiltered-by-tag view rather than showing a confusing always-empty page.
  const tagIsLive = $derived(!activeTag || tagOptions.some(([tag]) => tag === activeTag))

  const byTag = $derived.by(() =>
    activeTag && tagIsLive ? inCategory.filter((e) => e.tags.includes(activeTag)) : inCategory,
  )

  const filtered = $derived.by(() => {
    const q = query.trim().toLowerCase()
    if (!q) return byTag
    return byTag.filter(
      (e) =>
        e.name.toLowerCase().includes(q) ||
        e.description.toLowerCase().includes(q) ||
        e.tags.some((tag) => tag.toLowerCase().includes(q)),
    )
  })

  const heading = $derived.by(() => {
    const parts: string[] = []
    parts.push(activeCategory ? ecosystemCategoryLabel(activeCategory) : 'All projects')
    if (activeTag && tagIsLive) parts.push(`#${activeTag}`)
    return parts.join(' • ')
  })

  const inflight = new LatestOnly()

  async function load() {
    const { signal, stale } = inflight.next()
    loading = true
    error = null
    try {
      const items = await ecosystemApi.fetchList({ signal })
      if (stale()) return
      entries = items
    } catch (e) {
      if (stale() || (e instanceof DOMException && e.name === 'AbortError')) return
      error = e instanceof ApiException ? e.userMessage : t($messages, 'errorGeneric')
    } finally {
      if (!stale()) loading = false
    }
  }

  void load()
</script>

<PageMeta title="Algorand Open Registry" description={SITE_TAGLINE} path="/registry" />

<div class="page page-wide registry">
  <h1 class="sr-only">Algorand Open Registry</h1>

  <div class="layout">
    <!-- Floating, not boxed: no border, no fixed track width beyond a cap --
         this is the "big list of categories" from the original design,
         brought back per owner feedback (2026-09-08), but living in the
         page's own left gutter instead of a bordered sidebar that ate into
         the reading measure. -->
    <aside class="categories-rail" aria-label="Categories">
      <ul>
        <li>
          <button type="button" class:active={!activeCategory} onclick={() => selectCategory(null)}>
            <span>All projects</span>
            <span class="cat-count">{entries.length}</span>
          </button>
        </li>
        {#each categoryOptions as opt (opt.slug)}
          <li>
            <button
              type="button"
              class:active={activeCategory === opt.slug}
              onclick={() => selectCategory(opt.slug)}
            >
              <span>{opt.label}</span>
              <span class="cat-count">{opt.count}</span>
            </button>
          </li>
        {/each}
      </ul>
    </aside>

    <!-- The reading column: capped at 700px and centered on the page by the
         layout grid's equal-width outer tracks (categories on the left,
         nothing on the right) -- not by centering this column in isolation. -->
    <div class="main">
      <div class="toolbar">
        <label class="search">
          <span class="sr-only">{t($messages, 'navSearch')}</span>
          <input
            type="search"
            bind:value={query}
            placeholder="Search by name, description or tag"
            autocomplete="off"
            spellcheck="false"
          />
        </label>
      </div>

      {#if tagOptions.length}
        <div class="tag-row" aria-label="Tags">
          {#each tagOptions as [tag, count] (tag)}
            <button
              type="button"
              class="tag-btn"
              class:active={activeTag === tag && tagIsLive}
              onclick={() => selectTag(tag)}
            >
              #{tag}
              <span class="tag-count">{count}</span>
            </button>
          {/each}
        </div>
      {/if}

      <section class="results" aria-labelledby="results-heading">
        <div class="results-head">
          <h2 id="results-heading">{heading}</h2>
          {#if !loading && !error && filtered.length}
            <span class="count" aria-live="polite">
              {filtered.length === 1 ? '1 project' : `${filtered.length} projects`}
            </span>
          {/if}
        </div>

        {#if loading}
          <p class="state" role="status">{t($messages, 'loading')}</p>
        {:else if error}
          <p class="state err" role="alert">{error}</p>
        {:else if !entries.length}
          <div class="state empty">
            <p>Nothing listed yet.</p>
            <p>
              Know a project that belongs here?
              <a
                href="/registry/submit"
                onclick={(e) => {
                  e.preventDefault()
                  navigate('/registry/submit')
                }}
              >
                Submit it
              </a>
              and a human will review it, usually within two days.
            </p>
          </div>
        {:else if !filtered.length}
          <div class="state empty">
            <p>No projects match this filter{query.trim() ? ` and “${query.trim()}”` : ''}.</p>
            {#if activeTag || activeCategory || query.trim()}
              <button
                type="button"
                class="linkish"
                onclick={() => {
                  query = ''
                  navigate('/registry', false, false)
                }}
              >
                Clear all filters
              </button>
            {/if}
          </div>
        {:else}
          <ul class="entries">
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
                  <span class="entry-name">
                    {entry.name}
                    {#if entry.editor_pick}<span class="pick">Editor's pick</span>{/if}
                    {#if entry.reachable === false}<span class="offline">Offline</span>{/if}
                  </span>
                  <span class="entry-desc">{stripMarkdownToText(entry.description)}</span>
                </a>
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    </div>

    <!-- Deliberately empty: this track exists only so the center column's
         width is set by two EQUAL outer tracks, which is what actually
         centers it on the page -- a lone left sidebar (the previous layout)
         pushes the reading column right instead. -->
    <div class="layout-spacer" aria-hidden="true"></div>
  </div>
</div>

<style>
  .registry {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  /* Just the search field, above the reading column -- "Submit a project"
     used to live here too, but the registry build's own top-bar nav
     (AppShell's REGISTRY_NAV_PAGES moreNav entry) already surfaces that
     CTA, so this was a redundant second copy of the same link
     (owner feedback, 2026-09-08). Category selection lives in the
     floating rail, not here either. */
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
  }
  .search {
    flex: 1 1 100%;
  }
  .search input {
    width: 100%;
    min-height: 44px;
    padding: 0 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--surface);
    color: var(--on-surface);
    font-family: var(--font-sans);
    font-size: 15px;
    outline: none;
  }
  .search input:focus {
    border-color: var(--accent);
  }
  .search input::placeholder {
    color: var(--subtle);
  }

  /* Three tracks, the outer two EQUAL width -- that's what centers the
     700px reading column on the page. Categories float in the left
     gutter; the right gutter stays empty on purpose (owner spec,
     2026-09-08: "categories | text (700px) | empty", not a boxed
     two-column sidebar layout). */
  .layout {
    display: grid;
    grid-template-columns: 1fr;
    gap: 20px;
  }
  @media (min-width: 860px) {
    .layout {
      grid-template-columns: minmax(0, 1fr) minmax(0, 700px) minmax(0, 1fr);
      gap: 32px;
      align-items: start;
    }
  }

  .main {
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-width: 0;
  }

  .layout-spacer {
    display: none;
  }
  @media (min-width: 860px) {
    .layout-spacer {
      display: block;
    }
  }

  /* Floating list, not a bordered panel -- narrow, right-aligned toward the
     text column, no background/border of its own. Below 860px there's no
     gutter to float in, so it becomes a horizontal scrolling chip row
     instead of disappearing (2026-09-08 Fable review: a facet that only
     exists on desktop is a bug, not a simplification). */
  .categories-rail ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: row;
    flex-wrap: nowrap;
    gap: 8px;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  .categories-rail ul::-webkit-scrollbar {
    display: none;
  }
  @media (min-width: 860px) {
    .categories-rail {
      justify-self: end;
      width: max-content;
      max-width: 240px;
      position: sticky;
      top: 16px;
    }
    .categories-rail ul {
      flex-direction: column;
      flex-wrap: wrap;
      gap: 2px;
      overflow-x: visible;
    }
  }
  .categories-rail button {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
    flex: 0 0 auto;
    width: 100%;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: none;
    padding: 6px 12px;
    color: var(--muted);
    font: inherit;
    font-size: 13px;
    white-space: nowrap;
    text-align: end;
    cursor: pointer;
  }
  @media (min-width: 860px) {
    .categories-rail button {
      border: 0;
      border-inline-end: 2px solid transparent;
      border-radius: 0;
      padding: 5px 12px 5px 10px;
      font-size: 14px;
      white-space: normal;
    }
  }
  .categories-rail button:hover {
    color: var(--on-surface);
  }
  .categories-rail button.active {
    color: var(--on-surface);
    font-weight: 600;
    border-color: var(--accent);
  }
  @media (min-width: 860px) {
    .categories-rail button.active {
      border-inline-end-color: var(--accent);
      border-block-color: transparent;
    }
  }
  .cat-count {
    color: var(--subtle);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .categories-rail button.active .cat-count {
    color: var(--muted);
  }

  /* Tags live inside the reading column now (chip row, wraps freely) --
     they no longer need a dedicated sidebar since the column itself is
     already narrow enough that a horizontal list reads fine. */
  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .tag-btn {
    display: flex;
    align-items: baseline;
    gap: 6px;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: none;
    padding: 5px 12px;
    color: var(--muted);
    font: inherit;
    font-size: 13px;
    cursor: pointer;
  }
  .tag-btn:hover {
    color: var(--on-surface);
  }
  .tag-btn.active {
    color: var(--on-surface);
    font-weight: 600;
    border-color: var(--accent);
  }
  .tag-count {
    color: var(--subtle);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .tag-btn.active .tag-count {
    color: var(--muted);
  }

  .results {
    display: flex;
    flex-direction: column;
    gap: 16px;
    min-width: 0;
  }

  .results-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border);
  }
  .results-head h2 {
    margin: 0;
    font-size: 17px;
    line-height: 1.3;
  }
  .count {
    color: var(--subtle);
    font-size: 13px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .entries {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .entry {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 14px 0;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .entries li:last-child .entry {
    border-bottom: 0;
  }
  .entry:hover {
    text-decoration: none;
  }
  .entry:hover .entry-name {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .entry-name {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 4px 10px;
    font-family: var(--font-display);
    font-size: 1rem;
    font-weight: 600;
    letter-spacing: -0.1px;
    color: var(--on-surface);
  }
  .pick {
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 500;
    color: var(--primary);
  }
  .offline {
    font-family: var(--font-sans);
    font-size: 12px;
    font-weight: 500;
    color: var(--danger);
  }
  .entry-desc {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    overflow: hidden;
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.5;
    color: var(--muted);
    max-width: 62ch;
  }

  .state {
    margin: 0;
    color: var(--muted);
    font-size: 15px;
  }
  .state.err {
    color: var(--danger);
  }
  .empty {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 20px 0;
    max-width: 44ch;
  }
  .empty p {
    margin: 0;
  }
  .empty a {
    color: var(--accent);
  }
  .linkish {
    align-self: flex-start;
    border: 0;
    background: none;
    padding: 0;
    color: var(--accent);
    font: inherit;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
  }
</style>
