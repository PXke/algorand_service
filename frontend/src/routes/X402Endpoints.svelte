<script lang="ts">
  import { untrack } from 'svelte'
  import { activeLocale, messages, t } from '../lib/i18n'
  import { navigate } from '../lib/router'
  import { ApiException } from '../lib/api/client'
  import { x402Api, type X402Catalog, type X402NewsItem } from '../lib/api/x402'
  import { ourEndpointProducts } from '../lib/x402/catalog'
  import { formatDispatchStamp } from '../lib/liveClock'
  import PageMeta from '../components/PageMeta.svelte'
  import { SITE_TAGLINE } from '../lib/seo'
  import { isHttp } from '../lib/sanitizeHtml'
  import X402PageNav from '../components/x402/X402PageNav.svelte'
  import X402ProductCatalog from '../components/x402/X402ProductCatalog.svelte'

  let catalog: X402Catalog | null = $state(null)
  let catalogFailed = $state(false)
  let newsItems: X402NewsItem[] = $state([])
  let newsLoading = $state(true)
  let newsError = $state<string | null>(null)

  // Every PXke direct-utility product -- everything in the live catalog that
  // is NOT a marketplace mechanic (directory/board/features/grading/catalog
  // itself). See lib/x402/catalog.ts's exclusion list.
  const products = $derived(ourEndpointProducts(catalog, $messages))

  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        const doc = await x402Api.catalog({ signal: ac.signal })
        if (ac.signal.aborted) return
        catalog = doc
      } catch {
        if (ac.signal.aborted) return
        catalogFailed = true
      }
    })()
    return () => ac.abort()
  })

  // News is the one "Our Endpoints" product with a free browsable feed of
  // its own (every other product here is pure pay-per-call) -- this is the
  // same fetch + list that used to be the Marketplace page's "News" tab,
  // moved here since News is a PXke product, not a marketplace mechanic.
  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        newsItems = await x402Api.news({ signal: ac.signal })
        if (ac.signal.aborted) return
        newsLoading = false
      } catch (e) {
        if (ac.signal.aborted) return
        newsError =
          e instanceof ApiException
            ? e.userMessage
            : untrack(() => t($messages, 'errorGeneric'))
        newsLoading = false
      }
    })()
    return () => ac.abort()
  })

  function stamp(epoch: number | undefined | null): string {
    if (!epoch || !Number.isFinite(epoch)) return ''
    return formatDispatchStamp(epoch, $activeLocale)
  }

  function go(href: string, e: MouseEvent) {
    e.preventDefault()
    navigate(href)
  }
</script>

<PageMeta
  title={t($messages, 'x402EndpointsTitle')}
  description={t($messages, 'x402EndpointsLead') || SITE_TAGLINE}
  path="/x402/endpoints"
/>

<div class="page stack x402">
  <X402PageNav active="endpoints" />

  <header>
    <span class="accent-slug"></span>
    <p class="kicker">{t($messages, 'x402EndpointsKicker')}</p>
    <h1>{t($messages, 'x402EndpointsTitle')}</h1>
    <p class="lead muted">{t($messages, 'x402EndpointsLead')}</p>
  </header>

  <p class="cross-link muted">
    {t($messages, 'x402EndpointsCrossLinkPre')}
    <a href="/x402" onclick={(e) => go('/x402', e)}>{t($messages, 'x402NavMarketplace')}</a>
    {t($messages, 'x402EndpointsCrossLinkPost')}
  </p>

  <section class="pricing">
    <h2>{t($messages, 'x402PricingHeading')}</h2>
    {#if catalogFailed}
      <p class="muted cat-note">{t($messages, 'x402CatalogUnavailable')}</p>
    {:else if !catalog}
      <p class="muted cat-note">{t($messages, 'loading')}</p>
    {:else}
      <X402ProductCatalog {products} {catalog} />
    {/if}
  </section>

  <section class="news">
    <h2>{t($messages, 'x402EndpointsNewsHeading')}</h2>
    {#if newsLoading}
      <p class="muted">{t($messages, 'loading')}</p>
    {:else if newsError}
      <p class="err">{newsError}</p>
    {:else if newsItems.length === 0}
      <p class="muted empty">{t($messages, 'x402Empty')}</p>
    {:else}
      <ul class="rows">
        {#each newsItems as item, i (`${item.article_id}-${i}`)}
          <li class="row">
            <div class="row-head">
              {#if isHttp(item.url)}
                <a class="name" href={item.url} target="_blank" rel="noopener noreferrer nofollow"
                  >{item.title}</a
                >
              {:else}
                <span class="name">{item.title}</span>
              {/if}
            </div>
            {#if item.summary}
              <p class="desc">{item.summary}</p>
            {/if}
            <p class="meta">
              {#each item.tags ?? [] as tg (tg)}
                <span class="badge">#{tg}</span>
              {/each}
              {#if stamp(item.published_at_epoch)}
                <span class="stamp">{stamp(item.published_at_epoch)}</span>
              {/if}
            </p>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
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
  .cross-link {
    margin: 0;
    font-family: var(--font-serif);
    font-size: 0.9rem;
  }
  .cross-link a {
    color: var(--accent);
  }
  .pricing,
  .news {
    margin-top: 8px;
    padding-top: 18px;
    border-top: 1px solid var(--border);
  }
  .pricing h2,
  .news h2 {
    margin: 0;
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--on-surface);
  }
  .pricing h2::before,
  .news h2::before {
    content: '';
    display: inline-block;
    width: 7px;
    height: 7px;
    margin-inline-end: 9px;
    background: var(--accent);
    vertical-align: 6%;
  }
  .cat-note {
    margin: 10px 0 0;
    font-family: var(--font-serif);
    font-size: 0.95rem;
  }
  .empty {
    padding: 24px 0;
    font-family: var(--font-serif);
  }
  .rows {
    list-style: none;
    margin: 10px 0 0;
    padding: 0;
  }
  .row {
    padding: 14px 0;
    border-bottom: 1px solid var(--border);
    min-width: 0;
  }
  .row-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 16px;
    min-width: 0;
  }
  .name {
    font-family: var(--font-display);
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: var(--on-surface);
    text-decoration: none;
    overflow-wrap: anywhere;
    min-width: 0;
  }
  a.name:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .desc {
    margin: 6px 0 0;
    font-family: var(--font-serif);
    font-size: 0.95rem;
    line-height: 1.5;
    color: var(--muted);
    overflow-wrap: anywhere;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 10px;
    margin: 8px 0 0;
  }
  .badge,
  .stamp {
    font-family: var(--font-mono);
    font-size: 10.5px;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    color: var(--muted);
  }
  .badge {
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: var(--radius-control);
  }
  .err {
    color: var(--danger);
  }
</style>
