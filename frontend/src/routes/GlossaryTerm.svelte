<script lang="ts">
  import { glossaryApi, type GlossaryTerm } from '../lib/api/glossary'
  import { messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'

  let { slug }: { slug: string } = $props()

  let entry: GlossaryTerm | null = $state(null)
  let loading = $state(true)
  let error: 'notfound' | 'other' | null = $state(null)

  $effect(() => {
    const _slug = slug
    let cancelled = false
    void (async () => {
      loading = true
      error = null
      entry = null
      try {
        const result = await glossaryApi.fetchTerm(_slug)
        if (cancelled) return
        entry = result
      } catch (e) {
        if (cancelled) return
        error = e instanceof ApiException && e.statusCode === 404 ? 'notfound' : 'other'
      } finally {
        if (!cancelled) loading = false
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
    <nav class="breadcrumb muted">
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
      <h1>{entry.term}</h1>
    </header>
    <p class="definition">{entry.definition}</p>
    {#if entry.aliases?.length}
      <p class="aliases muted">Also known as: {entry.aliases.join(', ')}</p>
    {/if}
  {/if}
</div>

<style>
  .breadcrumb {
    font-size: 0.85rem;
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .breadcrumb a {
    color: inherit;
  }
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(26px, 4vw, 32px);
  }
  .definition {
    font-size: 1.05rem;
    line-height: 1.6;
    max-width: 60ch;
  }
  .aliases {
    font-size: 0.85rem;
  }
  .empty {
    text-align: center;
    padding: 48px 16px;
  }
  .empty h1 {
    margin: 0 0 8px;
  }
  .err {
    color: var(--danger);
  }
</style>
