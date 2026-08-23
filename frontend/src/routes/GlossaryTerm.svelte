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
</style>
