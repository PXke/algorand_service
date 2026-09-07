<script lang="ts">
  import { ecosystemApi, type EcosystemEntry } from '../lib/api/ecosystem'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { formatDispatchStamp } from '../lib/liveClock'
  import { isHttp } from '../lib/sanitizeHtml'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'

  let { slug }: { slug: string } = $props()

  let entry: EcosystemEntry | null = $state(null)
  let loading = $state(true)
  let error: 'notfound' | 'other' | null = $state(null)
  let badgeCopied = $state(false)

  $effect(() => {
    const _slug = slug
    let cancelled = false
    void (async () => {
      loading = true
      error = null
      entry = null
      try {
        const result = await ecosystemApi.fetchEntry(_slug)
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

  function stamp(epoch: number | undefined | null): string {
    if (!epoch || !Number.isFinite(epoch)) return ''
    return formatDispatchStamp(epoch, $activeLocale)
  }

  async function copyBadge() {
    if (!entry) return
    try {
      await navigator.clipboard.writeText(ecosystemApi.badgeMarkdownSnippet(entry.slug, entry.name))
      badgeCopied = true
      setTimeout(() => (badgeCopied = false), 2000)
    } catch {
      /* clipboard unavailable — the <pre> below is still selectable by hand */
    }
  }
</script>

{#if entry}
  <PageMeta title={entry.name} description={entry.description} path={`/registry/${entry.slug}`} />
{:else}
  <PageMeta title="Algorand Open Registry" path={`/registry/${slug}`} />
{/if}

<div class="page stack">
  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error === 'notfound'}
    <div class="empty">
      <h1>Not found</h1>
      <p class="muted">This registry entry doesn't exist, or is still awaiting review.</p>
      <a
        class="btn"
        href="/registry"
        onclick={(e) => {
          e.preventDefault()
          navigate('/registry')
        }}
      >
        Back to the Registry
      </a>
    </div>
  {:else if error === 'other'}
    <p class="err">{t($messages, 'errorGeneric')}</p>
  {:else if entry}
    <nav class="breadcrumb">
      <a
        href="/registry"
        onclick={(e) => {
          e.preventDefault()
          navigate('/registry')
        }}
      >
        Registry
      </a>
      <span aria-hidden="true">›</span>
      <span>{entry.name}</span>
    </nav>
    <header class="compact-head">
      <p class="kicker">{entry.category}{entry.editor_pick ? ' · Editor’s pick' : ''}</p>
      <h1>{entry.name}</h1>
    </header>
    <p class="description">{entry.description}</p>

    <dl class="meta">
      <div>
        <dt>Website</dt>
        <dd>
          {#if isHttp(entry.url)}
            <a href={entry.url} target="_blank" rel="noopener noreferrer">{entry.url}</a>
          {:else}
            <span>{entry.url}</span>
          {/if}
        </dd>
      </div>
      {#if entry.repo_url}
        <div>
          <dt>Repository</dt>
          <dd>
            {#if isHttp(entry.repo_url)}
              <a href={entry.repo_url} target="_blank" rel="noopener noreferrer">{entry.repo_url}</a>
            {:else}
              <span>{entry.repo_url}</span>
            {/if}
          </dd>
        </div>
      {/if}
      {#if entry.x402_enabled}
        <div>
          <dt>x402</dt>
          <dd>
            <!-- Cross-domain: x402.pxke.me is a separate product/subdomain,
                 not a route in this registry build's own router -- a plain
                 external link, never client-side navigate(). -->
            <a href="https://x402.pxke.me/directory" target="_blank" rel="noopener noreferrer">
              Has a paid x402 endpoint — see the marketplace
            </a>
          </dd>
        </div>
      {/if}
      <div>
        <dt>Stage</dt>
        <dd>{entry.stage}{entry.open_source ? ' · open source' : ''}</dd>
      </div>
      <div>
        <dt>Liveness</dt>
        <dd>
          {#if entry.reachable === true}
            <span class="live on">Reachable</span>{#if entry.last_probed_at_epoch}, last seen online {stamp(entry.last_probed_at_epoch)}{/if}
          {:else if entry.reachable === false}
            <span class="live off">Currently unreachable</span>
          {:else}
            <span class="muted">Not yet checked</span>
          {/if}
        </dd>
      </div>
      {#if entry.tags.length}
        <div>
          <dt>Tags</dt>
          <dd>{entry.tags.join(' · ')}</dd>
        </div>
      {/if}
      <div>
        <dt>Listed</dt>
        <dd>{entry.source === 'seeded' ? 'Seeded from public ecosystem directories' : 'Submitted, human-reviewed'}{entry.reviewed_at_epoch ? `, ${stamp(entry.reviewed_at_epoch)}` : ''}</dd>
      </div>
    </dl>

    <section class="badge-box">
      <p class="kicker">Listed on PXke Algorand</p>
      <img src={ecosystemApi.badgeUrl(entry.slug)} alt="Listed on PXke Algorand badge" width="164" height="20" />
      <p class="muted">Embed this badge in your README to link back here.</p>
      <pre class="snippet">{ecosystemApi.badgeMarkdownSnippet(entry.slug, entry.name)}</pre>
      <button class="btn btn-sm" type="button" onclick={copyBadge}>
        {badgeCopied ? 'Copied!' : 'Copy markdown'}
      </button>
    </section>

    <p class="suggest-change">
      Spot something wrong?
      <a
        href="/registry/submit"
        onclick={(e) => {
          e.preventDefault()
          navigate('/registry/submit')
        }}
      >
        Suggest a change
      </a>
    </p>
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
  .description {
    font-family: var(--font-serif);
    font-size: 1.12rem;
    line-height: 1.65;
    max-width: 60ch;
    color: var(--md-ink);
  }
  .meta {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 8px 20px;
    margin: 0;
    font-size: 0.92rem;
  }
  .meta dt {
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--subtle);
    padding-top: 2px;
  }
  .meta dd {
    margin: 0;
    word-break: break-word;
  }
  .live.on {
    color: var(--gain);
    font-weight: 600;
  }
  .live.off {
    color: var(--danger);
    font-weight: 600;
  }
  .badge-box {
    margin-top: 8px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }
  .snippet {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    padding: 10px 12px;
    font-size: 11px;
    max-width: 100%;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  .suggest-change {
    padding-top: 12px;
    border-top: 1px solid var(--border);
    font-size: 0.88rem;
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
