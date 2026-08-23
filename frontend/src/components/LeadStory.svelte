<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { messages, t, tPlural, activeLocale, localeTag } from '../lib/i18n'
  import { articleImageUrl, looksLikeFaviconUrl } from '../lib/images'
  import { articleHref } from '../lib/paths'
  import { navigate } from '../lib/router'
  import { displayTagLabel, primaryTopic, topicColor } from '../lib/tags'

  let { article }: { article: ArticleItem } = $props()

  const href = $derived(articleHref(article.article_id, null, article.slug))
  const media = $derived.by(() => {
    const url = article.image_url?.trim()
    if (!url || looksLikeFaviconUrl(url)) return null
    return articleImageUrl(article)
  })
  let failedSrc = $state<string | null>(null)
  const showMedia = $derived(media != null && media !== failedSrc)
  const topic = $derived(primaryTopic(article.tags))
  const kicker = $derived(topic ? displayTagLabel(topic) : 'Lead')
  const tone = $derived(topicColor(topic))
  const isSpecialEdition = $derived(
    (article.tags ?? []).some((tag) => String(tag).toLowerCase() === 'special-edition'),
  )
  const views = $derived(typeof article.views === 'number' ? article.views : 0)
  const when = $derived.by(() => {
    const epoch = article.published_at_epoch
    if (!epoch) return ''
    const lang = $activeLocale
    const diff = Date.now() / 1000 - epoch
    if (diff < 3600) return t($messages, 'timeMinutesAgo', { count: Math.max(1, Math.floor(diff / 60)) })
    if (diff < 86400) return t($messages, 'timeHoursAgo', { count: Math.floor(diff / 3600) })
    return new Date(epoch * 1000).toLocaleDateString(localeTag(lang), {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  })
</script>

<a
  class="lead"
  class:has-media={showMedia}
  {href}
  style="--tone:{tone}"
  onclick={(e) => {
    e.preventDefault()
    navigate(href)
  }}
>
  <div class="copy">
    <span class="accent-slug"></span>
    <div class="kicker-row">
      <p class="kicker">{kicker}</p>
      {#if isSpecialEdition}
        <span class="special-edition-badge">{t($messages, 'specialEditionBadge')}</span>
      {/if}
    </div>
    <h1 class="title">{article.title ?? 'Untitled'}</h1>
    {#if article.summary}
      <p class="deck muted">{article.summary}</p>
    {/if}
    {#if when || views > 0}
      <p class="wire-stamp">
        {#if when}<span>{when}</span>{/if}
        {#if when && views > 0}<span class="sep" aria-hidden="true">·</span>{/if}
        {#if views > 0}<span>{tPlural($messages, 'readsCount', views)}</span>{/if}
      </p>
    {/if}
  </div>
  {#if showMedia}
    <div class="media">
      <img
        src={media}
        alt={article.title ?? ''}
        width="680"
        height="425"
        loading="eager"
        fetchpriority="high"
        decoding="async"
        onerror={() => (failedSrc = media)}
      />
    </div>
  {/if}
</a>

<style>
  .lead {
    display: flex;
    flex-direction: column;
    gap: 16px;
    color: inherit;
    text-decoration: none;
    padding: 8px 0 4px;
  }
  /* Photo beside the hed — a plate, not a ribbon under a full-measure title.
     Type-only leads keep a reading measure so they don't stretch into a strip. */
  @media (min-width: 860px) {
    .lead.has-media {
      display: grid;
      grid-template-columns: minmax(18rem, 1.05fr) minmax(280px, 0.95fr);
      gap: 24px 36px;
      align-items: start;
    }
    .lead.has-media .media {
      aspect-ratio: 4 / 3;
      max-height: 400px;
      min-height: 260px;
    }
    .lead:not(.has-media) {
      max-width: 42rem;
    }
  }
  .lead:hover {
    text-decoration: none;
  }
  .lead:hover .title {
    text-decoration-color: currentColor;
  }
  .lead:hover .media img {
    transform: scale(1.02);
  }
  .kicker-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .special-edition-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 0;
    background: var(--accent);
    color: var(--surface);
  }
  .copy {
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 0;
  }
  @media (min-width: 640px) {
    .copy {
      padding: 0;
    }
  }
  .title {
    margin: 0;
    font-family: var(--font-display);
    font-weight: 800;
    font-size: var(--fs-lead);
    line-height: 1.08;
    letter-spacing: -1.2px;
    font-stretch: 94%;
    color: var(--on-surface);
    text-decoration: underline;
    text-decoration-color: transparent;
    text-underline-offset: 4px;
    text-decoration-thickness: 1.5px;
    text-wrap: balance;
  }
  .deck {
    font-family: var(--font-serif);
    margin: 0;
    font-size: var(--fs-deck);
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  /* The pipeline's stamp on its own lead — mono, uppercase, quiet. */
  .wire-stamp {
    margin: 2px 0 0;
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-family: var(--font-mono);
    font-size: 10.5px;
    font-weight: 500;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .wire-stamp .sep {
    color: var(--subtle);
  }
  @media (max-width: 639px) {
    .title {
      font-size: clamp(24px, 6.4vw, 30px);
    }
    .deck {
      font-size: 16px;
      -webkit-line-clamp: 3;
      line-clamp: 3;
    }
  }
  .media {
    aspect-ratio: 16 / 9;
    width: 100%;
    max-height: 340px;
    border-radius: var(--radius-thumb);
    overflow: hidden;
    background: var(--thumb-plate);
    border: 1px solid var(--border);
    padding: 10px;
  }
  @media (max-width: 639px) {
    .media {
      max-height: 200px;
      padding: 8px;
    }
  }
  .media img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1);
  }
  @media (prefers-reduced-motion: reduce) {
    .media img {
      transition: none;
    }
    .lead:hover .media img {
      transform: none;
    }
  }
</style>
