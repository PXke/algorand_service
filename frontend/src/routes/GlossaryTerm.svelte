<script lang="ts">
  import { glossaryApi, type GlossaryArticleRef, type GlossaryTerm } from '../lib/api/glossary'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { articleHref } from '../lib/paths'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'

  let { slug }: { slug: string } = $props()

  let entry: GlossaryTerm | null = $state(null)
  let loading = $state(true)
  let error: 'notfound' | 'other' | null = $state(null)
  let relatedArticles: GlossaryArticleRef[] = $state([])

  $effect(() => {
    const _slug = slug
    const _lang = $activeLocale
    let cancelled = false
    void (async () => {
      loading = true
      error = null
      entry = null
      relatedArticles = []
      try {
        const result = await glossaryApi.fetchTerm(_slug, _lang)
        if (cancelled) return
        entry = result
      } catch (e) {
        if (cancelled) return
        error = e instanceof ApiException && e.statusCode === 404 ? 'notfound' : 'other'
      } finally {
        if (!cancelled) loading = false
      }
      if (cancelled || error) return
      // Cross-referenced articles are a "see also" extra, not the page's
      // own content -- a failed fetch here leaves the list simply empty
      // rather than blanking the definition that already loaded.
      try {
        const articles = await glossaryApi.fetchArticles(_slug, _lang)
        if (!cancelled) relatedArticles = articles
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  })
</script>

{#if entry}
  <PageMeta title={entry.term} description={entry.definition} path={`/glossary/${entry.slug}`} />
{:else}
  <PageMeta title="Glossary" path={`/glossary/${slug}`} />
{/if}

<div class="page stack">
  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error === 'notfound'}
    <div class="empty">
      <h1>Term not found</h1>
      <p class="muted">This glossary entry doesn't exist or isn't published yet.</p>
      <a
        class="btn"
        href="/glossary"
        onclick={(e) => {
          e.preventDefault()
          navigate('/glossary')
        }}
      >
        Back to Glossary
      </a>
    </div>
  {:else if error === 'other'}
    <p class="err">{t($messages, 'errorGeneric')}</p>
  {:else if entry}
    <nav class="breadcrumb">
      <a
        href="/glossary"
        onclick={(e) => {
          e.preventDefault()
          navigate('/glossary')
        }}
      >
        Glossary
      </a>
      <span aria-hidden="true">›</span>
      <span>{entry.term}</span>
    </nav>
    <header>
      <span class="accent-slug"></span>
      <p class="kicker">Glossary</p>
      <h1>{entry.term}</h1>
    </header>
    <p class="definition">{entry.definition}</p>
    {#if entry.aliases?.length}
      <p class="aliases">
        <span class="aka">Also known as</span>
        {entry.aliases.join(' · ')}
      </p>
    {/if}
    {#if relatedArticles.length}
      <section class="related">
        <p class="kicker">{t($messages, 'articleRelatedTitle')}</p>
        <ul class="related-list">
          {#each relatedArticles as item (item.article_id)}
            <li>
              <a
                class="related-item"
                href={articleHref(item.article_id)}
                onclick={(e) => {
                  e.preventDefault()
                  navigate(articleHref(item.article_id))
                }}
              >
                <strong class="related-title">{item.title}</strong>
                {#if item.summary}<span class="related-summary muted">{item.summary}</span>{/if}
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>

<style>
  .breadcrumb {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    color: var(--muted);
    display: flex;
    gap: 8px;
    align-items: baseline;
  }
  .breadcrumb a {
    color: var(--accent);
    text-decoration: none;
  }
  .breadcrumb a:hover {
    text-decoration: underline;
  }
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(26px, 4vw, 32px);
  }
  .definition {
    font-family: var(--font-serif);
    font-size: 1.12rem;
    line-height: 1.65;
    max-width: 60ch;
    color: var(--md-ink);
  }
  .aliases {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--muted);
    display: flex;
    flex-wrap: wrap;
    gap: 8px 12px;
    align-items: baseline;
  }
  .aka {
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--subtle);
  }
  .empty {
    text-align: start;
    padding: 28px 0;
  }
  .empty h1 {
    margin: 0 0 8px;
  }
  .err {
    color: var(--danger);
  }

  /* Cross-references: same row/hover language as the glossary index's own
     .term-list (Glossary.svelte), just carrying an article title + summary
     instead of a term + definition. */
  .related {
    margin-top: 8px;
    padding-top: 22px;
    border-top: 1px solid var(--border);
  }
  .related-list {
    list-style: none;
    margin: 12px 0 0;
    padding: 0;
  }
  .related-item {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 12px 0;
    border-bottom: 1px solid var(--border);
    color: inherit;
    text-decoration: none;
  }
  .related-item:hover {
    text-decoration: none;
    background: color-mix(in srgb, var(--accent) 6%, transparent);
    margin-inline: -10px;
    padding-inline: 10px;
  }
  .related-item:hover .related-title {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .related-title {
    font-family: var(--font-display);
    font-size: 1rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
  }
  .related-summary {
    font-family: var(--font-serif);
    font-size: 0.92rem;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
</style>
