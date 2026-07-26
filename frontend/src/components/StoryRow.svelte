<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale, localeTag } from '../lib/i18n'
  import { articleLogoUrl, proxiedImageUrl } from '../lib/images'
  import { articleHref } from '../lib/paths'
  import { navigate } from '../lib/router'

  let {
    article,
    dense = false,
    rank = undefined,
    showReads = undefined,
  }: {
    article: ArticleItem
    dense?: boolean
    rank?: number
    showReads?: boolean
  } = $props()

  const href = $derived(articleHref(article.article_id))
  const views = $derived(typeof article.views === 'number' ? article.views : 0)
  const displayReads = $derived(
    showReads ?? ((rank != null || views > 0) && views > 0),
  )
  const media = $derived.by(() => {
    const u = article.image_url?.trim()
    if (u) return { src: proxiedImageUrl(u), logo: false }
    const logo = articleLogoUrl({
      sourceUrl: article.source_url,
      serviceId: article.service_id,
    })
    return logo ? { src: logo, logo: true } : null
  })
  const kicker = $derived.by(() => {
    const kind = article.trigger_kind?.toLowerCase()
    if (kind === 'chain' || kind === 'onchain') return t($messages, 'sourceKindOnChain')
    if (kind === 'scheduled') return t($messages, 'sourceKindScheduled')
    if (article.tags?.[0]) return article.tags[0]
    return t($messages, 'kickerNews')
  })
  const kickerColor = $derived.by(() => {
    const kind = article.trigger_kind?.toLowerCase()
    if (kind === 'chain' || kind === 'onchain') return 'var(--chain)'
    if (kind === 'scheduled') return 'var(--scheduled)'
    return 'var(--primary)'
  })
  const when = $derived.by(() => {
    const epoch = article.published_at_epoch
    if (!epoch) return ''
    const lang = $activeLocale
    const diff = Date.now() / 1000 - epoch
    if (diff < 60) return t($messages, 'timeJustNow')
    if (diff < 3600) return t($messages, 'timeMinutesAgo', { count: Math.floor(diff / 60) })
    if (diff < 86400) return t($messages, 'timeHoursAgo', { count: Math.floor(diff / 3600) })
    if (diff < 86400 * 7) return t($messages, 'timeDaysAgo', { count: Math.floor(diff / 86400) })
    return new Date(epoch * 1000).toLocaleDateString(localeTag(lang), {
      month: 'short',
      day: 'numeric',
    })
  })
</script>

<a
  class="row"
  class:dense
  {href}
  onclick={(e) => {
    e.preventDefault()
    navigate(href)
  }}
>
  {#if rank != null}
    <span class="rank" class:top={rank <= 3}>{rank}</span>
  {/if}
  <div class="text">
    <div class="meta-top">
      <p class="kicker" style="color:{kickerColor}">{kicker}</p>
      {#if when}
        <span class="when subtle">{when}</span>
      {/if}
    </div>
    <h3 class="title">{article.title ?? 'Untitled'}</h3>
    {#if !dense && article.summary}
      <p class="deck muted">{article.summary}</p>
    {/if}
    {#if displayReads}
      <p class="reads muted">{tPlural($messages, 'readsCount', views)}</p>
    {/if}
  </div>
  {#if media}
    <div class="thumb" class:logo={media.logo}>
      <img
        src={media.src}
        alt=""
        width={dense ? 64 : 88}
        height={dense ? 64 : 88}
        loading="lazy"
        decoding="async"
        onerror={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
      />
    </div>
  {/if}
</a>

<style>
  .row {
    display: grid;
    grid-template-columns: auto 1fr auto;
    gap: 14px;
    align-items: start;
    padding: 16px 0;
    color: inherit;
    text-decoration: none;
    border-bottom: 1px solid var(--border);
  }
  .row.dense {
    padding: 14px 0;
  }
  .row:hover {
    text-decoration: none;
  }
  .row:hover .title {
    color: var(--primary);
  }
  .row:hover .thumb img {
    transform: scale(1.06);
    filter: saturate(1);
  }
  .rank {
    width: 34px;
    font-family: var(--font-display);
    font-size: 24px;
    font-weight: 700;
    line-height: 1;
    color: var(--subtle);
    text-align: center;
  }
  .rank.top {
    color: var(--accent);
  }
  .text {
    min-width: 0;
  }
  .meta-top {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 4px;
  }
  .kicker {
    margin: 0;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .when {
    flex-shrink: 0;
    font-size: 10.5px;
  }
  .title {
    margin: 0;
    font-family: var(--font-display);
    font-weight: 700;
    font-size: 18.5px;
    line-height: 1.3;
    letter-spacing: -0.2px;
    transition: color 0.25s ease;
  }
  .dense .title {
    font-size: 17px;
  }
  .deck {
    margin: 6px 0 0;
    font-size: 0.95rem;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .reads {
    margin: 6px 0 0;
    font-size: 0.85rem;
  }
  .thumb {
    width: 88px;
    height: 88px;
    border-radius: var(--radius-thumb);
    overflow: hidden;
    background: var(--callout);
    flex-shrink: 0;
    display: grid;
    place-items: center;
  }
  .dense .thumb {
    width: 64px;
    height: 64px;
  }
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    filter: saturate(0.92);
    transition: transform 0.45s cubic-bezier(0.22, 1, 0.36, 1), filter 0.35s ease;
  }
  /* Favicon/fallback: letterbox instead of blowing up a tiny icon. */
  .thumb.logo img {
    object-fit: contain;
    object-position: center;
    padding: 18%;
    filter: none;
    box-sizing: border-box;
  }
  @media (prefers-reduced-motion: reduce) {
    .title,
    .thumb img {
      transition: none;
    }
    .row:hover .thumb img {
      transform: none;
    }
  }
</style>
