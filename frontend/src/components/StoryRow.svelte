<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale, localeTag } from '../lib/i18n'
  import { articleImageUrl } from '../lib/images'
  import { articleHref } from '../lib/paths'
  import { navigate } from '../lib/router'
  import { displayTagLabel, primaryTopic, topicColor } from '../lib/tags'

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
    showReads ?? views > 0,
  )
  const media = $derived(articleImageUrl(article))

  /* Track the src that failed rather than hiding the <img>: hiding it left the
     wrapper behind as an empty grey box. Keying on the URL means a new article
     re-arms it without an effect or a reset. */
  let failedSrc = $state<string | null>(null)
  const showMedia = $derived(media != null && media !== failedSrc)
  /* One pass — primaryTopic() allocates a Set and two arrays, and the front
     page renders ~23 of these. */
  const topic = $derived(primaryTopic(article.tags))
  const kicker = $derived.by(() => {
    const kind = article.trigger_kind?.toLowerCase()
    if (kind === 'chain' || kind === 'onchain') return t($messages, 'sourceKindOnChain')
    if (kind === 'scheduled') return t($messages, 'sourceKindScheduled')
    return topic ? displayTagLabel(topic) : t($messages, 'kickerNews')
  })
  /* Provenance kinds keep their own colours; everything else takes the
     topic tone so the feed's kickers become a navigable colour system. */
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
  {href}
  style="--tone:{tone}"
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
      <p class="kicker">{kicker}</p>
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
  {#if showMedia}
    <div class="thumb">
      <img
        src={media}
        alt=""
        width={dense ? 64 : 88}
        height={dense ? 64 : 88}
        loading="lazy"
        decoding="async"
        onerror={() => (failedSrc = media)}
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
  /* Underline on hover, not a colour change: recolouring a headline mid-read
     is jarring, and the tone already labels the desk in the kicker. */
  .row:hover .title {
    text-decoration: underline;
    text-underline-offset: 2px;
    text-decoration-thickness: 1.5px;
  }
  .row:hover .thumb img {
    transform: scale(1.06);
    filter: saturate(1);
  }
  .rank {
    width: 34px;
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 600;
    line-height: 1;
    color: var(--subtle);
    text-align: center;
  }
  @media (max-width: 519px) {
    .rank {
      width: 26px;
      font-size: 18px;
    }
  }
  .rank.top {
    color: var(--tone, var(--accent));
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
  .thumb {
    width: 88px;
    height: 88px;
    border-radius: var(--radius-thumb);
    overflow: hidden;
    background: var(--callout);
    flex-shrink: 0;
  }
  .dense .thumb {
    width: 64px;
    height: 64px;
  }
  /* contain — see LeadStory: cover crops wordmarks and washes out screenshots. */
  .thumb img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    filter: saturate(0.96);
    transition: transform 0.45s cubic-bezier(0.22, 1, 0.36, 1), filter 0.35s ease;
  }
  @media (max-width: 519px) {
    .row {
      gap: 12px;
      padding: 12px 0;
    }
    .row:not(.dense) .title {
      font-size: 16.5px;
    }
    .row:not(.dense) .thumb {
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
    .dense .thumb {
      width: 56px;
      height: 56px;
    }
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
