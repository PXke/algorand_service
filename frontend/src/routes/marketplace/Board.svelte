<script lang="ts">
  /** Visibility board (x402.pxke.me/board). Free feed + a pointer to the
   * paid placement route -- no in-browser wallet flow yet for this write
   * (only listing's has one, see lib/x402/pay.ts's own docstring), so this
   * stays a curl-first page like the old board tab did. */
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../../lib/i18n'
  import { config } from '../../lib/config'
  import { ApiException } from '../../lib/api/client'
  import { x402Api, X402_PATHS, type X402Placement } from '../../lib/api/x402'
  import { x402Stamp, x402HostOf } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import PageMeta from '../../components/PageMeta.svelte'

  let placements: X402Placement[] = $state([])
  let loading = $state(true)
  let error = $state<string | null>(null)

  const apiBase = $derived(config.apiBaseUrl.replace(/\/$/, '') || 'https://algorand-api.pxke.me')

  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        placements = await x402Api.board({ signal: ac.signal })
        if (ac.signal.aborted) return
        loading = false
      } catch (e) {
        if (ac.signal.aborted) return
        error = e instanceof ApiException ? e.userMessage : untrack(() => t($messages, 'errorGeneric'))
        loading = false
      }
    })()
    return () => ac.abort()
  })
</script>

<PageMeta
  title="Board"
  description="Pay to place a link, name and pitch on the public visibility board for a fixed term."
  path="/board"
/>

<div class="page stack x402">
  <header class="page-head">
    <span class="accent-slug"></span>
    <p class="kicker">Get found</p>
    <h1>Board</h1>
    <p class="lead muted">
      Pay to place a link, name and pitch on the public visibility board for a fixed term.
    </p>
  </header>

  <section class="how">
    <p class="muted">
      Place your own tile with a real x402 payment (no in-browser wallet flow for this route
      yet):
    </p>
    <pre class="curl"><code
      >curl -X POST {apiBase}{X402_PATHS.board} -H 'Content-Type: application/json' -d '{'{'}"link": "https://agent.example.com", "name": "Example Agent", "pitch": "..."{'}'}'</code
    ></pre>
  </section>

  {#if loading}
    <p class="muted">{t($messages, 'loading')}</p>
  {:else if error}
    <p class="x402-err">{error}</p>
  {:else if placements.length === 0}
    <p class="muted x402-empty">{t($messages, 'x402Empty')}</p>
  {:else}
    <p class="x402-hit-count">{placements.length}<span class="sep" aria-hidden="true">·</span>placed</p>
    <ul class="x402-rows">
      {#each placements as item, i (`${item.link}-${i}`)}
        <li class="x402-row">
          <div class="x402-row-head">
            {#if isHttp(item.link)}
              <a class="x402-row-name" href={item.link} target="_blank" rel="noopener noreferrer nofollow"
                >{item.name || x402HostOf(item.link)}</a
              >
            {:else}
              <span class="x402-row-name">{item.name || item.link}</span>
            {/if}
            {#if typeof item.clicks === 'number'}
              <span class="x402-row-price">{item.clicks} clicks</span>
            {/if}
          </div>
          {#if item.pitch}
            <p class="x402-row-desc">{item.pitch}</p>
          {/if}
          <p class="x402-row-meta">
            <span class="x402-stamp">{x402HostOf(item.link)}</span>
            {#if x402Stamp(item.term_end_epoch, $activeLocale)}
              <span class="x402-stamp">Until {x402Stamp(item.term_end_epoch, $activeLocale)}</span>
            {/if}
          </p>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style>
  header h1 {
    margin: 8px 0 0;
    font-size: clamp(28px, 4vw, 34px);
  }
  .lead {
    margin: 8px 0 0;
    max-width: 46rem;
    font-family: var(--font-serif);
    font-size: 17px;
    line-height: 1.55;
  }
  .how {
    padding: 12px 14px;
    border: 1px solid var(--border);
    border-radius: var(--radius-card);
    background: var(--panel);
  }
  .how p {
    margin: 0 0 8px;
  }
  .curl {
    margin: 0;
    padding: 10px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
    background: var(--accent-soft);
    overflow-x: auto;
    white-space: pre-wrap;
  }
  .curl code {
    font-family: var(--font-mono);
    font-size: 12px;
  }
</style>
