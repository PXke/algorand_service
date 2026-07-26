<script lang="ts">
  import type { ArticleItem } from '../lib/api/news'
  import { articleLogoUrl, proxiedImageUrl } from '../lib/images'
  import { navigate } from '../lib/router'

  let { article }: { article: ArticleItem } = $props()

  const href = $derived(`/news/articles/${article.article_id}`)
  /** Server already dimension-vets image_url — trust it; favicon only if absent. */
  const media = $derived.by(() => {
    const u = article.image_url?.trim()
    if (u) return { src: proxiedImageUrl(u), logo: false }
    const logo = articleLogoUrl({
      sourceUrl: article.source_url,
      serviceId: article.service_id,
    })
    return logo ? { src: logo, logo: true } : null
  })
  const kicker = $derived(article.tags?.[0] ?? 'Lead')
</script>

<a
  class="lead"
  {href}
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
  {#if media}
    <div class="media" class:logo={media.logo}>
      <img
        src={media.src}
        alt=""
        loading="eager"
        onerror={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')}
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
      grid-template-columns: 1fr minmax(240px, 38%);
      align-items: start;
      gap: 28px;
    }
  }
  .lead:hover {
    text-decoration: none;
  }
  .lead:hover .title {
    color: var(--primary);
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
  }
  .deck {
    margin: 0;
    font-size: 17px;
    line-height: 1.55;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .media {
    aspect-ratio: 16 / 10;
    max-height: 224px;
    border-radius: 12px;
    overflow: hidden;
    background: var(--callout);
  }
  @media (min-width: 640px) {
    .media {
      width: 100%;
      max-width: 340px;
      height: 224px;
      aspect-ratio: auto;
      justify-self: end;
    }
  }
  .media img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: center 30%;
    filter: saturate(0.85);
  }
  .media.logo img {
    object-fit: contain;
    object-position: center;
    padding: 16%;
    filter: none;
    box-sizing: border-box;
  }
</style>
