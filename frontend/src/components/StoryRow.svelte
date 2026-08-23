<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale, localeTag } from '../lib/i18n'
  import { articleImageUrl, looksLikeFaviconUrl } from '../lib/images'
  import { articleHref } from '../lib/paths'
  import { navigate } from '../lib/router'
  import { displayTagLabel, primaryTopic, topicColor } from '../lib/tags'
  import { staggerMs } from '../lib/motion'

  let {
    article,
    dense = false,
    rank = undefined,
    showReads = undefined,
    showThumb = undefined,
    showWhen = true,
    enterIndex = undefined,
  }: {
    article: ArticleItem
    dense?: boolean
    rank?: number
    showReads?: boolean
    showThumb?: boolean
    showWhen?: boolean
    enterIndex?: number
  } = $props()

  const enterDelay = $derived(enterIndex != null ? `${staggerMs(enterIndex)}ms` : undefined)

  const href = $derived(articleHref(article.article_id, null, article.slug))
  const views = $derived(typeof article.views === 'number' ? article.views : 0)
  const displayReads = $derived(
    showReads ?? views > 0,
  )
  const displayThumb = $derived(showThumb ?? !dense)
  const media = $derived.by(() => {
    const url = article.image_url?.trim()
    if (!url || looksLikeFaviconUrl(url)) return null
    return articleImageUrl(article)
  })

  /* Track the src that failed rather than hiding the <img>: hiding it left the
     wrapper behind as an empty grey box. Keying on the URL means a new article
     re-arms it without an effect or a reset. */
  let failedSrc = $state<string | null>(null)
  const showMedia = $derived(media != null && media !== failedSrc)
  /* One pass — primaryTopic() allocates a Set and two arrays, and the front
     page renders ~23 of these. */
  const topic = $derived(primaryTopic(article.tags))
  const isSpecialEdition = $derived(
    (article.tags ?? []).some((tag) => String(tag).toLowerCase() === 'special-edition'),
  )
  const kicker = $derived.by(() => {
    const kind = article.trigger_kind?.toLowerCase()
    if (kind === 'chain' || kind === 'onchain') return t($messages, 'sourceKindOnChain')
    if (kind === 'scheduled') return t($messages, 'sourceKindScheduled')
    return topic ? displayTagLabel(topic) : t($messages, 'kickerNews')
  })
  /* On-chain keeps the brand indigo; scheduled stays the olive stamp.
     Desks share muted ink — colour on a kicker is reserved for alerts. */
  const tone = $derived.by(() => {
    const kind = article.trigger_kind?.toLowerCase()
    if (kind === 'chain' || kind === 'onchain') return 'var(--chain)'
    if (kind === 'scheduled') return 'var(--scheduled)'
    return topicColor(topic)
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
  class:enter={enterIndex != null}
  {href}
  style:--tone={tone}
  style:--enter-delay={enterDelay}
  onclick={(e) => {
    e.preventDefault()
    navigate(href)
  }}
>
  {#if rank != null}
    <span class="rank" class:top={rank <= 3}>{rank}</span>
  {/if}
  {#if showMedia && displayThumb}
    <div class="thumb">
      <img
        src={media}
        alt={article.title ?? ''}
        width="80"
        height="80"
        loading="lazy"
        decoding="async"
        onerror={() => (failedSrc = media)}
      />
    </div>
  {/if}
  <div class="text">
    <div class="meta-top">
      <p class="kicker">{kicker}</p>
      {#if isSpecialEdition}
        <span class="special-edition-badge">{t($messages, 'specialEditionBadge')}</span>
      {/if}
      {#if showWhen && when}
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
</a>

<style>
  .row {
    position: relative;
    isolation: isolate;
    display: grid;
    grid-template-columns: 1fr;
    gap: 14px;
    align-items: start;
    padding: 18px 0;
    color: inherit;
    text-decoration: none;
    border-bottom: 1px solid var(--border);
  }
  /* Wash sits in a paint-only layer so hover never changes the measure —
     the old padding/margin swap rewrapped titles and shoved the next column. */
  .row::before {
    content: "";
    position: absolute;
    inset: 2px 0;
    border-radius: var(--radius-card);
    background: color-mix(in srgb, var(--accent) 6%, transparent);
    opacity: 0;
    pointer-events: none;
    z-index: -1;
    transition: opacity 0.12s ease;
  }
  .row:has(.rank) {
    grid-template-columns: auto 1fr;
  }
  .row:has(.thumb):not(:has(.rank)) {
    grid-template-columns: auto 1fr;
  }
  .row:has(.rank):has(.thumb) {
    grid-template-columns: auto auto 1fr;
  }
  .row.dense {
    padding: 14px 0;
  }
  .row:hover {
    text-decoration: none;
  }
  .row:hover::before {
    opacity: 1;
  }
  .row:hover .title {
    text-decoration-color: currentColor;
  }
  .row:hover .thumb img {
    transform: scale(1.04);
  }
  .rank {
    width: 34px;
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 600;
    line-height: 1;
    color: var(--subtle);
    text-align: center;
    font-variant-numeric: tabular-nums;
  }
  /* The podium gets the wire's indigo — a chart, not a list. */
  .rank.top {
    color: var(--accent);
  }
  @media (max-width: 519px) {
    .rank {
      width: 26px;
      font-size: 18px;
    }
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
    font-family: var(--font-mono);
    font-size: 10px;
    font-variant-numeric: tabular-nums;
  }
  .special-edition-badge {
    flex-shrink: 0;
    font-family: var(--font-mono);
    font-size: 9.5px;
    font-weight: 700;
    letter-spacing: 0.3px;
    text-transform: uppercase;
    padding: 1.5px 7px;
    border-radius: 2px;
    background: var(--accent);
    color: var(--surface);
  }
  .title {
    margin: 0;
    font-family: var(--font-display);
    font-weight: 700;
    font-size: var(--fs-story);
    line-height: 1.32;
    letter-spacing: -0.2px;
    color: var(--on-surface);
    text-decoration: underline;
    text-decoration-color: transparent;
    text-underline-offset: 3px;
    text-decoration-thickness: 1.5px;
  }
  .dense .title {
    font-size: var(--fs-story-dense);
  }
  .deck {
    font-family: var(--font-serif);
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
  /* Wire-photo plate: art ships on white more often than not, so the tile is
     white in both themes — dark logos stay legible on the night surface. */
  .thumb {
    width: 80px;
    height: 80px;
    border-radius: var(--radius-thumb);
    overflow: hidden;
    background: var(--thumb-plate);
    border: 1px solid var(--border);
    padding: 4px;
    flex-shrink: 0;
  }
  /* contain — see LeadStory: cover crops wordmarks and washes out screenshots. */
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    transition: transform 0.45s cubic-bezier(0.22, 1, 0.36, 1);
  }
  @media (max-width: 519px) {
    .row {
      gap: 12px;
      padding: 12px 0;
    }
    .row:not(.dense) .title {
      font-size: 16.5px;
    }
    .thumb {
      width: 64px;
      height: 64px;
    }
    .row:not(.dense) .deck {
      -webkit-line-clamp: 2;
      line-clamp: 2;
      font-size: 0.9rem;
    }
    .dense .title {
      font-size: 15.5px;
    }
  }
  .dense .thumb {
    width: 64px;
    height: 64px;
  }
  @media (prefers-reduced-motion: no-preference) {
    .row.enter {
      animation: rise-in 0.42s cubic-bezier(0.22, 1, 0.36, 1) both;
      animation-delay: var(--enter-delay, 0ms);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .thumb img,
    .row::before {
      transition: none;
    }
    .row:hover .thumb img {
      transform: none;
    }
  }
</style>
