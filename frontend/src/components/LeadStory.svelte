<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { articleImageUrl } from '../lib/images'
  import { articleHref } from '../lib/paths'
  import { navigate } from '../lib/router'
  import { displayTagLabel, primaryTopic, topicColor } from '../lib/tags'

  let { article }: { article: ArticleItem } = $props()

  const href = $derived(articleHref(article.article_id, null, article.slug))
  const media = $derived(articleImageUrl(article))

  let failedSrc = $state<string | null>(null)
  const showMedia = $derived(media != null && media !== failedSrc)
  const topic = $derived(primaryTopic(article.tags))
  const kicker = $derived(topic ? displayTagLabel(topic) : 'Lead')
  const tone = $derived(topicColor(topic))
</script>

<a
  class="lead"
  {href}
  style="--tone:{tone}"
  onclick={(e) => {
    e.preventDefault()
    navigate(href)
  }}
>
  <div class="copy">
    <span class="accent-slug"></span>
    <p class="kicker">{kicker}</p>
    <h1 class="title">{article.title ?? 'Untitled'}</h1>
    {#if article.summary}
      <p class="deck muted">{article.summary}</p>
    {/if}
  </div>
  {#if showMedia}
    <div class="media">
      <img
        src={media}
        alt=""
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
    display: grid;
    gap: 18px;
    color: inherit;
    text-decoration: none;
    padding-bottom: 8px;
  }
  @media (min-width: 640px) {
    .lead {
      grid-template-columns: 1fr minmax(260px, 43%);
      align-items: start;
      gap: 32px;
    }
  }
  .lead:hover {
    text-decoration: none;
  }
  .lead:hover .title {
    color: var(--tone, var(--primary));
  }
  .lead:hover .media img {
    transform: scale(1.035);
    filter: saturate(1);
  }
  .copy {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .title {
    margin: 0;
    font-family: var(--font-display);
    font-weight: 700;
    font-size: clamp(28px, 4vw, 38px);
    line-height: 1.12;
    letter-spacing: -0.6px;
    transition: color 0.28s ease;
  }
  .deck {
    font-family: var(--font-serif);
    margin: 0;
    font-size: 17px;
    line-height: 1.55;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  @media (max-width: 639px) {
    .title {
      font-size: clamp(24px, 7vw, 30px);
    }
    .deck {
      font-size: 15.5px;
      -webkit-line-clamp: 2;
      line-clamp: 2;
    }
    .media {
      max-height: 180px;
      border-radius: 10px;
    }
  }
  .media {
    aspect-ratio: 16 / 10;
    max-height: 260px;
    border-radius: 12px;
    overflow: hidden;
    background: var(--callout);
  }
  @media (min-width: 640px) {
    .media {
      width: 100%;
      max-width: none;
      height: auto;
      min-height: 200px;
      max-height: 320px;
      aspect-ratio: 16 / 10;
      justify-self: stretch;
    }
  }
  /* `contain`, not `cover`: our art is overwhelmingly wordmarks, logos and
     UI screenshots. Cover clipped "AlgoRank" to "AlgoRan" and magnified
     light screenshots into featureless white. */
  .media img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    object-position: center;
    filter: saturate(0.96);
    transition: transform 0.55s cubic-bezier(0.22, 1, 0.36, 1), filter 0.45s ease;
  }
  @media (prefers-reduced-motion: reduce) {
    .title,
    .media img {
      transition: none;
    }
    .lead:hover .media img {
      transform: none;
    }
  }
</style>
