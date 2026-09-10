<script lang="ts">
  import {
    ecosystemApi,
    ecosystemCategoryLabel,
    stripMarkdownToText,
    type EcosystemEntry,
  } from '../lib/api/ecosystem'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { formatDispatchStamp } from '../lib/liveClock'
  import { isHttp, sanitizeArticleHtml } from '../lib/sanitizeHtml'
  import PageMeta from '../components/PageMeta.svelte'
  import { ApiException } from '../lib/api/client'

  /**
   * Minimal markdown render for a submitter-written project description
   * (multi-line, links, lists -- 2026-09-08). Deliberately NOT
   * Markdown.svelte: that component's glossary-popover/chart-render/
   * image-proxy machinery is article-specific and would drag in a lot of
   * unrelated behavior for a short project blurb. The one piece actually
   * shared (not re-copied) is sanitizeArticleHtml -- the same {@html} +
   * DOMPurify allowlist convention every markdown surface in this codebase
   * uses (CLAUDE.md section 5), applied here with no custom renderer.
   */
  async function renderDescription(source: string): Promise<string> {
    if (!source.trim()) return ''
    const { marked } = await import('marked')
    const html = marked.parse(source, { async: false, gfm: true, breaks: false }) as string
    return sanitizeArticleHtml(html)
  }

  let { slug }: { slug: string } = $props()

  let entry: EcosystemEntry | null = $state(null)
  let loading = $state(true)
  let error: 'notfound' | 'other' | null = $state(null)
  let badgeCopied = $state(false)
  let descriptionHtml = $state('')

  $effect(() => {
    const _slug = slug
    let cancelled = false
    void (async () => {
      loading = true
      error = null
      entry = null
      descriptionHtml = ''
      try {
        const result = await ecosystemApi.fetchEntry(_slug)
        if (cancelled) return
        entry = result
        const html = await renderDescription(result.description)
        if (cancelled) return
        descriptionHtml = html
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

  /** Show links as their host, not the full URL — the href carries the rest. */
  function hostOf(url: string): string {
    try {
      return new URL(url).host.replace(/^www\./, '')
    } catch {
      return url
    }
  }

  function go(path: string) {
    return (e: MouseEvent) => {
      e.preventDefault()
      navigate(path)
    }
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
  <PageMeta
    title={entry.name}
    description={stripMarkdownToText(entry.description)}
    path={`/registry/${entry.slug}`}
  />
{:else}
  <PageMeta title="Algorand Open Registry" path={`/registry/${slug}`} />
{/if}

<div class="page detail">
  {#if loading}
    <p class="state" role="status">{t($messages, 'loading')}</p>
  {:else if error === 'notfound'}
    <div class="notfound">
      <h1>Not found</h1>
      <p class="state">This entry doesn't exist, or is still awaiting review.</p>
      <a class="btn" href="/registry" onclick={go('/registry')}>Back to the registry</a>
    </div>
  {:else if error === 'other'}
    <p class="state err" role="alert">{t($messages, 'errorGeneric')}</p>
  {:else if entry}
    <nav class="crumbs" aria-label="Breadcrumb">
      <a href="/registry" onclick={go('/registry')}>Registry</a>
      <span aria-hidden="true">/</span>
      <a
        href={`/registry?category=${encodeURIComponent(entry.category)}`}
        onclick={go(`/registry?category=${encodeURIComponent(entry.category)}`)}
      >
        {ecosystemCategoryLabel(entry.category)}
      </a>
    </nav>

    <header class="head">
      <h1>{entry.name}</h1>
      {#if descriptionHtml}
        <div class="description">{@html descriptionHtml}</div>
      {:else}
        <p class="description">{entry.description}</p>
      {/if}
      <p class="links">
        {#if isHttp(entry.url)}
          <a class="btn btn-outlined" href={entry.url} target="_blank" rel="noopener noreferrer">
            Visit {hostOf(entry.url)}
          </a>
        {/if}
        {#if entry.repo_url && isHttp(entry.repo_url)}
          <a class="btn btn-outlined" href={entry.repo_url} target="_blank" rel="noopener noreferrer">
            Source code
          </a>
        {/if}
      </p>
    </header>

    <dl class="facts">
      <div>
        <dt>Status</dt>
        <dd>
          <!-- Says what was actually measured (a transport-level response),
               not a claim about whether the project is genuinely operating
               -- this check never reads the page (2026-09-08: "Online"
               overclaimed a site that could be parked, suspended, or
               otherwise wound down while still answering HTTP 200). -->
          {#if entry.reachable === true}
            Site responded{#if entry.last_http_status} (HTTP {entry.last_http_status}){/if}{#if entry.last_probed_at_epoch}, checked {stamp(entry.last_probed_at_epoch)}{/if}
          {:else if entry.reachable === false}
            <span class="offline">No response</span>{#if entry.last_probed_at_epoch}, last checked {stamp(entry.last_probed_at_epoch)}{/if}
          {:else}
            Not checked yet
          {/if}
        </dd>
      </div>
      <div>
        <dt>Stage</dt>
        <dd>{entry.stage}{entry.open_source ? ', open source' : ''}</dd>
      </div>
      {#if entry.tags.length}
        <div>
          <dt>Tags</dt>
          <dd>{entry.tags.join(', ')}</dd>
        </div>
      {/if}
      {#if entry.x402_enabled}
        <div>
          <dt>x402</dt>
          <dd>
            <!-- Cross-domain: x402.pxke.me is a separate product, not a route in
                 this build's router — a plain external link, never navigate(). -->
            Sells a paid endpoint.
            <a href="https://x402.pxke.me/" target="_blank" rel="noopener noreferrer">
              See it on the marketplace
            </a>
          </dd>
        </div>
      {/if}
      <div>
        <dt>Listed</dt>
        <dd>
          {entry.source === 'seeded'
            ? 'Seeded from public ecosystem directories'
            : 'Submitted by the project and reviewed by a human'}{entry.reviewed_at_epoch
            ? `, ${stamp(entry.reviewed_at_epoch)}`
            : ''}
          {#if entry.editor_pick}<span class="pick">Editor's pick</span>{/if}
        </dd>
      </div>
    </dl>

    <section class="badge" aria-labelledby="badge-heading">
      <div class="badge-text">
        <h2 id="badge-heading">Link back with a badge</h2>
        <p>Add this to your README. It links here and tells visitors the project is listed.</p>
      </div>
      <img
        src={ecosystemApi.badgeUrl(entry.slug)}
        alt="Listed on PXke Algorand badge"
        width="164"
        height="20"
      />
      <pre class="snippet">{ecosystemApi.badgeMarkdownSnippet(entry.slug, entry.name)}</pre>
      <button class="btn btn-sm" type="button" onclick={copyBadge}>
        {badgeCopied ? 'Copied' : 'Copy markdown'}
      </button>
    </section>

    <p class="correction">
      Something out of date?
      <a
        href={`/registry/${entry.slug}/request`}
        onclick={go(`/registry/${entry.slug}/request`)}
      >
        Suggest a change
      </a>
    </p>
  {/if}
</div>

<style>
  .detail {
    display: flex;
    flex-direction: column;
    gap: 28px;
  }

  .crumbs {
    display: flex;
    gap: 8px;
    font-size: 13px;
    color: var(--subtle);
  }
  .crumbs a {
    color: var(--muted);
    text-decoration: none;
  }
  .crumbs a:hover {
    color: var(--on-surface);
    text-decoration: underline;
    text-underline-offset: 3px;
  }

  .head {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .head h1 {
    margin: 0;
    font-size: clamp(26px, 3.4vw, 34px);
    line-height: 1.12;
    letter-spacing: -0.4px;
  }
  .description {
    margin: 0;
    font-family: var(--font-serif);
    font-size: 1.15rem;
    line-height: 1.6;
    max-width: 58ch;
    overflow-wrap: anywhere;
  }
  /* Rendered markdown (see renderDescription) nests real <p>/<ul>/<a> --
     reset browser default margins and pick up the same link styling used
     elsewhere on the page rather than the .md component's article voice,
     which is overkill for a short project blurb. */
  .description :global(p) {
    margin: 0 0 0.9em;
  }
  .description :global(p:last-child) {
    margin-bottom: 0;
  }
  .description :global(ul),
  .description :global(ol) {
    margin: 0 0 0.9em;
    padding-inline-start: 1.4em;
  }
  .description :global(li) {
    margin-bottom: 0.3em;
  }
  .description :global(a) {
    color: var(--accent);
  }
  .description :global(strong) {
    font-weight: 700;
  }
  .description :global(code) {
    font-family: var(--font-mono);
    font-size: 0.85em;
    padding: 0.1em 0.35em;
    border-radius: 4px;
    background: var(--callout);
  }
  .links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 4px 0 0;
  }

  .facts {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    gap: 10px 24px;
    margin: 0;
    padding-top: 20px;
    border-top: 1px solid var(--border);
    font-size: 15px;
  }
  .facts > div {
    display: contents;
  }
  .facts dt {
    color: var(--subtle);
  }
  .facts dd {
    margin: 0;
    overflow-wrap: anywhere;
  }
  .facts dd a {
    color: var(--accent);
  }
  .offline {
    color: var(--danger);
    font-weight: 600;
  }
  .pick {
    margin-inline-start: 8px;
    color: var(--primary);
    font-size: 13px;
    font-weight: 500;
  }

  .badge {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    padding: 20px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--panel);
  }
  .badge-text {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .badge h2 {
    margin: 0;
    font-size: 16px;
  }
  .badge-text p {
    margin: 0;
    color: var(--muted);
    font-size: 14px;
    max-width: 50ch;
  }
  .snippet {
    margin: 0;
    width: 100%;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    padding: 10px 12px;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .correction {
    margin: 0;
    color: var(--muted);
    font-size: 14px;
  }
  .correction a {
    color: var(--accent);
  }

  .state {
    margin: 0;
    color: var(--muted);
  }
  .state.err {
    color: var(--danger);
  }
  .notfound {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    padding: 20px 0;
  }
  .notfound h1 {
    margin: 0;
  }
</style>
