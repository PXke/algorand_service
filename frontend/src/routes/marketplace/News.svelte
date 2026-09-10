<script lang="ts">
  /**
   * /news: the newspaper as JSON. Free routes are called live here (the
   * newest headlines and the top tags) so the page shows the real feed,
   * not only an example.
   */
  import { messages, t, activeLocale } from '../../lib/i18n'
  import { x402Api, X402_PATHS, type X402NewsItem, type X402NewsTags } from '../../lib/api/x402'
  import { x402Stamp } from '../../lib/x402/format'
  import { isHttp } from '../../lib/sanitizeHtml'
  import X402ProductPage from '../../components/x402/X402ProductPage.svelte'

  let items = $state<X402NewsItem[]>([])
  let tags = $state<X402NewsTags | null>(null)
  let loaded = $state(false)
  let failed = $state(false)

  $effect(() => {
    const ac = new AbortController()
    void (async () => {
      try {
        const [headlines, taxonomy] = await Promise.all([
          x402Api.news(5, { signal: ac.signal }),
          x402Api.newsTags(8, { signal: ac.signal }),
        ])
        if (ac.signal.aborted) return
        items = headlines
        tags = taxonomy
        loaded = true
      } catch {
        if (ac.signal.aborted) return
        failed = true
        loaded = true
      }
    })()
    return () => ac.abort()
  })
</script>

<X402ProductPage product="news">
  {#snippet before()}
    <section class="x402-block" aria-labelledby="live-heading">
      <h2 id="live-heading">{t($messages, 'x402NewsLiveHeading')}</h2>
      <p>
        <code>GET {X402_PATHS.news}?limit=5</code>
      </p>
      {#if !loaded}
        <p class="x402-state" role="status">{t($messages, 'loading')}</p>
      {:else if failed}
        <p class="x402-err" role="alert">{t($messages, 'x402NewsLiveError')}</p>
      {:else}
        <ol class="headlines">
          {#each items as item (item.article_id)}
            <li>
              <span class="stamp">{x402Stamp(item.published_at_epoch, $activeLocale)}</span>
              {#if isHttp(item.url)}
                <a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
              {:else}
                <span>{item.title}</span>
              {/if}
            </li>
          {/each}
        </ol>
        {#if tags && tags.tags.length}
          <p class="tags">
            <code>GET {X402_PATHS.newsTags}</code>
            {#each tags.tags as tag (tag.tag)}
              <span class="tag">{tag.tag} <em>{tag.count}</em></span>
            {/each}
            <span class="more">{t($messages, 'x402NewsTagsTotal', { count: tags.tag_count_total, articles: tags.article_count })}</span>
          </p>
        {/if}
      {/if}
    </section>
  {/snippet}
</X402ProductPage>

<style>
  code {
    font-family: var(--font-mono);
    font-size: 12.5px;
    color: var(--muted);
  }
  .headlines {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    border-top: 1px solid var(--border);
  }
  .headlines li {
    display: grid;
    grid-template-columns: 9ch minmax(0, 1fr);
    gap: 14px;
    align-items: baseline;
    padding: 9px 0;
    border-bottom: 1px solid var(--border);
    font-size: 15px;
    line-height: 1.45;
  }
  .headlines a {
    color: var(--on-surface);
    text-decoration: none;
  }
  .headlines a:hover {
    text-decoration: underline;
    text-underline-offset: 3px;
  }
  .stamp {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--subtle);
    white-space: nowrap;
  }
  .tags {
    margin: 4px 0 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 6px 14px;
    font-size: 14px;
  }
  .tags code {
    flex-basis: 100%;
  }
  .tag em {
    font-style: normal;
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--subtle);
  }
  .more {
    color: var(--subtle);
    font-size: 13px;
  }
  @media (max-width: 519px) {
    .headlines li {
      grid-template-columns: 1fr;
      gap: 2px;
    }
  }
</style>
